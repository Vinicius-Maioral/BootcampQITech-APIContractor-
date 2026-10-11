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

    def provision_contract_ledger(self, payload, operation_key):
        assert self.session.in_transaction()
        assert not self.session.new and not self.session.dirty, 'Ledger must precede local writes'
        self.calls.append(('split', deepcopy(payload), operation_key))
        if self.exception:
            raise self.exception
        if self.response:
            return self.response
        with self.lock:
            if operation_key in self.operations and self.operations[operation_key] != payload:
                return SimpleNamespace(status=409, json={'code': 'LEDGER_CONFLICT'})
            self.operations[operation_key] = deepcopy(payload)
        return SimpleNamespace(status=201, json={'contract_ledger_key': 'ledger-' + payload['contract_key']})

    def close_contract_ledger(self, key, operation_key):
        assert self.session.in_transaction()
        assert not self.session.new and not self.session.dirty
        self.calls.append(('closure', key, operation_key))
        if self.response:
            return self.response
        return SimpleNamespace(status=204, json=None)


@pytest.fixture
def payload():
    parties = [{'party_key': str(uuid4()), 'role': role}
               for role in ('tenant', 'landlord', 'administrator', 'provider')]
    return {
        'client_key': str(uuid4()), 'external_reference': 'ERP-101', 'property_reference': 'APT-101',
        'start_date': '2026-01-01', 'parties': parties,
        'split': {'rent_amount_in_cents': 125000, 'items': [
            {**parties[1], 'purpose_code': 'RENT', 'allocation_type': 'remainder'},
            {**parties[2], 'purpose_code': 'OWN', 'allocation_type': 'percentage', 'percentage': '10.0000'},
            {**parties[3], 'purpose_code': 'IPTU', 'allocation_type': 'fixed', 'amount_in_cents': 1000},
        ]},
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


def test_complete_lifecycle(controller, payload):
    key = create(controller, payload)
    draft = controller.get_by_key(key)
    assert draft['status'] == 'draft'
    assert draft['split_versions'][0]['provisioned_at'] is None
    assert draft['status_events'][0]['from_status'] is None
    assert controller.ledger.calls == []
    assert controller.activate(key, 'activator')['status'] == 'active'
    assert len([c for c in controller.registry.calls if c[0] == 'party']) == 4
    split = deepcopy(payload['split'])
    split['items'][1]['percentage'] = '12.0000'
    assert controller.create_split_version(key, split, 'editor')['version'] == 2
    active = controller.get_by_key(key)
    assert [s['version'] for s in active['split_versions']] == [1, 2]
    assert active['split_versions'][0]['items'][1]['percentage'] == '10.0000'
    assert all(s['provisioned_at'] for s in active['split_versions'])
    assert controller.terminate(key, 'closer')['status'] == 'terminated'
    closed = controller.get_by_key(key)
    assert [e['to_status'] for e in closed['status_events']] == ['draft', 'active', 'terminated']
    assert closed['end_date'] is None
    assert controller.ledger.calls[-1][1] == 'ledger-' + key
    assert len([c for c in controller.registry.calls if c[0] == 'client']) == 2
    assert controller.ledger.calls[0][1]['platform_fee_percentage'] == '2.5000'
    assert controller.get_list(1, 0, {})['is_last_page']


@pytest.mark.parametrize('mutation,code', [
    (lambda p: p['parties'].pop(0), 'QIT003004'),
    (lambda p: p['parties'].append(deepcopy(p['parties'][0])), 'QIT003004'),
    (lambda p: p['parties'][1].update(party_key=p['parties'][0]['party_key']), 'QIT003005'),
    (lambda p: p['parties'][2].update(party_key=str(uuid4())), 'QIT003011'),
    (lambda p: p['split']['items'][0].update(role='tenant'), 'QIT003006'),
    (lambda p: (p['split']['items'][0].update(allocation_type='percentage', percentage='5'),
                p['split']['items'][1].update(allocation_type='remainder'),
                p['split']['items'][1].pop('percentage')), 'QIT003006'),
    (lambda p: p['split']['items'][1].update(role='provider'), 'QIT003006'),
    (lambda p: (p['split']['items'][1].update(allocation_type='fixed', amount_in_cents=1),
                p['split']['items'][1].pop('percentage')), 'QIT003006'),
    (lambda p: p['split']['items'][2].update(party_key=str(uuid4())), 'QIT003006'),
    (lambda p: (p['split']['items'][2].update(allocation_type='percentage', percentage='5'),
                p['split']['items'][2].pop('amount_in_cents')), 'QIT003006'),
    (lambda p: p['split']['items'][2].update(role='tenant', party_key=p['parties'][0]['party_key']), 'QIT003006'),
    (lambda p: p['split']['items'][1].update(percentage='100'), 'QIT003012'),
    (lambda p: p['split']['items'][1].update(percentage='100.0001'), 'QIT003012'),
    (lambda p: p['split']['items'][0].update(purpose_code='BAD'), 'QIT000001'),
    (lambda p: p['parties'][0].update(role='platform'), 'QIT000001'),
    (lambda p: p['split']['items'].pop(0), 'QIT000001'),
    (lambda p: p['split']['items'].append(deepcopy(p['split']['items'][0])), 'QIT000001'),
    (lambda p: p['split']['items'].append(deepcopy(p['split']['items'][2])), 'QIT000001'),
    (lambda p: p['split']['items'][2].update(amount_in_cents=-1), 'QIT000001'),
    (lambda p: p['split']['items'][1].update(percentage='10.00001'), 'QIT000001'),
    (lambda p: p.update(start_date='2026-02-30'), 'QIT000001'),
    (lambda p: p.update(end_date='2025-12-31'), 'QIT000010'),
    (lambda p: p.update(split=None), 'QIT000001'),
    (lambda p: p.update(split={'items': [None]}), 'QIT000001'),
])
def test_creation_rules_have_no_writes(controller, payload, mutation, code):
    mutation(payload)
    assert_error(code, lambda: create(controller, payload))
    assert controller.ledger.calls == []
    with controller.session.begin():
        assert controller.session.query(Contract).count() == 0


def test_decimal_boundary(controller, payload):
    payload['split']['items'][1]['percentage'] = Decimal('99.9999')
    key = create(controller, payload)
    controller.activate(key, 'test')
    assert controller.ledger.calls[0][1]['split']['items'][1]['percentage'] == '99.9999'


def test_optional_provider_and_own(controller, payload):
    payload['parties'].pop()
    payload['split']['items'] = payload['split']['items'][:1]
    create(controller, payload)


@pytest.mark.parametrize('purpose', ['CONDO', 'WATER', 'ELECTRICITY'])
def test_provider_purposes(controller, payload, purpose):
    payload['split']['items'][2]['purpose_code'] = purpose
    payload['parties'].append({'party_key': str(uuid4()), 'role': 'provider'})
    create(controller, payload)


def test_registry_queries_each_distinct_party_once(controller, payload):
    payload['parties'][3]['party_key'] = payload['parties'][0]['party_key']
    payload['split']['items'][2]['party_key'] = payload['parties'][0]['party_key']
    key = create(controller, payload)
    controller.activate(key, 'test')
    calls = [key for kind, key in controller.registry.calls if kind == 'party']
    assert len(calls) == len(set(calls)) == 3


def test_not_found_and_duplicate(controller, payload):
    assert_error('QIT003001', lambda: controller.get_by_key(str(uuid4())))
    create(controller, payload)
    assert_error('QIT003002', lambda: create(controller, payload))


def test_status_transitions(controller, payload):
    key = create(controller, payload)
    assert_error('QIT003003', lambda: controller.terminate(key, 'test'))
    assert_error('QIT003003', lambda: controller.create_split_version(key, payload['split'], 'test'))
    controller.activate(key, 'test')
    assert_error('QIT003003', lambda: controller.activate(key, 'test'))
    controller.terminate(key, 'test')
    assert_error('QIT003003', lambda: controller.create_split_version(key, payload['split'], 'test'))
    assert_error('QIT003003', lambda: controller.terminate(key, 'test'))


@pytest.mark.parametrize('status,body,code', [
    (404, None, 'QIT003014'),
    (200, {'status': 'inactive'}, 'QIT003015'),
    (503, None, 'QIT003010'),
    (200, {'status': 'active'}, 'QIT003010'),
    (401, None, 'QIT000500'),
])
def test_client_failures(controller, payload, status, body, code):
    controller.registry.client_response = SimpleNamespace(status=status, json=body)
    assert_error(code, lambda: create(controller, payload))


@pytest.mark.parametrize('status,body,code', [
    (404, None, 'QIT003008'),
    (200, {'status': 'inactive'}, 'QIT003007'),
    (200, None, 'QIT003010'),
    (500, None, 'QIT003010'),
])
def test_party_failures(controller, payload, status, body, code):
    key = create(controller, payload)
    controller.registry.parties[payload['parties'][0]['party_key']] = SimpleNamespace(status=status, json=body)
    assert_error(code, lambda: controller.activate(key, 'test'))
    assert controller.get_by_key(key)['status'] == 'draft'
    assert not controller.ledger.calls


@pytest.mark.parametrize('status,body,code', [
    (422, {'code': 'LEDGER_REFUSED_SPLIT'}, 'QIT003009'),
    (409, {'code': 'LEDGER_CONFLICT'}, 'QIT003013'),
    (503, None, 'QIT003010'),
    (201, {}, 'QIT003010'),
    (422, {'code': 'UNKNOWN'}, 'QIT000500'),
])
def test_ledger_failures_rollback(controller, payload, status, body, code):
    key = create(controller, payload)
    controller.ledger.response = SimpleNamespace(status=status, json=body)
    assert_error(code, lambda: controller.activate(key, 'test'))
    draft = controller.get_by_key(key)
    assert draft['status'] == 'draft'
    assert len(draft['status_events']) == 1
    assert draft['split_versions'][0]['provisioned_at'] is None


def test_timeout_result_unknown(controller, payload):
    key = create(controller, payload)
    controller.ledger.exception = Timeout()
    assert_error('QIT003010', lambda: controller.activate(key, 'test'))
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
    assert controller.ledger.calls[0][1:] == controller.ledger.calls[1][1:]
    assert len(controller.ledger.operations) == 1


def test_new_version_and_closure_failures_preserve_state(controller, payload):
    key = create(controller, payload)
    controller.activate(key, 'test')
    controller.ledger.response = SimpleNamespace(status=409, json={'code': 'LEDGER_CONFLICT'})
    assert_error('QIT003013', lambda: controller.create_split_version(key, payload['split'], 'test'))
    assert_error('QIT003013', lambda: controller.terminate(key, 'test'))
    state = controller.get_by_key(key)
    assert state['status'] == 'active'
    assert len(state['split_versions']) == 1
    assert len(state['status_events']) == 2


def test_codes_unique():
    error_verification()
