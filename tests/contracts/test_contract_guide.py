"""Literal guia-contract-api §9 and local §12 acceptance cases.

Expected responses are specified here independently of application DTOs.
CU-K9 requires `split_version` by project decision, overriding the guide table's
`version` field. Real-service integration is outside
this suite; all dependency calls use the unchanged official WireMock mappings.
"""
import json
import subprocess
from uuid import uuid4

import pytest
import requests
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from tests.utils.wiremock import WireMock
from .test_contract_postgres import pg_factory


pytestmark = pytest.mark.contract_guide

CLIENT = 'c1c1c1c1-c1c1-4c1c-8c1c-c1c1c1c1c1c1'
ADMINISTRATOR = '11111111-1111-4111-8111-111111111111'
LANDLORD = '22222222-2222-4222-8222-222222222222'
TENANT = '33333333-3333-4333-8333-333333333333'
PROVIDER = '44444444-4444-4444-8444-444444444444'


@pytest.fixture(scope='session', autouse=True)
def guide_prerequisites(docker_environment):
    return docker_environment


@pytest.fixture(scope='session')
def guide_db(guide_prerequisites, contract_database_url):
    engine = create_engine(contract_database_url)
    try:
        with engine.connect() as connection:
            assert connection.execute(text('SELECT 1')).scalar_one() == 1
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def guide_api(live):
    base, headers, _, config, mocks = live
    assert config['services']['api']['environment']['PARTY_API_TIMEOUT'] == '1'
    assert config['services']['api']['environment']['LEDGER_API_TIMEOUT'] == '1'
    # Exact guide example; only the ERP reference varies to isolate test runs.
    payload = {
        'client_key': CLIENT, 'external_reference': 'ALFA-2026-0042-' + uuid4().hex,
        'property_reference': 'APT-101', 'start_date': '2026-09-01',
        'rent_amount_in_cents': 500000,
        'parties': [
            {'party_key': TENANT, 'role': 'tenant'},
            {'party_key': LANDLORD, 'role': 'landlord'},
            {'party_key': ADMINISTRATOR, 'role': 'administrator'},
            {'party_key': PROVIDER, 'role': 'provider'},
        ],
        'split': [
            {'purpose_code': 'RENT', 'party_key': LANDLORD, 'role': 'landlord', 'type': 'remainder'},
            {'purpose_code': 'OWN', 'party_key': ADMINISTRATOR, 'role': 'administrator', 'type': 'percentage', 'value': '10.0000'},
            {'purpose_code': 'IPTU', 'party_key': PROVIDER, 'role': 'provider', 'type': 'fixed', 'value': 30000},
        ],
        'actor': 'erp-alfa',
    }
    return {'base': base, 'headers': headers, 'payload': payload, 'config': config, 'mocks': mocks}


def post(api, path, payload):
    return requests.post(api['base'] + path, json=payload, headers=api['headers'], timeout=10)


def error(response, status, code):
    assert response.status_code == status, response.text
    body = response.json()
    assert body['code'] == code, body
    assert set(body) == {'title', 'description', 'translation', 'code'}
    return body


def create(api):
    response = post(api, '/contracts', api['payload'])
    assert response.status_code == 201, response.text
    body = response.json()
    assert body['status'] == 'draft', body
    return body['contract_key']


def activate(api, key):
    response = post(api, f'/contracts/{key}/activation', {
        'actor': 'erp-alfa', 'reason': 'ativação do contrato ALFA-2026-0042',
    })
    assert response.status_code == 200, response.text
    assert response.json()['status'] == 'active'
    return response


def state(api, key):
    response = requests.get(api['base'] + '/contracts/' + key, headers=api['headers'], timeout=10)
    assert response.status_code == 200, response.text
    return response.json()


def persisted_events(engine, key):
    with engine.connect() as connection:
        return connection.execute(text('''
            SELECT old.enumerator AS from_status, new.enumerator AS to_status, e.trace_id
            FROM contract_status_event e
            JOIN contract c ON c.id=e.contract_id
            LEFT JOIN contract_status old ON old.id=e.from_status_id
            JOIN contract_status new ON new.id=e.to_status_id
            WHERE c.contract_key=:key ORDER BY e.event_datetime, e.id
        '''), {'key': key}).mappings().all()


def test_CU_K1_create_201_draft(guide_api):
    create(guide_api)


def test_CU_K2_repeated_post_409_QIT003002(guide_api):
    create(guide_api)
    error(post(guide_api, '/contracts', guide_api['payload']), 409, 'QIT003002')


def test_CU_K3_no_tenant_422_QIT003004(guide_api):
    guide_api['payload']['parties'] = [p for p in guide_api['payload']['parties'] if p['role'] != 'tenant']
    error(post(guide_api, '/contracts', guide_api['payload']), 422, 'QIT003004')


def test_CU_K4_landlord_is_tenant_422_QIT003005(guide_api):
    next(p for p in guide_api['payload']['parties'] if p['role'] == 'tenant')['party_key'] = LANDLORD
    error(post(guide_api, '/contracts', guide_api['payload']), 422, 'QIT003005')


