from copy import deepcopy
from decimal import Decimal
from threading import Lock
from types import SimpleNamespace
from uuid import uuid4

import pytest
from requests.exceptions import Timeout
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import database
from controllers import ContractController
from errors import QIException, error_verification, qi_exception_to_response
from models import Contract, ContractRole, ContractStatus
from models.base import Base


class RegistryMock:
    def __init__(self, session, administrator):
        self.session = session
        self.administrator = administrator
        self.client_response = SimpleNamespace(status=200, json={
            'status': 'active', 'party_key': administrator, 'platform_fee_percentage': '2.5000',
        })
        self.parties = {}
        self.calls = []

    def get_client(self, key):
        assert not self.session.in_transaction(), 'Cadastro must run outside a local transaction'
        self.calls.append(('client', key))
        return self.client_response

    def get_party(self, key):
        assert not self.session.in_transaction(), 'Cadastro must run outside a local transaction'
        self.calls.append(('party', key))
        return self.parties.get(key, SimpleNamespace(status=200, json={'status': 'active'}))


class LedgerMock:
    def __init__(self, session):
        self.session = session
        self.calls = []
        self.operations = {}
        self.response = None
        self.exception = None
        self.lock = Lock()

    def provision_contract_ledger(self, payload):
        assert not self.session.in_transaction(), 'Ledger must run outside SQL transactions'
        self.calls.append(('split', deepcopy(payload)))
        if self.exception:
            raise self.exception
        if self.response:
            return self.response
        key = (payload['contract_key'], payload['split_version'])
        content = {k: v for k, v in payload.items() if k not in ('actor', 'reason')}
        with self.lock:
            replay = key in self.operations
            if replay and self.operations[key] != content:
                return SimpleNamespace(status=409, json={'code': 'QIT001032'})
            self.operations[key] = deepcopy(content)
        return SimpleNamespace(status=200 if replay else 201, json={
            'contract_key': payload['contract_key'], 'split_version': payload['split_version'],
            'expected_amount_in_cents': payload['rent_amount_in_cents'] + sum(i['value'] for i in payload['items'] if i['type'] == 'fixed'),
            'accounts': [{'purpose_code': 'COLLECTION', 'ledger_account_key': 'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee', 'owner_party_key': None, 'owner_role': None}],
        })

    def close_contract_ledger(self, key, payload):
        assert not self.session.in_transaction()
        self.calls.append(('closure', key, deepcopy(payload)))
        if self.exception:
            raise self.exception
        if self.response:
            return self.response
        return SimpleNamespace(status=200, json={'contract_key': key, 'closed_accounts': 3})


@pytest.fixture
def payload():
    parties = [{'party_key': str(uuid4()), 'role': role}
               for role in ('tenant', 'landlord', 'administrator', 'provider')]
    return {
        'client_key': str(uuid4()), 'external_reference': 'ERP-101', 'property_reference': 'APT-101',
        'start_date': '2026-01-01', 'parties': parties, 'rent_amount_in_cents': 125000,
        'split': [
            {**parties[1], 'purpose_code': 'RENT', 'type': 'remainder'},
            {**parties[2], 'purpose_code': 'OWN', 'type': 'percentage', 'value': '10.0000'},
            {**parties[3], 'purpose_code': 'IPTU', 'type': 'fixed', 'value': 1000},
        ],
    }



@pytest.fixture
def controller(monkeypatch, payload):
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
    @event.listens_for(engine, 'connect')
    def foreign_keys(connection, record):
        connection.execute('PRAGMA foreign_keys=ON')
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    with factory.begin() as session:
        session.add_all([ContractStatus(enumerator=s) for s in ('draft', 'active', 'terminated')])
        session.add_all([ContractRole(enumerator=r) for r in ('tenant', 'landlord', 'administrator', 'provider')])
    monkeypatch.setattr(database, 'SessionLocal', factory)
    context = database.open_context()
    controller = ContractController()
    controller.registry = RegistryMock(controller.session, payload['parties'][2]['party_key'])
    controller.ledger = LedgerMock(controller.session)
    yield controller
    context.db_session.close()
    database.clear_context()
    engine.dispose()


