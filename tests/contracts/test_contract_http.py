import asyncio
import json
from types import SimpleNamespace

import pytest

from starlette.requests import Request

import controllers.contract_controller as controller_module
import database
from app import create_app
from resources.contract import ContractResource
from . import test_contract_controller as unit
from .test_contract_controller import RegistryMock, LedgerMock, split_body

# Fixtures shared with the unit module; assigning them lets pytest find them here.
controller = unit.controller
payload = unit.payload


def request(app, method, path, body=None, token=True, query=b''):
    """Exercise the real ASGI middleware/resources without a network server or httpx."""
    async def run():
        messages = []
        delivered = False

        async def receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {'type': 'http.request', 'body': body or b'', 'more_body': False}
            await asyncio.Event().wait()

        async def send(message):
            messages.append(message)
        headers = [(b'content-type', b'application/json'), (b'x-request-id', b'http-contract-test')]
        if token:
            from constants import INTERNAL_TOKEN
            headers.append((b'internal-token', INTERNAL_TOKEN.encode()))
        scope = {'type': 'http', 'asgi': {'version': '3.0', 'spec_version': '2.3'},
                 'http_version': '1.1', 'method': method, 'scheme': 'http', 'path': path,
                 'raw_path': path.encode(), 'query_string': query, 'headers': headers,
                 'client': ('127.0.0.1', 1234), 'server': ('test', 80), 'root_path': ''}
        await app(scope, receive, send)
        status = next(m['status'] for m in messages if m['type'] == 'http.response.start')
        content = b''.join(m.get('body', b'') for m in messages if m['type'] == 'http.response.body')
        return status, json.loads(content) if content else None
    return asyncio.run(run())


def test_http_lifecycle_and_error_contract(controller, payload, monkeypatch):
    monkeypatch.setattr(controller_module, 'RegistryConnector', lambda: RegistryMock(
        database.get_context().db_session, payload['parties'][2]['party_key']))
    monkeypatch.setattr(controller_module, 'LedgerConnector', lambda: LedgerMock(database.get_context().db_session))
    app = create_app()
    assert request(app, 'POST', '/contracts', b'{}', token=False)[0] == 403
    status, body = request(app, 'POST', '/contracts', b'{}')
    assert (status, body['code']) == (400, 'QIT000001')
    raw = json.dumps({**payload, 'actor': 'http-test'}).encode()
    status, body = request(app, 'POST', '/contracts', raw)
    assert status == 201
    key = body['contract_key']
    status, body = request(app, 'GET', '/contracts/' + key)
    assert status == 200 and body['status'] == 'draft'
    assert body['status_events'][0]['trace_id'] == 'http-contract-test'
    audit = b'{"actor":"http-test"}'
    assert request(app, 'POST', f'/contracts/{key}/activation', audit)[0] == 200
    status, body = request(app, 'POST', f'/contracts/{key}/split_versions',
                           json.dumps({**split_body(payload), 'actor': 'http-test'}).encode())
    assert status == 201 and body['split_version'] == 2 and 'replayed' not in body
    assert request(app, 'POST', f'/contracts/{key}/termination', audit)[0] == 200
    status, body = request(app, 'POST', f'/contracts/{key}/activation', audit)
    assert (status, body['code']) == (409, 'QIT003003')
    status, body = request(app, 'GET', '/contracts', query=('limit=1&party_key=' + payload['parties'][0]['party_key']).encode())
    assert status == 200 and body['data'][0]['contract_key'] == key
    assert (body['limit'], body['page'], body['is_last_page']) == (1, 0, True) and 'contracts' not in body
    assert request(app, 'GET', '/contracts', query=b'limit=0')[0] == 400
    assert request(app, 'GET', '/contracts', query=b'limit=1&limit=2')[0] == 400
    assert request(app, 'GET', '/health_check', token=False)[0] == 204


def test_raw_json_rejected():
    from errors import InvalidSchema
    import pytest
    for raw in (b'null', b'[]', b'{', b'{"percentage":NaN}'):
        async def receive():
            return {'type': 'http.request', 'body': raw}
        req = Request({'type': 'http'}, receive)
        with pytest.raises(InvalidSchema):
            asyncio.run(ContractResource._body(req))


