"""Byte fidelity and real WireMock HTTP behavior of the published mappings."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from uuid import uuid4

import pytest
import requests


MOCK_ROOT = Path(__file__).resolve().parents[1] / 'mocks'
MANIFEST = json.loads((MOCK_ROOT / 'manifest.json').read_text(encoding='utf-8'))
PARTY_FILES = sorted((MOCK_ROOT / 'party-api/mappings').glob('*.json'))


def load(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def ledger_payload(rent=500000, version=1):
    source = json.loads((MOCK_ROOT.parents[1] / 'examples/contract.json').read_text(encoding='utf-8'))
    return {
        'contract_key': str(uuid4()), 'client_key': source['client_key'],
        'client_party_key': next(p['party_key'] for p in source['parties'] if p['role'] == 'administrator'),
        'rent_amount_in_cents': rent, 'split_version': version,
        'platform_fee_percentage': '1.0000', 'actor': 'contract-api', 'reason': 'wiremock-check',
        'items': [{'purpose_code': i['purpose_code'], 'beneficiary_party_key': i['party_key'],
                   'beneficiary_role': i['role'], 'type': i['type'],
                   **({'value': i['value']} if 'value' in i else {})} for i in source['split']],
    }


@pytest.mark.parametrize('service,count', [('party-api',21), ('ledger-api',9)])
def test_official_mapping_bytes_and_counts(service,count):
    files = sorted((MOCK_ROOT / service / 'mappings').glob('*.json'))
    expected = MANIFEST['services'][service]
    assert len(files) == expected['count'] == count
    assert {p.name for p in files} == set(expected['sha256'])
    assert {p.name: sha256(p.read_bytes()).hexdigest() for p in files} == expected['sha256']
    for path in files:
        mapping = load(path)
        assert 'headers' not in mapping['request']


@pytest.mark.parametrize('path',PARTY_FILES,ids=lambda p:p.stem)
def test_official_party_responses(official_mocks,path):
    mapping = load(path)
    route = mapping['request'].get('urlPath')
    if route is None:
        prefix = '/clients/' if path.name.startswith('clients-') else '/parties/'
        route = prefix + str(uuid4())
    url = official_mocks['party'].base_url + route
    expected = mapping['response']
    response = requests.get(url,timeout=5)
    assert response.status_code == expected['status']
    if 'jsonBody' in expected:
        raw = json.dumps(expected['jsonBody']).replace('{{request.pathSegments.[1]}}',route.split('/')[-1])
        assert response.json() == json.loads(raw)
        assert response.headers['Content-Type'].startswith('application/json')
    else:
        assert response.content == b''


@pytest.mark.parametrize('service,path', [
    ('party','/clients/c503c503-c503-4c50-8c50-c503c503c503'),
    ('party','/parties/cccccccc-cccc-4ccc-8ccc-cccccccccccc'),
])
def test_official_party_delay_exceeds_test_timeout(official_mocks,service,path):
    with pytest.raises(requests.exceptions.Timeout):
        requests.get(official_mocks[service].base_url + path,timeout=1)


def test_wiremock_mapping_counts(official_mocks):
    assert official_mocks['party'].mappings()['meta']['total'] == 21
    assert official_mocks['ledger'].mappings()['meta']['total'] == 9


def test_ledger_success_uses_official_fixed_body_and_echo(official_mocks):
    mock = official_mocks['ledger']
    payload = ledger_payload()
    expected = load(MOCK_ROOT / 'ledger-api/mappings/contract-ledgers-201.json')['response']['body']
    expected = expected.replace("{{jsonPath request.body '$.contract_key'}}",payload['contract_key']).replace("{{jsonPath request.body '$.split_version'}}",str(payload['split_version']))
    first = requests.post(mock.base_url + '/contract_ledgers',json=payload,timeout=5)
    assert first.status_code == 201 and first.json() == json.loads(expected)
    assert first.json()['expected_amount_in_cents'] == 640000
    assert len(first.json()['accounts']) == 8
    # These mappings deliberately do not authenticate or validate a complete body.
    payload['split_version'] = 2
    payload['rent_amount_in_cents'] = 530000
    response = requests.post(mock.base_url + '/contract_ledgers',json=payload,timeout=5)
    assert response.status_code == 201 and response.json()['split_version'] == 2
    assert response.json()['expected_amount_in_cents'] == 640000


@pytest.mark.parametrize('rent,status,code', [(422008,422,'QIT001008'),(409032,409,'QIT001032')])
def test_ledger_magic_errors(official_mocks,rent,status,code):
    response = requests.post(official_mocks['ledger'].base_url + '/contract_ledgers',json=ledger_payload(rent),timeout=5)
    assert response.status_code == status and response.json()['code'] == code
    assert set(response.json()) == {'title','description','translation','code'}


def test_ledger_unavailable_delay_and_empty_body(official_mocks):
    url = official_mocks['ledger'].base_url + '/contract_ledgers'
    with pytest.raises(requests.exceptions.Timeout):
        requests.post(url,json=ledger_payload(503503),timeout=1)
    response = requests.post(url,json=ledger_payload(503503),timeout=5)
    assert response.status_code == 503 and response.content == b''


def test_ledger_timeout_replay_and_scenario_reset(official_mocks):
    mock = official_mocks['ledger']
    payload = ledger_payload(408200)
    url = mock.base_url + '/contract_ledgers'
    with pytest.raises(requests.exceptions.Timeout):
        requests.post(url,json=payload,timeout=1)
    response = requests.post(url,json=payload,timeout=5)
    assert response.status_code == 200
    assert response.json()['contract_key'] == payload['contract_key']
    assert response.json()['split_version'] == 1
    assert response.json()['expected_amount_in_cents'] == 640000
    mock.admin('POST','/scenarios/reset')
    with pytest.raises(requests.exceptions.Timeout):
        requests.post(url,json=payload,timeout=1)


@pytest.mark.parametrize('reason,status,code', [('ordinary closure',200,None),('mock:saldo-retido',409,'QIT001037'),('mock:fora-do-ar',503,None)])
def test_ledger_official_closure(official_mocks,reason,status,code):
    key = str(uuid4())
    url = official_mocks['ledger'].base_url + '/contract_ledgers/' + key + '/closure'
    response = requests.post(url,json={'actor':'contract-api','reason':reason},timeout=5)
    assert response.status_code == status
    if status == 200:
        assert response.json() == {'contract_key':key,'closed_accounts':6}
    elif code:
        assert response.json()['code'] == code
    else:
        assert response.content == b''


def test_ledger_closure_timeout(official_mocks):
    url = official_mocks['ledger'].base_url + '/contract_ledgers/' + str(uuid4()) + '/closure'
    with pytest.raises(requests.exceptions.Timeout):
        requests.post(url,json={'actor':'contract-api','reason':'mock:fora-do-ar'},timeout=1)