def assert_error(code, call):
    with pytest.raises(QIException) as error:
        call()
    assert error.value.code == code
    expected_statuses = {
        'QIT003001': 404, 'QIT003002': 409, 'QIT003003': 409, 'QIT003004': 422,
        'QIT003005': 422, 'QIT003006': 422, 'QIT003007': 409, 'QIT003008': 422,
        'QIT003009': 422, 'QIT003010': 503, 'QIT003011': 422, 'QIT003012': 422,
        'QIT003013': 409, 'QIT003014': 422, 'QIT003015': 409, 'QIT000001': 400,
        'QIT000010': 400, 'QIT000500': 500,
    }
    assert qi_exception_to_response(error.value).status_code == expected_statuses[code]
    return error.value


def create(controller, payload):
    return controller.create(payload, 'test-actor')['contract_key']


def split_body(payload):
    return deepcopy({k: payload[k] for k in ['rent_amount_in_cents', 'split']})


def test_complete_lifecycle(controller, payload):
    result = controller.create(payload, 'test-actor')
    assert result['status'] == 'draft'
    key = result['contract_key']
    draft = controller.get_by_key(key)
    assert draft['split_version'] == 1 and draft['provisioned_at'] is None
    assert draft['status_events'][0]['from_status'] is None
    assert not controller.ledger.calls
    assert not any(c[0] == 'party' for c in controller.registry.calls)
    activation = controller.activate(key, 'activator')
    assert activation['status'] == 'active' and activation['split_version'] == 1
    assert activation['ledger_accounts']
    assert len([c for c in controller.registry.calls if c[0] == 'party']) == 4
    before = (len(controller.registry.calls), len(controller.ledger.calls))
    assert controller.activate(key, 'another-actor') == {'contract_key': key, 'status': 'active', 'split_version': 1}
    assert before == (len(controller.registry.calls), len(controller.ledger.calls))
    data = split_body(payload)
    data['split'][1]['value'] = '12.0000'
    version = controller.create_split_version(key, data, 'editor')
    assert version['split_version'] == 2 and version['_http_status'] == 201
    active = controller.get_by_key(key)
    assert active['split_version'] == 2 and active['provisioned_at'].endswith('Z')
    assert next(i for i in active['split'] if i['purpose_code'] == 'OWN')['value'] == '12.0000'
    snapshot = controller._snapshot(key)
    assert [s['version'] for s in snapshot['split_versions']] == [1, 2]
    assert next(i for i in snapshot['split_versions'][0]['items'] if i['purpose_code'] == 'OWN')['percentage'] == '10.0000'
    assert controller.terminate(key, 'closer')['status'] == 'terminated'
    closed = controller.get_by_key(key)
    assert [e['to_status'] for e in closed['status_events']] == ['draft', 'active', 'terminated']
    assert controller.ledger.calls[-1][1] == key
    assert controller.ledger.calls[-1][2]['actor'] == 'contract-api'
    assert len([c for c in controller.registry.calls if c[0] == 'client']) == 2
    sent = controller.ledger.calls[0][1]
    assert set(sent) == {'contract_key', 'client_key', 'client_party_key', 'split_version', 'rent_amount_in_cents', 'platform_fee_percentage', 'items', 'actor', 'reason'}
    assert sent['client_party_key'] == payload['parties'][2]['party_key']
    assert sent['platform_fee_percentage'] == '2.5000'
    assert all(i['beneficiary_party_key'] for i in sent['items'])


