"""RFC v2 cases through the real API and the official WireMock mappings (CON-25)."""
from copy import deepcopy
import json
from pathlib import Path
from uuid import uuid4

import pytest
import requests

from tests.utils.wiremock import WireMock


LEDGER_MAPPINGS = Path(__file__).resolve().parents[1] / 'mocks/ledger-api/mappings'
ADMINISTRATOR = '11111111-1111-4111-8111-111111111111'


def post(live, path, body):
    base, headers, _, _, _ = live
    return requests.post(base + path, json=body, headers=headers, timeout=10)


def get(live, path, params=None):
    base, headers, _, _, _ = live
    return requests.get(base + path, params=params, headers=headers, timeout=10)


def create(live, payload):
    response = post(live, '/contracts', payload)
    assert response.status_code == 201, response.text
    return response.json()['contract_key']


def assert_error(response, status, code):
    assert response.status_code == status, response.text
    body = response.json()
    assert body['code'] == code, body
    assert set(body) == {'title', 'description', 'translation', 'code'}


def lost_accounts(contract_key):
    """Accounts in the official 408200 response that the 1 s timeout discards."""
    mapping = json.loads((LEDGER_MAPPINGS / 'contract-ledgers-408200-1-atrasa.json').read_text(encoding='utf-8-sig'))
    body = mapping['response']['body'].replace("{{jsonPath request.body '$.contract_key'}}", contract_key)
    return json.loads(body.replace("{{jsonPath request.body '$.split_version'}}", '1'))['accounts']


def test_CU_K8_replay_after_timeout_returns_the_same_account_keys(live):
    payload = deepcopy(live[2])
    payload['rent_amount_in_cents'] = 408200
    key = create(live, payload)
    assert_error(post(live, f'/contracts/{key}/activation', {'actor': 'erp-alfa'}), 503, 'QIT003010')
    replay = post(live, f'/contracts/{key}/activation', {'actor': 'erp-alfa'})
    assert replay.status_code == 200, replay.text
    assert replay.json()['status'] == 'active'
    keys = {a['purpose_code']: a['ledger_account_key'] for a in replay.json()['ledger_accounts']}
    assert keys == {a['purpose_code']: a['ledger_account_key'] for a in lost_accounts(key)}
    calls = live[4]['ledger'].journal()
    assert len(calls) == 2 and all(c['url'] == '/contract_ledgers' for c in calls)
    # The replay re-sends the same version and content: the Ledger recognizes it and keeps the keys.
    bodies = [WireMock.body(c) for c in calls]
    assert bodies[0] == bodies[1] and bodies[0]['contract_key'] == key and bodies[0]['split_version'] == 1
    again = post(live, f'/contracts/{key}/activation', {'actor': 'erp-alfa'})
    assert again.status_code == 200 and again.json()['split_version'] == 1
    assert len(live[4]['ledger'].journal()) == 2


def test_list_by_party_key_returns_the_party_roles(live):
    payload = deepcopy(live[2])
    party, landlord = str(uuid4()), str(uuid4())
    # Fresh parties isolate the listing; registry checks on parties only run at activation.
    payload['parties'][0]['party_key'] = party
    payload['parties'][3]['party_key'] = party
    payload['parties'][1]['party_key'] = landlord
    next(i for i in payload['split'] if i['purpose_code'] == 'IPTU')['party_key'] = party
    next(i for i in payload['split'] if i['purpose_code'] == 'RENT')['party_key'] = landlord
    key = create(live, payload)
    response = get(live, '/contracts', {'party_key': party})
    assert response.status_code == 200, response.text
    body = response.json()
    assert {k: v for k, v in body.items() if k != 'data'} == {'limit': 10, 'page': 0, 'is_last_page': True}
    assert len(body['data']) == 1
    item = body['data'][0]
    assert item['contract_key'] == key and item['status'] == 'draft'
    assert item['roles'] == ['provider', 'tenant']
    by_landlord = get(live, '/contracts', {'party_key': landlord}).json()['data']
    assert [(c['contract_key'], c['roles']) for c in by_landlord] == [(key, ['landlord'])]
    assert get(live, '/contracts', {'party_key': str(uuid4())}).json()['data'] == []


@pytest.mark.parametrize('path', ['activation', 'termination', 'split_versions'])
def test_actions_on_unknown_contract_are_404_QIT003001(live, path):
    body = {'actor': 'erp-alfa', 'reason': 'contrato inexistente'}
    if path == 'split_versions':
        body.update(rent_amount_in_cents=530000, split=live[2]['split'])
    assert_error(post(live, f'/contracts/{uuid4()}/{path}', body), 404, 'QIT003001')
    assert live[4]['ledger'].journal() == []


def wrong_role(payload):
    next(i for i in payload['split'] if i['purpose_code'] == 'RENT')['role'] = 'tenant'


def outsider(payload):
    next(i for i in payload['split'] if i['purpose_code'] == 'IPTU')['party_key'] = str(uuid4())


def other_administrator(payload):
    other = str(uuid4())
    next(p for p in payload['parties'] if p['role'] == 'administrator')['party_key'] = other
    next(i for i in payload['split'] if i['purpose_code'] == 'OWN')['party_key'] = other


def full_percentage(payload):
    next(i for i in payload['split'] if i['purpose_code'] == 'OWN')['value'] = '100.0000'


@pytest.mark.parametrize('change,code', [
    (wrong_role, 'QIT003006'), (outsider, 'QIT003006'),
    (other_administrator, 'QIT003011'), (full_percentage, 'QIT003012'),
], ids=['beneficiary_wrong_role', 'beneficiary_outside_contract', 'administrator_is_not_client', 'percentages_100'])
def test_create_business_rules_are_422(live, change, code):
    payload = deepcopy(live[2])
    change(payload)
    response = post(live, '/contracts', payload)
    assert_error(response, 422, code)
    if code == 'QIT003011':
        assert ADMINISTRATOR in response.json()['description']
    lookup = get(live, '/contracts', {'client_key': payload['client_key'], 'external_reference': payload['external_reference']})
    assert lookup.status_code == 200 and lookup.json()['data'] == []
    assert live[4]['ledger'].journal() == []


@pytest.mark.parametrize('change,code', [
    (wrong_role, 'QIT003006'), (outsider, 'QIT003006'), (full_percentage, 'QIT003012'),
], ids=['beneficiary_wrong_role', 'beneficiary_outside_contract', 'percentages_100'])
def test_split_version_business_rules_are_422(live, change, code):
    key = create(live, live[2])
    assert post(live, f'/contracts/{key}/activation', {'actor': 'erp-alfa'}).status_code == 200
    data = deepcopy(live[2])
    change(data)
    calls = len(live[4]['ledger'].journal())
    body = {'actor': 'erp-alfa', 'rent_amount_in_cents': 530000, 'split': data['split']}
    assert_error(post(live, f'/contracts/{key}/split_versions', body), 422, code)
    assert len(live[4]['ledger'].journal()) == calls
    current = get(live, '/contracts/' + key).json()
    assert current['split_version'] == 1 and current['rent_amount_in_cents'] == live[2]['rent_amount_in_cents']