@pytest.mark.parametrize('query', [b'', b'client_key=00000000-0000-4000-8000-000000000001', b'external_reference=ERP', b'party_key=00000000-0000-4000-8000-000000000002&client_key=00000000-0000-4000-8000-000000000001&external_reference=ERP', b'party_key=00000000-0000-4000-8000-000000000002&offset=0', b'party_key=00000000-0000-4000-8000-000000000002&page=-1', b'party_key=00000000-0000-4000-8000-000000000002&limit=101'])
def test_list_invalid_filters(controller, query):
    status, body = request(create_app(), 'GET', '/contracts', query=query)
    assert status == 400 and body['code'] == 'QIT000001'


def test_http_pending_replay_and_active_replay(controller, payload, monkeypatch):
    from types import SimpleNamespace
    failure = {'response': None}

    def ledger_factory():
        ledger = LedgerMock(database.get_context().db_session)
        ledger.response = failure['response']
        return ledger
    monkeypatch.setattr(controller_module, 'RegistryConnector', lambda: RegistryMock(database.get_context().db_session, payload['parties'][2]['party_key']))
    monkeypatch.setattr(controller_module, 'LedgerConnector', ledger_factory)
    app = create_app()
    _, body = request(app, 'POST', '/contracts', json.dumps({**payload, 'actor': 'test'}).encode())
    key = body['contract_key']
    assert request(app, 'POST', f'/contracts/{key}/activation', b'{"actor":"test"}')[0] == 200
    failure['response'] = SimpleNamespace(status=503, json={})
    status, replay = request(app, 'POST', f'/contracts/{key}/activation', b'{"actor":"test"}')
    assert status == 200 and 'ledger_accounts' not in replay
    data = {**split_body(payload), 'actor': 'test'}
    status, body = request(app, 'POST', f'/contracts/{key}/split_versions', json.dumps(data).encode())
    assert status == 503 and body['code'] == 'QIT003010'
    assert request(app, 'GET', f'/contracts/{key}')[1]['split_version'] == 1
    failure['response'] = None
    status, body = request(app, 'POST', f'/contracts/{key}/split_versions', json.dumps(data).encode())
    assert status == 200 and body['split_version'] == 2 and body['ledger_accounts']
    assert '_http_status' not in body and 'replayed' not in body


@pytest.mark.parametrize('roles', [[], ['tenant'], ['landlord', 'administrator'],
                                   ['tenant', 'administrator'], ['tenant', 'landlord']])
def test_http_insufficient_parties_is_business_error(controller, payload, monkeypatch, roles):
    registry_calls = []

    def registry_factory():
        registry = RegistryMock(database.get_context().db_session, controller.registry.administrator)
        registry.calls = registry_calls
        return registry

    monkeypatch.setattr(controller_module, 'RegistryConnector', registry_factory)
    payload['parties'] = [p for p in payload['parties'] if p['role'] in roles]
    status, body = request(create_app(), 'POST', '/contracts',
                           json.dumps({**payload, 'actor': 'test'}).encode())
    assert (status, body['code']) == (422, 'QIT003004')
    assert registry_calls == [('client', payload['client_key'])]


@pytest.mark.parametrize('invalid_schema', [False, True])
def test_http_creation_error_precedence(controller, payload, monkeypatch, invalid_schema):
    registry_calls = []

    def registry_factory():
        registry = RegistryMock(database.get_context().db_session, controller.registry.administrator)
        registry.client_response = SimpleNamespace(status=404, json={'code': 'QIT002005'})
        registry.calls = registry_calls
        return registry

    monkeypatch.setattr(controller_module, 'RegistryConnector', registry_factory)
    payload['parties'] = []
    if invalid_schema:
        payload['start_date'] = 'invalid'
    status, body = request(create_app(), 'POST', '/contracts',
                           json.dumps({**payload, 'actor': 'test'}).encode())
    assert (status, body['code']) == ((400, 'QIT000001') if invalid_schema else (422, 'QIT003008'))
    assert len(registry_calls) == (0 if invalid_schema else 1)
    if not invalid_schema:
        assert 'QIT002005' in body['description']