@pytest.mark.parametrize('mutation,code', [
    (lambda p: p['parties'].pop(0), 'QIT003004'),
    (lambda p: p['parties'].append(deepcopy(p['parties'][0])), 'QIT003004'),
    (lambda p: p['parties'][1].update(party_key=p['parties'][0]['party_key']), 'QIT003005'),
    (lambda p: p['parties'][2].update(party_key=str(uuid4())), 'QIT003011'),
    (lambda p: p['split'][0].update(role='tenant'), 'QIT003006'),
    (lambda p: (p['split'][0].update(type='percentage', value='5'), p['split'][1].update(type='remainder'), p['split'][1].pop('value')), 'QIT003006'),
    (lambda p: p['split'][1].update(role='provider'), 'QIT003006'),
    (lambda p: p['split'][1].update(type='fixed', value=1), 'QIT003006'),
    (lambda p: p['split'][2].update(party_key=str(uuid4())), 'QIT003006'),
    (lambda p: p['split'][2].update(type='percentage', value='5'), 'QIT003006'),
    (lambda p: p['split'][2].update(role='tenant', party_key=p['parties'][0]['party_key']), 'QIT003006'),
    (lambda p: p['split'][1].update(value='100'), 'QIT003012'),
    (lambda p: p['split'][1].update(value='100.0001'), 'QIT003012'),
    (lambda p: p['split'][0].update(purpose_code='BAD'), 'QIT000001'),
    (lambda p: p['parties'][0].update(role='platform'), 'QIT000001'),
    (lambda p: p['split'].pop(0), 'QIT000001'),
    (lambda p: p['split'].append(deepcopy(p['split'][0])), 'QIT000001'),
    (lambda p: p['split'].append(deepcopy(p['split'][2])), 'QIT000001'),
    (lambda p: p['split'][2].update(value=-1), 'QIT000001'),
    (lambda p: p['split'][2].update(value=1.0), 'QIT000001'),
    (lambda p: p['split'][2].update(value='1000'), 'QIT000001'),
    (lambda p: p['split'][1].update(value='10.00001'), 'QIT000001'),
    (lambda p: p['split'][1].update(value=10.0), 'QIT000001'),
    (lambda p: p['split'][1].update(value=Decimal('10')), 'QIT000001'),
    (lambda p: p.update(client_key='00000000-0000-0000-0000-000000000001'), 'QIT000001'),
    (lambda p: p.update(start_date='2026-02-30'), 'QIT000001'),
    (lambda p: p.update(end_date='2025-12-31'), 'QIT000010'),
    (lambda p: p.update(split=None), 'QIT000001'),
    (lambda p: p.update(split={'items': [None]}), 'QIT000001'),
    (lambda p: p.update(rent_amount_in_cents=True), 'QIT000001'),
])
def test_creation_rules_have_no_writes(controller, payload, mutation, code):
    mutation(payload)
    assert_error(code, lambda: create(controller, payload))
    assert not controller.ledger.calls
    with controller.session.begin():
        assert controller.session.query(Contract).count() == 0


def test_decimal_boundary(controller, payload):
    payload['split'][1]['value'] = '99.9999'
    key = create(controller, payload)
    controller.activate(key, 'test')
    assert next(i for i in controller.ledger.calls[0][1]['items'] if i['purpose_code'] == 'OWN')['value'] == '99.9999'


def test_optional_provider_and_own(controller, payload):
    payload['parties'].pop()
    payload['split'] = payload['split'][:1]
    controller.registry.client_response.json['platform_fee_percentage'] = '0.0000'
    key = create(controller, payload)
    assert controller.activate(key, 'test')['status'] == 'active'


def test_platform_fee_refusal_is_decided_by_ledger_on_activation(controller, payload):
    payload['split'][1]['value'] = '0.5000'
    key = create(controller, payload)
    assert not controller.ledger.calls
    controller.ledger.response = SimpleNamespace(status=422, json={'code': 'QIT001034'})
    error = assert_error('QIT003009', lambda: controller.activate(key, 'test'))
    assert 'QIT001034' in error.description
    sent = controller.ledger.calls[-1][1]
    assert sent['platform_fee_percentage'] == '2.5000'
    assert next(i for i in sent['items'] if i['purpose_code'] == 'OWN')['value'] == '0.5000'
    draft = controller.get_by_key(key)
    assert draft['status'] == 'draft' and draft['provisioned_at'] is None
    assert len(draft['status_events']) == 1


def test_platform_fee_refusal_leaves_new_version_pending(controller, payload):
    key = create(controller, payload)
    controller.activate(key, 'test')
    data = split_body(payload)
    data['split'][1]['value'] = '0.5000'
    controller.ledger.response = SimpleNamespace(status=422, json={'code': 'QIT001034'})
    error = assert_error('QIT003009', lambda: controller.create_split_version(key, data, 'editor'))
    assert 'QIT001034' in error.description
    sent = controller.ledger.calls[-1][1]
    assert sent['split_version'] == 2
    assert next(i for i in sent['items'] if i['purpose_code'] == 'OWN')['value'] == '0.5000'
    active = controller.get_by_key(key)
    assert active['status'] == 'active' and active['split_version'] == 1
    assert len(active['status_events']) == 2
    snapshot = controller._snapshot(key)
    assert len(snapshot['split_versions']) == 2
    assert snapshot['split_versions'][-1]['provisioned_at'] is None