@pytest.mark.parametrize('client,status,code', [
    ('c404c404-c404-4c40-8c40-c404c404c404', 422, 'QIT003008'),
    ('c503c503-c503-4c50-8c50-c503c503c503', 503, 'QIT003010'),
], ids=['client_not_found', 'client_unavailable'])
def test_client_failure(guide_api, client, status, code):
    guide_api['payload']['client_key'] = client
    error(post(guide_api, '/contracts', guide_api['payload']), status, code)


def test_CU_K5_activate_200_active_event_and_provisioned_at(guide_api, guide_db):
    key = create(guide_api)
    activate(guide_api, key)
    active = state(guide_api, key)
    assert active['status'] == 'active'
    assert active['provisioned_at'] is not None
    events = persisted_events(guide_db, key)
    assert [(e['from_status'], e['to_status']) for e in events] == [(None, 'draft'), ('draft', 'active')]
    with guide_db.connect() as connection:
        provisioned = connection.execute(text('''
            SELECT s.provisioned_at FROM split_version s JOIN contract c ON c.id=s.contract_id
            WHERE c.contract_key=:key AND s.version=1
        '''), {'key': key}).scalar_one()
    assert provisioned is not None


def test_CU_K6_pending_provider_no_ledger_call(guide_api):
    pending = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
    next(p for p in guide_api['payload']['parties'] if p['role'] == 'provider')['party_key'] = pending
    next(i for i in guide_api['payload']['split'] if i['purpose_code'] == 'IPTU')['party_key'] = pending
    key = create(guide_api)
    before = state(guide_api, key)
    error(post(guide_api, f'/contracts/{key}/activation', {'actor': 'erp-alfa'}), 409, 'QIT003007')
    after = state(guide_api, key)
    assert after['status'] == 'draft'
    assert after['status_events'] == before['status_events']
    assert guide_api['mocks']['ledger'].journal() == []


def test_CU_K7_ledger_unavailable_no_new_event(guide_api):
    guide_api['payload']['rent_amount_in_cents'] = 503503
    key = create(guide_api)
    before = state(guide_api, key)
    error(post(guide_api, f'/contracts/{key}/activation', {'actor': 'erp-alfa'}), 503, 'QIT003010')
    after = state(guide_api, key)
    assert after['status'] == 'draft'
    assert after['status_events'] == before['status_events']


def test_CU_K8_timeout_then_200_active(guide_api):
    guide_api['payload']['rent_amount_in_cents'] = 408200
    key = create(guide_api)
    error(post(guide_api, f'/contracts/{key}/activation', {'actor': 'erp-alfa'}), 503, 'QIT003010')
    assert state(guide_api, key)['status'] == 'draft'
    # No reset here: the official scenario must advance to replay.
    activate(guide_api, key)
    assert state(guide_api, key)['status'] == 'active'


def test_ledger_refuses_422_QIT003009_with_QIT001008(guide_api):
    guide_api['payload']['rent_amount_in_cents'] = 422008
    key = create(guide_api)
    body = error(post(guide_api, f'/contracts/{key}/activation', {'actor': 'erp-alfa'}), 422, 'QIT003009')
    assert 'QIT001008' in body['description']


def test_CU_K9_adjust_rent_530000_201_split_version_2(guide_api):
    key = create(guide_api)
    activate(guide_api, key)
    response = post(guide_api, f'/contracts/{key}/split_versions', {
        'actor': 'erp-alfa', 'rent_amount_in_cents': 530000, 'split': guide_api['payload']['split'],
    })
    assert response.status_code == 201, response.text
    body = response.json()
    assert 'split_version' in body, f"CU-K9 requires 'split_version: 2'; observed response: {body}"
    assert type(body['split_version']) is int, body
    assert body['split_version'] == 2, body


def test_CU_K10_balance_prevents_termination(guide_api):
    key = create(guide_api)
    activate(guide_api, key)
    before = state(guide_api, key)
    error(post(guide_api, f'/contracts/{key}/termination', {'actor': 'erp-alfa', 'reason': 'mock:saldo-retido'}), 409, 'QIT003013')
    after = state(guide_api, key)
    assert after['status'] == 'active'
    assert after['status_events'] == before['status_events']


def test_CU_K11_terminate_200_terminated(guide_api):
    key = create(guide_api)
    activate(guide_api, key)
    response = post(guide_api, f'/contracts/{key}/termination', {'actor': 'erp-alfa', 'reason': 'fim da locação ALFA-2026-0042'})
    assert response.status_code == 200, response.text
    assert response.json()['status'] == 'terminated'
    assert state(guide_api, key)['status'] == 'terminated'


def test_CU_K12_exact_trace_id_in_event_and_ledger_header(guide_api, guide_db):
    key = create(guide_api)
    guide_api['headers']['X-Request-ID'] = 'teste-k12'
    activate(guide_api, key)
    activation = [e for e in persisted_events(guide_db, key) if e['to_status'] == 'active']
    assert len(activation) == 1 and activation[0]['trace_id'] == 'teste-k12'
    calls = guide_api['mocks']['ledger'].journal()
    assert len(calls) == 1 and calls[0]['url'] == '/contract_ledgers'
    assert WireMock.header(calls[0], 'X-Request-ID') == 'teste-k12'


