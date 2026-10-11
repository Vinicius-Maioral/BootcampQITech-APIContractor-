import asyncio
import json

from starlette.requests import Request

import controllers.contract_controller as controller_module
import database
from app import create_app
from resources.contract import ContractResource
from .test_contract_controller import RegistryMock, LedgerMock, controller, payload


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
    raw = json.dumps({**payload, 'actor': 'http-test'}).encode().replace(b'"10.0000"', b'99.9999')
    status, body = request(app, 'POST', '/contracts', raw)
    assert status == 201
    key = body['contract_key']
    status, body = request(app, 'GET', '/contracts/' + key)
    assert status == 200 and body['status'] == 'draft'
    assert body['status_events'][0]['trace_id'] == 'http-contract-test'
    audit = b'{"actor":"http-test"}'
    assert request(app, 'POST', f'/contracts/{key}/activation', audit)[0] == 200
    status, body = request(app, 'POST', f'/contracts/{key}/split_versions',
                           json.dumps({'split': payload['split'], 'actor': 'http-test'}).encode())
    assert status == 201 and body['version'] == 2
    assert request(app, 'POST', f'/contracts/{key}/closure', audit)[0] == 200
    status, body = request(app, 'POST', f'/contracts/{key}/activation', audit)
    assert (status, body['code']) == (409, 'QIT003003')
    assert request(app, 'GET', '/contracts', query=b'limit=1&status=terminated')[1]['contracts'][0]['contract_key'] == key
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