@pytest.mark.parametrize('invalid_schema', [False, True])
def test_schema_then_client_then_business_rules(controller, payload, invalid_schema):
    payload['parties'] = []
    controller.registry.client_response = SimpleNamespace(status=404, json={'code': 'QIT002005'})
    if invalid_schema:
        payload['start_date'] = 'invalid'
    error = assert_error('QIT000001' if invalid_schema else 'QIT003008', lambda: create(controller, payload))
    assert len(controller.registry.calls) == (0 if invalid_schema else 1)
    if not invalid_schema:
        assert 'QIT002005' in error.description
    assert not controller.ledger.calls
    with controller.session.begin():
        assert controller.session.query(Contract).count() == 0


@pytest.mark.parametrize('purpose', ['CONDO', 'WATER', 'ELECTRICITY'])
def test_provider_purposes(controller, payload, purpose):
    payload['split'][2]['purpose_code'] = purpose
    payload['parties'].append({'party_key': str(uuid4()), 'role': 'provider'})
    create(controller, payload)


def test_registry_checks_each_participation(controller, payload):
    payload['parties'][3]['party_key'] = payload['parties'][0]['party_key']
    payload['split'][2]['party_key'] = payload['parties'][0]['party_key']
    key = create(controller, payload)
    controller.activate(key, 'test')
    assert len([c for c in controller.registry.calls if c[0] == 'party']) == 4


def test_not_found_and_duplicate(controller, payload):
    assert_error('QIT003001', lambda: controller.get_by_key(str(uuid4())))
    create(controller, payload)
    assert_error('QIT003002', lambda: create(controller, payload))


def test_status_transitions(controller, payload):
    key = create(controller, payload)
    assert_error('QIT003003', lambda: controller.create_split_version(key, split_body(payload), 'test'))
    controller.terminate(key, 'test')
    assert controller.get_by_key(key)['split_version'] == 1
    assert controller.get_by_key(key)['provisioned_at'] is None
    assert_error('QIT003003', lambda: controller.activate(key, 'test'))
    assert_error('QIT003003', lambda: controller.terminate(key, 'test'))
    assert_error('QIT003003', lambda: controller.create_split_version(key, split_body(payload), 'test'))


@pytest.mark.parametrize('status,body,code', [
    (404, None, 'QIT003008'), (200, {'status': 'pending'}, 'QIT003007'),
    (503, None, 'QIT003010'), (200, {'status': 'active'}, 'QIT003010'), (401, None, 'QIT000500'),
    (404, {'code': 'QIT002005'}, 'QIT003008'), (503, {'code': 'QIT002999'}, 'QIT003010'),
])
def test_client_failures(controller, payload, status, body, code):
    controller.registry.client_response = SimpleNamespace(status=status, json=body)
    error = assert_error(code, lambda: create(controller, payload))
    if isinstance(body, dict) and body.get('code'):
        assert body['code'] in error.description
    else:
        assert 'Source error code:' not in error.description


@pytest.mark.parametrize('status,body,code', [
    (404, None, 'QIT003008'), (200, {'status': 'pending'}, 'QIT003007'),
    (200, None, 'QIT003010'), (500, None, 'QIT003010'),
    (404, {'code': 'QIT002001'}, 'QIT003008'),
])
def test_party_failures(controller, payload, status, body, code):
    key = create(controller, payload)
    controller.registry.parties[payload['parties'][0]['party_key']] = SimpleNamespace(status=status, json=body)
    error = assert_error(code, lambda: controller.activate(key, 'test'))
    if isinstance(body, dict) and body.get('code'):
        assert body['code'] in error.description
    else:
        assert 'Source error code:' not in error.description
    assert controller.get_by_key(key)['status'] == 'draft'
    assert not controller.ledger.calls


