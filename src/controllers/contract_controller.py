from contextlib import contextmanager
from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from uuid import UUID

from requests.exceptions import RequestException
from sqlalchemy.exc import IntegrityError

from connectors.ledger_connector import LedgerConnector
from connectors.registry_connector import RegistryConnector
from controllers.base_controller import BaseController
from dtos import ContractDTO
from errors import (
    AdministratorIsNotClient, ClientNotActiveInRegistry, ClientNotFoundInRegistry,
    ContractNotFound, DependencyUnavailable, DuplicatedExternalReference, InternalError,
    InvalidContractRoles, InvalidContractTransition, InvalidParameter, InvalidSchema,
    InvalidSplitBeneficiary, LandlordIsTenant, LedgerConflict, LedgerRefusedSplit,
    PartyNotActiveInRegistry, PartyNotFoundInRegistry, SplitPercentagesTooHigh,
)
from models import ContractStatus
from repositories import ContractRepository
from utils.request_context import get_request_id
from utils.schema_handler import SchemaHandler


class ContractController(BaseController):
    """Contract business rules and coordination of local/external state."""

    def __init__(self):
        super().__init__(__name__)
        self.repository = ContractRepository(self.context)
        self.registry = RegistryConnector()
        self.ledger = LedgerConnector()
        self._confirmed_operation = None

    @property
    def trace_id(self):
        return get_request_id()

    @contextmanager
    def _transaction(self):
        self._confirmed_operation = None
        try:
            with self.session.begin():
                yield
        except Exception:
            if self._confirmed_operation:
                self.logger.exception(
                    'LEDGER_CONFIRMED_LOCAL_FAILURE operation=%s trace_id=%s; reconciliation required',
                    self._confirmed_operation, self.trace_id,
                )
            raise
        finally:
            self._confirmed_operation = None

    def create(self, contract_data: dict, actor: str, reason=None) -> dict:
        data = self._validated_data(contract_data, actor, reason, 'post_contract.json')
        self._validate_roles(data['parties'])
        if data.get('end_date') and date.fromisoformat(data['end_date']) < date.fromisoformat(data['start_date']):
            raise InvalidParameter('end_date must not precede start_date')
        client = self._get_client(data['client_key'])
        self._validate_administrator(data['parties'], client['party_key'])
        self._validate_split(data['parties'], data['split'])
        try:
            with self._transaction():
                if self.repository.get_by_external_reference(data['client_key'], data['external_reference']):
                    raise DuplicatedExternalReference(data['client_key'], data['external_reference'])
                contract = self.repository.create(data)
                self.repository.add_split(contract, data['split'], 1, actor, reason, self.trace_id)
                self.repository.append_status_event(contract, None, ContractStatus.DRAFT, actor, reason, self.trace_id)
                result = ContractDTO.only_obj_key(contract)
        except IntegrityError as error:
            if (getattr(error.orig, 'pgcode', None) == '23505'
                    and getattr(getattr(error.orig, 'diag', None), 'constraint_name', None)
                    == 'contract_client_key_external_reference_key'):
                raise DuplicatedExternalReference(data['client_key'], data['external_reference']) from error
            raise
        return result

    def _require_contract(self, key, lock=False):
        try:
            if str(UUID(key)) != key:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise InvalidParameter('contract_key must be a canonical UUID')
        contract = self.repository.get_by_key(key, lock=lock)
        if contract is None:
            raise ContractNotFound(key)
        return contract

    def get_by_key(self, contract_key: str) -> dict:
        with self.session.begin():
            return ContractDTO.obj_to_dict(self._require_contract(contract_key))

    def get_list(self, limit: int, offset: int, filters: dict) -> dict:
        if not 1 <= limit <= 100 or offset < 0:
            raise InvalidParameter('limit must be 1..100 and offset must be nonnegative')
        with self.session.begin():
            contracts = self.repository.list_page(limit, offset, filters)
            return {
                'contracts': [ContractDTO.simplified(c) for c in contracts[:limit]],
                'is_last_page': len(contracts) <= limit,
            }

    def activate(self, contract_key: str, actor: str, reason=None) -> dict:
        self._validate_audit(actor, reason)
        # Finish even read-only SQL transactions before calling Cadastro.
        with self.session.begin():
            contract = self._require_contract(contract_key)
            self._require_status(contract, ContractStatus.DRAFT, ContractStatus.ACTIVE)
            snapshot = ContractDTO.obj_to_dict(contract)
        client = self._get_client(snapshot['client_key'])
        for party_key in sorted({p['party_key'] for p in snapshot['parties']}):
            self._get_party(party_key)
        self._validate_roles(snapshot['parties'])
        self._validate_administrator(snapshot['parties'], client['party_key'])
        split_data = self._split_input(snapshot['split_versions'][-1])
        self._validate_split(snapshot['parties'], split_data)
        with self._transaction():
            contract = self._require_contract(contract_key, lock=True)
            self._require_status(contract, ContractStatus.DRAFT, ContractStatus.ACTIVE)
            if ContractDTO.obj_to_dict(contract) != snapshot:
                raise InvalidContractTransition('draft changed during validation', 'active')
            split = max(contract.split_versions, key=lambda s: s.version)
            if split.provisioned_at is not None:
                raise InvalidContractTransition('already provisioned', 'active')
            ledger_key = self._provision(contract, split_data, split.version, client['platform_fee_percentage'])
            contract.ledger_key = ledger_key
            contract.platform_fee_percentage = client['platform_fee_percentage']
            split.provisioned_at = datetime.now(timezone.utc)
            self.repository.append_status_event(
                contract, ContractStatus.DRAFT, ContractStatus.ACTIVE, actor, reason, self.trace_id,
            )
            result = {'contract_key': contract_key, 'status': ContractStatus.ACTIVE}
        return result

    def create_split_version(self, contract_key: str, split_data: dict, actor: str, reason=None) -> dict:
        data = self._validated_data({'split': split_data}, actor, reason, 'post_split_version.json')['split']
        with self._transaction():
            contract = self._require_contract(contract_key, lock=True)
            self._require_status(contract, ContractStatus.ACTIVE, 'new split version')
            parties = [{'party_key': p.party_key, 'role': p.role.enumerator} for p in contract.parties]
            self._validate_roles(parties)
            self._validate_split(parties, data)
            version = max(s.version for s in contract.split_versions) + 1
            ledger_key = self._provision(contract, data, version, contract.platform_fee_percentage)
            if ledger_key != contract.ledger_key:
                raise DependencyUnavailable('Ledger: inconsistent ledger identity')
            self.repository.add_split(
                contract, data, version, actor, reason, self.trace_id, datetime.now(timezone.utc),
            )
            result = {'contract_key': contract_key, 'version': version}
        return result

    def terminate(self, contract_key: str, actor: str, reason=None) -> dict:
        self._validate_audit(actor, reason)
        with self._transaction():
            contract = self._require_contract(contract_key, lock=True)
            self._require_status(contract, ContractStatus.ACTIVE, ContractStatus.TERMINATED)
            if not contract.ledger_key:
                raise DependencyUnavailable('Ledger: missing ledger identity')
            operation_key = f'{contract_key}:closure'
            response = self._call('Ledger', self.ledger.close_contract_ledger, contract.ledger_key, operation_key)
            self._check_ledger_response(response, contract_key, 'closure', {200, 204})
            self._confirmed_operation = operation_key
            self.repository.append_status_event(
                contract, ContractStatus.ACTIVE, ContractStatus.TERMINATED, actor, reason, self.trace_id,
            )
            result = {'contract_key': contract_key, 'status': ContractStatus.TERMINATED}
        return result

    @staticmethod
    def _require_status(contract, expected, target):
        current = contract.status.enumerator
        if current != expected:
            raise InvalidContractTransition(current, target)

    @staticmethod
    def _split_input(split):
        return {name: split[name] for name in ('rent_amount_in_cents', 'items')}

    @staticmethod
    def _validated_data(data, actor, reason, schema):
        if not isinstance(data, dict):
            raise InvalidSchema('Request data must be an object')
        result = deepcopy(data)
        result.update(actor=actor, reason=reason)
        # Raw JSON numbers are parsed as Decimal by the resource; schemas validate the exact text.
        split = result.get('split')
        if isinstance(split, dict) and isinstance(split.get('items'), list):
            for item in split['items']:
                if isinstance(item, dict):
                    value = item.get('percentage')
                    if isinstance(value, (Decimal, float)):
                        item['percentage'] = str(value)
        SchemaHandler.validate_payload(result, schema)
        if isinstance(split, dict):
            for item in split['items']:
                if 'percentage' in item:
                    item['percentage'] = Decimal(str(item['percentage']))
        return result

    @staticmethod
    def _validate_audit(actor, reason):
        SchemaHandler.validate_payload({'actor': actor, 'reason': reason}, 'contract_action.json')

    @staticmethod
    def _validate_roles(parties):
        links = [(p['party_key'], p['role']) for p in parties]
        if (len(set(links)) != len(links)
                or any(sum(p['role'] == r for p in parties) != 1 for r in ('tenant', 'landlord', 'administrator'))):
            raise InvalidContractRoles(', '.join(p['role'] for p in parties))
        tenant = next(p['party_key'] for p in parties if p['role'] == 'tenant')
        landlord = next(p['party_key'] for p in parties if p['role'] == 'landlord')
        if landlord == tenant:
            raise LandlordIsTenant(landlord)

    @staticmethod
    def _validate_administrator(parties, client_party_key):
        administrator = next(p['party_key'] for p in parties if p['role'] == 'administrator')
        if administrator != client_party_key:
            raise AdministratorIsNotClient(administrator, client_party_key)

    @staticmethod
    def _validate_split(parties, split):
        items = split['items']
        purposes = [i['purpose_code'] for i in items]
        if len(set(purposes)) != len(purposes):
            raise InvalidSchema('purpose_code must be unique within a split')
        if sum(i['allocation_type'] == 'remainder' for i in items) != 1:
            raise InvalidSchema('A split must have exactly one remainder')
        links = {(p['party_key'], p['role']) for p in parties}
        requirements = {'RENT': ('remainder', 'landlord'), 'OWN': ('percentage', 'administrator')}
        for item in items:
            expected = requirements.get(item['purpose_code'], ('fixed', 'provider'))
            if ((item['party_key'], item['role']) not in links
                    or (item['allocation_type'], item['role']) != expected):
                raise InvalidSplitBeneficiary(item['party_key'])
        total = sum((Decimal(str(i['percentage'])) for i in items if i['allocation_type'] == 'percentage'), Decimal(0))
        if total >= 100:
            raise SplitPercentagesTooHigh(total)

    def _call(self, dependency, method, *args):
        try:
            return method(*args)
        except RequestException as error:
            self.logger.warning('Dependency call failed dependency=%s trace_id=%s result=unknown', dependency, self.trace_id)
            raise DependencyUnavailable(dependency) from error

    def _get_client(self, client_key):
        response = self._call('Cadastro', self.registry.get_client, client_key)
        if response.status == 404:
            raise ClientNotFoundInRegistry(client_key)
        data = self._registry_data(response)
        if data['status'] != 'active':
            raise ClientNotActiveInRegistry(client_key)
        try:
            party_key = data['party_key']
            if not isinstance(party_key, str) or str(UUID(party_key)) != party_key:
                raise ValueError()
            if isinstance(data['platform_fee_percentage'], bool):
                raise ValueError()
            fee = Decimal(str(data['platform_fee_percentage']))
            if not fee.is_finite() or not 0 <= fee < 100 or fee.as_tuple().exponent < -4:
                raise ValueError()
        except (KeyError, TypeError, ValueError, InvalidOperation):
            raise DependencyUnavailable('Cadastro: invalid client response')
        return {'party_key': party_key, 'platform_fee_percentage': fee}

    def _get_party(self, party_key):
        response = self._call('Cadastro', self.registry.get_party, party_key)
        if response.status == 404:
            raise PartyNotFoundInRegistry(party_key)
        data = self._registry_data(response)
        if data['status'] != 'active':
            raise PartyNotActiveInRegistry(party_key)
        return data

    def _registry_data(self, response):
        if response.status == 429 or response.status >= 500:
            raise DependencyUnavailable('Cadastro')
        if response.status != 200:
            self.logger.error('Unexpected Cadastro HTTP status=%s', response.status)
            raise InternalError()
        if not isinstance(response.json, dict) or not isinstance(response.json.get('status'), str):
            raise DependencyUnavailable('Cadastro: invalid response')
        return response.json

    def _check_ledger_response(self, response, contract_key, operation, accepted):
        if response.status in accepted:
            return
        if response.status == 429 or response.status >= 500:
            raise DependencyUnavailable('Ledger')
        code = response.json.get('code') if isinstance(response.json, dict) else None
        # Provisional codes, deliberately explicit until 01b is supplied.
        if response.status == 409 and code == 'LEDGER_CONFLICT':
            raise LedgerConflict(contract_key, operation)
        if operation == 'split' and response.status == 422 and code == 'LEDGER_REFUSED_SPLIT':
            raise LedgerRefusedSplit(contract_key)
        self.logger.error('Unexpected Ledger response status=%s operation=%s code=%s', response.status, operation, code)
        raise InternalError()

    def _provision(self, contract, data, version, fee):
        if fee is None:
            raise DependencyUnavailable('Cadastro: missing fee snapshot')
        operation_key = f'{contract.contract_key}:split:{version}'
        payload = {
            'contract_key': contract.contract_key, 'client_key': contract.client_key,
            'version': version, 'platform_fee_percentage': format(Decimal(str(fee)), '.4f'),
            'parties': sorted(
                ({'party_key': p.party_key, 'role': p.role.enumerator} for p in contract.parties),
                key=lambda p: (p['party_key'], p['role']),
            ),
            'split': {**data, 'items': sorted(data['items'], key=lambda i: i['purpose_code'])},
        }
        # Normalize numeric representation so retries of the same version have identical content.
        payload['split']['items'] = [dict(i) for i in payload['split']['items']]
        for item in payload['split']['items']:
            if 'percentage' in item:
                item['percentage'] = format(Decimal(str(item['percentage'])), '.4f')
        response = self._call('Ledger', self.ledger.provision_contract_ledger, payload, operation_key)
        self._check_ledger_response(response, contract.contract_key, 'split', {200, 201})
        self._confirmed_operation = operation_key
        key = response.json.get('contract_ledger_key') if isinstance(response.json, dict) else None
        if not isinstance(key, str) or not 1 <= len(key) <= 255:
            raise DependencyUnavailable('Ledger: missing contract_ledger_key')
        return key
