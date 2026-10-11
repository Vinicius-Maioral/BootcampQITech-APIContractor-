import json
from decimal import Decimal

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from controllers import ContractController
from errors import InvalidSchema
from utils.schema_handler import SchemaHandler


class ContractResource:
    """HTTP transport only. Decimal parsing preserves the original percentage precision."""

    @staticmethod
    async def _body(request):
        try:
            payload = json.loads(await request.body(), parse_float=Decimal,
                                 parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
        except (ValueError, UnicodeDecodeError) as error:
            raise InvalidSchema('Request body must be valid JSON') from error
        if not isinstance(payload, dict):
            raise InvalidSchema('Request body must be an object')
        return payload

    @staticmethod
    def _invoke(method, *args):
        return getattr(ContractController(), method)(*args)

    async def on_post(self, request: Request):
        payload = await self._body(request)
        # Controller schemas also validate required audit fields; extraction must not raise KeyError.
        data = {k: v for k, v in payload.items() if k not in ('actor', 'reason')}
        result = await run_in_threadpool(self._invoke, 'create', data, payload.get('actor'), payload.get('reason'))
        return JSONResponse(result, status_code=201)

    async def on_get_by_key(self, contract_key: str):
        return JSONResponse(await run_in_threadpool(self._invoke, 'get_by_key', contract_key))

    async def on_get_list(self, request: Request):
        params = request.query_params
        payload = {k: params.getlist(k) if k == 'status' else params[k] for k in params}
        for key in params:
            if key != 'status' and len(params.getlist(key)) != 1:
                raise InvalidSchema(f'Query parameter {key} must not be repeated')
        SchemaHandler.validate_payload(payload, 'get_contracts.json')
        filters = {'client_key': params.get('client_key'), 'external_reference': params.get('external_reference'),
                   'status_enumerators': params.getlist('status')}
        result = await run_in_threadpool(self._invoke, 'get_list', int(params.get('limit', 10)),
                                         int(params.get('offset', 0)), filters)
        return JSONResponse(result)

    async def on_activate(self, contract_key: str, request: Request):
        payload = await self._body(request)
        SchemaHandler.validate_payload(payload, 'contract_action.json')
        result = await run_in_threadpool(self._invoke, 'activate', contract_key, payload['actor'], payload.get('reason'))
        return JSONResponse(result)

    async def on_split_version(self, contract_key: str, request: Request):
        payload = await self._body(request)
        # Validate outer fields before unpacking; percentage values are normalized by the controller.
        unknown = set(payload) - {'split', 'actor', 'reason'}
        if unknown or 'split' not in payload:
            raise InvalidSchema('Expected split, actor and optional reason')
        result = await run_in_threadpool(self._invoke, 'create_split_version', contract_key,
                                         payload['split'], payload.get('actor'), payload.get('reason'))
        return JSONResponse(result, status_code=201)

    async def on_terminate(self, contract_key: str, request: Request):
        payload = await self._body(request)
        SchemaHandler.validate_payload(payload, 'contract_action.json')
        result = await run_in_threadpool(self._invoke, 'terminate', contract_key, payload['actor'], payload.get('reason'))
        return JSONResponse(result)