@pytest.mark.parametrize('status,body,code', [
    (422, {'code': c}, 'QIT003009') for c in ['QIT001008','QIT001028','QIT001034']
] + [(409, {'code': c}, 'QIT003013') for c in ['QIT001032','QIT001033']] + [
    (503, None, 'QIT003010'), (201, {}, 'QIT003010'),
    (400, {'code': 'QIT000001'}, 'QIT000500'), (422, {'code': 'UNKNOWN'}, 'QIT000500'),
])
def test_ledger_failures_preserve_draft(controller, payload, status, body, code):
    key = create(controller, payload)
    controller.ledger.response = SimpleNamespace(status=status, json=body)
    error = assert_error(code, lambda: controller.activate(key, 'test'))
    if isinstance(body, dict) and body.get('code') and code != 'QIT000500':
        assert body['code'] in error.description
    else:
        assert 'Source error code:' not in error.description
    draft = controller.get_by_key(key)
    assert draft['status'] == 'draft' and draft['provisioned_at'] is None
    assert len(draft['status_events']) == 1


def test_timeout_result_unknown(controller, payload):
    key = create(controller, payload)
    controller.ledger.exception = Timeout()
    error = assert_error('QIT003010', lambda: controller.activate(key, 'test'))
    assert 'Source error code:' not in error.description
    assert controller.get_by_key(key)['status'] == 'draft'


def test_confirmed_ledger_commit_failure_retry(controller, payload, caplog):
    key = create(controller, payload)
    def fail_commit(session):
        if controller._confirmed_operation:
            raise RuntimeError('simulated commit failure')
    event.listen(controller.session, 'before_commit', fail_commit)
    try:
        with pytest.raises(RuntimeError):
            controller.activate(key, 'test')
    finally:
        event.remove(controller.session, 'before_commit', fail_commit)
    assert 'LEDGER_CONFIRMED_LOCAL_FAILURE' in caplog.text
    assert controller.get_by_key(key)['status'] == 'draft'
    controller.activate(key, 'test')
    assert len(controller.ledger.operations) == 1


def test_pending_split_replay_and_current_version(controller, payload):
    key = create(controller, payload)
    controller.activate(key, 'test')
    controller.ledger.exception = Timeout()
    data = split_body(payload)
    data['rent_amount_in_cents'] += 100
    assert_error('QIT003010', lambda: controller.create_split_version(key, data, 'first-attempt'))
    snapshot = controller._snapshot(key)
    assert len(snapshot['split_versions']) == 2 and snapshot['split_versions'][-1]['provisioned_at'] is None
    assert controller.get_by_key(key)['split_version'] == 1
    different = deepcopy(data)
    different['rent_amount_in_cents'] += 1
    count = len(controller.ledger.calls)
    assert_error('QIT003013', lambda: controller.create_split_version(key, different, 'different'))
    assert count == len(controller.ledger.calls)
    controller.ledger.exception = None
    data['split'].reverse()
    next(i for i in data['split'] if i['purpose_code'] == 'OWN')['value'] = '10'
    controller.registry.client_response.json['platform_fee_percentage'] = '99'
    response = controller.create_split_version(key, data, 'second-attempt', 'another reason')
    assert response['split_version'] == 2 and response['_http_status'] == 200
    assert controller.get_by_key(key)['split_version'] == 2
    assert controller._snapshot(key)['split_versions'][-1]['actor'] == 'first-attempt'
    assert controller.ledger.calls[-1][1]['platform_fee_percentage'] == '2.5000'


@pytest.mark.parametrize('status,body,expected', [
    (200, {}, None), (404, {'code':'QIT001035'}, None),
    (409, {'code':'QIT001037'}, 'QIT003013'),
    (409, {'code':'QIT001011'}, 'QIT003010'), (503, {}, 'QIT003010'),
])
@pytest.mark.parametrize('active', [False, True])
def test_closure_translation(controller, payload, status, body, expected, active):
    key = create(controller, payload)
    if active:
        controller.activate(key, 'test')
    controller.ledger.response = SimpleNamespace(status=status, json=body)
    if expected:
        error = assert_error(expected, lambda: controller.terminate(key, 'test'))
        if body.get('code'):
            assert body['code'] in error.description
        assert controller.get_by_key(key)['status'] == ('active' if active else 'draft')
    else:
        controller.terminate(key, 'test')
        state = controller.get_by_key(key)
        assert state['status'] == 'terminated'
        assert state['status_events'][-1]['from_status'] == ('active' if active else 'draft')