def test_update_contract_status_event_raises_trigger_exception(pg_factory):
    # All writes, including the attempted UPDATE, target a disposable PG schema.
    engine = pg_factory.kw['bind']
    with engine.begin() as connection:
        contract_id = connection.execute(text('''
            INSERT INTO contract (contract_key,client_key,contract_status_id,external_reference,property_reference,start_date)
            VALUES (:key,:client,(SELECT id FROM contract_status WHERE enumerator='draft'),'GUIDE-TRIGGER','APT-101','2026-09-01')
            RETURNING id
        '''), {'key': str(uuid4()), 'client': CLIENT}).scalar_one()
        event_id = connection.execute(text('''
            INSERT INTO contract_status_event (contract_id,to_status_id,actor,trace_id,event_datetime)
            VALUES (:contract,(SELECT id FROM contract_status WHERE enumerator='draft'),'erp-alfa','guide-trigger',NOW())
            RETURNING id
        '''), {'contract': contract_id}).scalar_one()
    with pytest.raises(DBAPIError, match='contract_status_event.*append-only'):
        with engine.begin() as connection:
            connection.execute(text("UPDATE contract_status_event SET actor='modified' WHERE id=:id"), {'id': event_id})
    with engine.connect() as connection:
        assert connection.execute(text('SELECT actor FROM contract_status_event WHERE id=:id'), {'id': event_id}).scalar_one() == 'erp-alfa'


def test_section_12_local_environment(guide_api, guide_db):
    config = guide_api['config']['services']
    assert int(config['api']['ports'][0]['published']) == 3002
    assert int(config['db']['ports'][0]['published']) == 5434
    for service in ('api', 'db', 'party-mock', 'ledger-mock'):
        container = subprocess.check_output(['docker', 'compose', 'ps', '-q', service], encoding='utf-8').strip()
        assert container, f'{service} is not running'
        inspected = json.loads(subprocess.check_output(['docker', 'inspect', container], encoding='utf-8'))[0]
        assert inspected['State']['Health']['Status'] == 'healthy', service
    assert requests.get(guide_api['base'] + '/health_check', timeout=10).status_code == 204
    with guide_db.connect() as connection:
        assert connection.execute(text('SELECT 1')).scalar_one() == 1


def test_section_12_provisioning_payload_matches_01b(guide_api):
    key = create(guide_api)
    activate(guide_api, key)
    calls = guide_api['mocks']['ledger'].journal()
    assert len(calls) == 1
    call = calls[0]
    assert call['method'] == 'POST' and call['url'] == '/contract_ledgers'
    assert WireMock.header(call, 'X-Request-ID') == guide_api['headers']['X-Request-ID']
    token = guide_api['config']['services']['api']['environment']['LEDGER_API_INTERNAL_TOKEN']
    assert WireMock.header(call, 'INTERNAL-TOKEN') == token
    assert WireMock.header(call, 'Content-Type') == 'application/json'
    body = WireMock.body(call)
    expected = {
        'contract_key': key, 'client_key': CLIENT, 'client_party_key': ADMINISTRATOR,
        'split_version': 1, 'rent_amount_in_cents': 500000,
        'platform_fee_percentage': '1.0000', 'actor': 'contract-api',
        'reason': 'ativação do contrato ALFA-2026-0042',
        'items': [
            {'purpose_code': 'RENT', 'beneficiary_party_key': LANDLORD, 'beneficiary_role': 'landlord', 'type': 'remainder'},
            {'purpose_code': 'OWN', 'beneficiary_party_key': ADMINISTRATOR, 'beneficiary_role': 'administrator', 'type': 'percentage', 'value': '10.0000'},
            {'purpose_code': 'IPTU', 'beneficiary_party_key': PROVIDER, 'beneficiary_role': 'provider', 'type': 'fixed', 'value': 30000},
        ],
    }
    assert set(body) == set(expected)
    assert type(body['rent_amount_in_cents']) is int
    assert type(body['split_version']) is int
    assert type(next(i for i in body['items'] if i['type'] == 'fixed')['value']) is int
    # 01b semantic content does not depend on item order or serialized bytes.
    body['items'] = sorted(body['items'], key=lambda i: i['purpose_code'])
    expected['items'] = sorted(expected['items'], key=lambda i: i['purpose_code'])
    assert body == expected
    for request in guide_api['mocks']['party'].journal():
        assert request['method'] == 'GET'
        assert not request.get('body')
        assert WireMock.header(request, 'X-Request-ID') == guide_api['headers']['X-Request-ID']
        party_token = guide_api['config']['services']['api']['environment']['PARTY_API_INTERNAL_TOKEN']
        assert WireMock.header(request, 'INTERNAL-TOKEN') == party_token