def test_list_roles_and_natural_key(controller, payload):
    payload['parties'][3]['party_key'] = payload['parties'][0]['party_key']
    payload['split'][2]['party_key'] = payload['parties'][0]['party_key']
    key = create(controller, payload)
    filters = {'party_key': payload['parties'][0]['party_key']}
    result = controller.get_list(10, 0, filters)
    assert len(result['contracts']) == 1
    assert result['contracts'][0]['roles'] == ['provider', 'tenant']
    assert controller.get_list(10, 1, filters)['contracts'] == []
    result = controller.get_list(10, 0, {'client_key':payload['client_key'], 'external_reference':payload['external_reference']})
    assert [c['contract_key'] for c in result['contracts']] == [key]


def test_codes_unique():
    error_verification()


def test_pending_version_after_remote_success_and_timeout(controller,payload):
    key=create(controller,payload)
    controller.activate(key,'test')
    original=controller.ledger.provision_contract_ledger
    def confirmed_then_timeout(data):
        original(data)
        raise Timeout('response lost after remote commit')
    controller.ledger.provision_contract_ledger=confirmed_then_timeout
    data=split_body(payload)
    assert_error('QIT003010',lambda:controller.create_split_version(key,data,'first'))
    assert len(controller.ledger.operations)==2
    assert controller.get_by_key(key)['split_version']==1
    controller.ledger.provision_contract_ledger=original
    result=controller.create_split_version(key,data,'retry')
    assert result['_http_status']==200 and result['split_version']==2
    assert len(controller.ledger.operations)==2


def test_new_version_confirmed_then_commit_failure(controller,payload,caplog):
    key=create(controller,payload)
    controller.activate(key,'test')
    def fail_commit(session):
        if controller._confirmed_operation:
            raise RuntimeError('local confirmation commit failed')
    event.listen(controller.session,'before_commit',fail_commit)
    try:
        with pytest.raises(RuntimeError):
            controller.create_split_version(key,split_body(payload),'first')
    finally:
        event.remove(controller.session,'before_commit',fail_commit)
    assert controller.get_by_key(key)['split_version']==1
    assert len(controller._snapshot(key)['split_versions'])==2
    assert 'LEDGER_CONFIRMED_LOCAL_FAILURE' in caplog.text
    assert controller.create_split_version(key,split_body(payload),'retry')['_http_status']==200
    assert len(controller.ledger.operations)==2


@pytest.mark.parametrize('fee',['2e-1','2.00001',2.5,True])
def test_invalid_registry_percentage(controller,payload,fee):
    controller.registry.client_response.json['platform_fee_percentage']=fee
    assert_error('QIT003010',lambda:create(controller,payload))


def test_fixed_ledger_expected_amount_is_accepted(controller, payload):
    key = create(controller, payload)
    original = controller.ledger.provision_contract_ledger

    def fixed_amount(data):
        response = original(data)
        response.json['expected_amount_in_cents'] = 640000
        return response

    controller.ledger.provision_contract_ledger = fixed_amount
    assert controller.activate(key, 'test')['status'] == 'active'
    assert controller.create_split_version(key, split_body(payload), 'test')['split_version'] == 2


@pytest.mark.parametrize('mutation', [
    lambda body: body.update(expected_amount_in_cents='640000'),
    lambda body: body.update(expected_amount_in_cents=640000.0),
    lambda body: body.update(expected_amount_in_cents=True),
    lambda body: body.update(contract_key=str(uuid4())),
    lambda body: body.update(split_version='1'),
    lambda body: body.update(split_version=2),
    lambda body: body.update(accounts=None),
    lambda body: body['accounts'][0].update(ledger_account_key='e0000000-0000-5000-8000-000000000001'),
    lambda body: body['accounts'][0].update(owner_role='invalid-role'),
])
def test_invalid_ledger_response_still_preserves_draft(controller, payload, mutation):
    key = create(controller, payload)
    original = controller.ledger.provision_contract_ledger

    def invalid_response(data):
        response = original(data)
        mutation(response.json)
        return response

    controller.ledger.provision_contract_ledger = invalid_response
    assert_error('QIT003010', lambda: controller.activate(key, 'test'))
    draft = controller.get_by_key(key)
    assert draft['status'] == 'draft' and draft['provisioned_at'] is None
    assert len(draft['status_events']) == 1
