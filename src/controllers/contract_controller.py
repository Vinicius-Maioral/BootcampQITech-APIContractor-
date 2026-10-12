from contextlib import contextmanager
from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import re
from uuid import UUID

from requests.exceptions import RequestException
from sqlalchemy.exc import IntegrityError

from connectors.ledger_connector import LedgerConnector
from connectors.registry_connector import RegistryConnector
from controllers.base_controller import BaseController
from dtos import ContractDTO
from errors import (
    AdministratorIsNotClient, ContractNotFound, DependencyUnavailable,
    DuplicatedExternalReference, InternalError, InvalidContractRoles,
    InvalidContractTransition, InvalidParameter, InvalidSchema,
    InvalidSplitBeneficiary, LandlordIsTenant, LedgerConflict, LedgerRefusedSplit,
    PartyNotActiveInRegistry, PartyNotFoundInRegistry, SplitPercentagesTooHigh,
)
from models import ContractStatus
from repositories import ContractRepository
from utils.request_context import get_request_id
from utils.schema_handler import SchemaHandler


class ContractController(BaseController):
    """Coordinate Cadastro, Ledger and short local transactions according to 01b."""

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
        try:
            with self.session.begin():
                yield
        except Exception:
            if self._confirmed_operation:
                self.logger.exception('LEDGER_CONFIRMED_LOCAL_FAILURE operation=%s trace_id=%s; reconciliation required', self._confirmed_operation, self.trace_id)
            raise
        finally:
            self._confirmed_operation = None

    @staticmethod
    def _check_key(key):
        try:
            parsed = UUID(key)
            if str(parsed) != key or parsed.version != 4:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            # RFC v2 only foresees 200/404 for a contract key: a malformed key names no contract.
            raise ContractNotFound(key)

    def _require_contract(self, key, lock=False):
        self._check_key(key)
        contract = self.repository.get_by_key(key, lock=lock)
        if contract is None:
            raise ContractNotFound(key)
        return contract

    def _snapshot(self, key):
        with self.session.begin():
            return ContractDTO.snapshot(self._require_contract(key))

    def create(self, contract_data: dict, actor: str, reason=None) -> dict:
        data = self._validated_data(contract_data, actor, reason, 'post_contract.json')
        client = self._get_client(data['client_key'])
        self._validate_roles(data['parties'])
        if data.get('end_date') and date.fromisoformat(data['end_date']) < date.fromisoformat(data['start_date']):
            raise InvalidParameter('end_date must not precede start_date')
        self._validate_administrator(data['parties'], client['party_key'])
        self._validate_split(data['parties'], data['split'])
        try:
            with self._transaction():
                if self.repository.get_by_external_reference(data['client_key'], data['external_reference']):
                    raise DuplicatedExternalReference(data['client_key'], data['external_reference'])
                contract = self.repository.create(data)
                self.repository.add_split(contract, data['split'], 1, actor, reason, self.trace_id)
                self.repository.append_status_event(contract, None, ContractStatus.DRAFT, actor, reason, self.trace_id)
                result = {'contract_key': contract.contract_key, 'status': ContractStatus.DRAFT}
        except IntegrityError as error:
            if (getattr(error.orig, 'pgcode', None) == '23505' and getattr(getattr(error.orig, 'diag', None), 'constraint_name', None) == 'contract_client_key_external_reference_key'):
                raise DuplicatedExternalReference(data['client_key'], data['external_reference']) from error
            raise
        return result

    def get_by_key(self, contract_key: str) -> dict:
        with self.session.begin():
            return ContractDTO.obj_to_dict(self._require_contract(contract_key))

    def get_list(self, limit: int, page: int, filters: dict) -> dict:
        if not 1 <= limit <= 100 or page < 0:
            raise InvalidParameter('limit must be 1..100 and page must be nonnegative')
        with self.session.begin():
            contracts = self.repository.list_page(limit, page * limit, filters)
            result = []
            for contract in contracts[:limit]:
                item = ContractDTO.simplified(contract)
                if filters.get('party_key'):
                    item['roles'] = sorted(p.role.enumerator for p in contract.parties if p.party_key == filters['party_key'])
                result.append(item)
            return {'contracts': result, 'is_last_page': len(contracts) <= limit}

    def activate(self, contract_key: str, actor: str, reason=None) -> dict:
        self._validate_audit(actor, reason)
        self._check_key(contract_key)
        with self.repository.advisory_lock(contract_key):
            snapshot = self._snapshot(contract_key)
            if snapshot['status'] == ContractStatus.ACTIVE:
                version = max(s['version'] for s in snapshot['split_versions'] if s['provisioned_at'])
                return {'contract_key': contract_key, 'status': ContractStatus.ACTIVE, 'split_version': version}
            self._require_snapshot_status(snapshot, ContractStatus.DRAFT, 'active')
            client = self._get_client(snapshot['client_key'])
            for party in snapshot['parties']:
                self._get_party(party['party_key'])
            self._validate_roles(snapshot['parties'])
            self._validate_administrator(snapshot['parties'], client['party_key'])
            split = snapshot['split_versions'][-1]
            data = self._split_input(split)
            self._validate_split(snapshot['parties'], data)
            accounts = self._provision(snapshot, data, split['version'], client['party_key'], client['platform_fee_percentage'], reason)
            with self._transaction():
                contract = self._require_contract(contract_key, lock=True)
                self._require_status(contract, ContractStatus.DRAFT, 'active')
                contract.platform_fee_percentage = client['platform_fee_percentage']
                latest = max(contract.split_versions, key=lambda s: s.version)
                if latest.version != split['version'] or latest.provisioned_at is not None:
                    raise InvalidContractTransition('draft changed during provisioning', 'active')
                latest.provisioned_at = datetime.now(timezone.utc)
                self.repository.append_status_event(contract, ContractStatus.DRAFT, ContractStatus.ACTIVE, actor, reason, self.trace_id)
            return {'contract_key': contract_key, 'status': ContractStatus.ACTIVE, 'split_version': split['version'], 'ledger_accounts': accounts}

    def create_split_version(self, contract_key: str, split_data: dict, actor: str, reason=None) -> dict:
        data = self._validated_data(split_data, actor, reason, 'post_split_version.json')['split']
        self._check_key(contract_key)
        with self.repository.advisory_lock(contract_key):
            snapshot = self._snapshot(contract_key)
            self._require_snapshot_status(snapshot, ContractStatus.ACTIVE, 'new split version')
            self._validate_roles(snapshot['parties'])
            self._validate_split(snapshot['parties'], data)
            fee = snapshot['platform_fee_percentage']
            if fee is None:
                raise DependencyUnavailable('Missing activation fee snapshot')
            latest = snapshot['split_versions'][-1]
            replay = latest['provisioned_at'] is None
            if replay:
                if self._canonical(data) != self._canonical(self._split_input(latest)):
                    # Raised locally, not by the Ledger: this pending version may already be
                    # there, and re-sending another body would get 409 QIT001032 (same version,
                    # other content), which 01b maps to QIT003013. Answering it here avoids
                    # the call and keeps one code for the same conflict.
                    raise LedgerConflict(contract_key, 'pending split content differs')
                version = latest['version']
                data = self._split_input(latest)
            else:
                version = latest['version'] + 1
                with self._transaction():
                    contract = self._require_contract(contract_key, lock=True)
                    self._require_status(contract, ContractStatus.ACTIVE, 'new split version')
                    self.repository.add_split(contract, data, version, actor, reason, self.trace_id)
            client_party = next(p['party_key'] for p in snapshot['parties'] if p['role'] == 'administrator')
            accounts = self._provision(snapshot, data, version, client_party, fee, reason)
            with self._transaction():
                contract = self._require_contract(contract_key, lock=True)
                self._require_status(contract, ContractStatus.ACTIVE, 'new split version')
                split = next(s for s in contract.split_versions if s.version == version)
                if split.provisioned_at is None:
                    split.provisioned_at = datetime.now(timezone.utc)
            return {'contract_key': contract_key, 'status': ContractStatus.ACTIVE, 'split_version': version, 'ledger_accounts': accounts, '_http_status': 200 if replay else 201}

    def terminate(self, contract_key: str, actor: str, reason=None) -> dict:
        self._validate_audit(actor, reason)
        self._check_key(contract_key)
        with self.repository.advisory_lock(contract_key):
            snapshot = self._snapshot(contract_key)
            if snapshot['status'] == ContractStatus.TERMINATED:
                raise InvalidContractTransition(snapshot['status'], 'terminated')
            response = self._call('Ledger', self.ledger.close_contract_ledger, contract_key, {'actor': 'contract-api', 'reason': reason or 'termination of contract ' + snapshot['external_reference']})
            self._check_ledger_response(response, contract_key, 'closure', {200})
            self._confirmed_operation = contract_key + ':closure'
            with self._transaction():
                contract = self._require_contract(contract_key, lock=True)
                self.repository.append_status_event(contract, snapshot['status'], ContractStatus.TERMINATED, actor, reason, self.trace_id)
            return {'contract_key': contract_key, 'status': ContractStatus.TERMINATED}

    @staticmethod
    def _require_status(contract, expected, target):
        if contract.status.enumerator != expected:
            raise InvalidContractTransition(contract.status.enumerator, target)

    @staticmethod
    def _require_snapshot_status(snapshot, expected, target):
        if snapshot['status'] != expected:
            raise InvalidContractTransition(snapshot['status'], target)

    @staticmethod
    def _split_input(split):
        return {name: split[name] for name in ('rent_amount_in_cents', 'items')}

    @staticmethod
    def _canonical(data):
        return (data['rent_amount_in_cents'], tuple(sorted((i['purpose_code'], i['party_key'], i['role'], i['allocation_type'], Decimal(str(i['percentage'])) if 'percentage' in i else i.get('amount_in_cents')) for i in data['items'])))

    @staticmethod
    def _validated_data(data, actor, reason, schema):
        if not isinstance(data, dict):
            raise InvalidSchema('Request data must be an object')
        result = deepcopy(data)
        result.update(actor=actor, reason=reason)
        if 'rent_amount_in_cents' in result and type(result['rent_amount_in_cents']) is not int:
            raise InvalidSchema('rent_amount_in_cents must be an integer, never float or string')
        if isinstance(result.get('split'), list):
            for item in result['split']:
                if isinstance(item, dict) and item.get('type') == 'fixed' and type(item.get('value')) is not int:
                    raise InvalidSchema('fixed value must be an integer, never float or string')
        SchemaHandler.validate_payload(result, schema)
        result['split'] = {'rent_amount_in_cents': result['rent_amount_in_cents'], 'items': [{
            'purpose_code': i['purpose_code'], 'party_key': i['party_key'], 'role': i['role'], 'allocation_type': i['type'],
            **({'percentage': Decimal(i['value'])} if i['type'] == 'percentage' else {}),
            **({'amount_in_cents': i['value']} if i['type'] == 'fixed' else {}),
        } for i in result['split']]}
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
        assert not self.session.in_transaction(), 'External calls must run outside SQL transactions'
        try:
            return method(*args)
        except RequestException as error:
            self.logger.warning('Dependency call failed dependency=%s trace_id=%s result=unknown', dependency, self.trace_id)
            raise DependencyUnavailable(dependency) from error

    def _get_client(self, client_key):
        response = self._call('Cadastro', self.registry.get_client, client_key)
        if response.status == 404:
            raise PartyNotFoundInRegistry(client_key, self._source_code(response))
        data = self._registry_data(response)
        if data['status'] != 'active':
            raise PartyNotActiveInRegistry(client_key, self._source_code(response))
        try:
            party_key = data['party_key']
            parsed = UUID(party_key)
            if str(parsed) != party_key or parsed.version != 4:
                raise ValueError()
            percentage = data['platform_fee_percentage']
            if not isinstance(percentage, str) or not re.fullmatch(r'(?:0|[1-9][0-9]{0,2})(?:\.[0-9]{1,4})?', percentage):
                raise ValueError()
            fee = Decimal(percentage)
            if not fee.is_finite() or not 0 <= fee < 100 or fee.as_tuple().exponent < -4:
                raise ValueError()
        except (KeyError, TypeError, ValueError, AttributeError, InvalidOperation):
            raise DependencyUnavailable('Cadastro: invalid client response')
        return {'party_key': party_key, 'platform_fee_percentage': fee}

    def _get_party(self, party_key):
        response = self._call('Cadastro', self.registry.get_party, party_key)
        if response.status == 404:
            raise PartyNotFoundInRegistry(party_key, self._source_code(response))
        data = self._registry_data(response)
        if data['status'] != 'active':
            raise PartyNotActiveInRegistry(party_key, self._source_code(response))
        return data

    def _registry_data(self, response):
        if response.status == 429 or response.status >= 500:
            raise DependencyUnavailable('Cadastro', self._source_code(response))
        if response.status != 200:
            self.logger.error('Unexpected Cadastro HTTP status=%s', response.status)
            raise InternalError()
        if not isinstance(response.json, dict) or not isinstance(response.json.get('status'), str):
            raise DependencyUnavailable('Cadastro: invalid response')
        return response.json

    @staticmethod
    def _source_code(response):
        code = response.json.get('code') if isinstance(response.json, dict) else None
        return code if isinstance(code, str) and code else None

    def _check_ledger_response(self, response, contract_key, operation, accepted):
        if response.status in accepted:
            return
        code = self._source_code(response)
        if operation == 'closure' and response.status == 404 and code == 'QIT001035':
            return
        if response.status == 429 or response.status >= 500 or (operation == 'closure' and response.status == 409 and code == 'QIT001011'):
            raise DependencyUnavailable('Ledger', code)
        conflicts = {'QIT001032', 'QIT001033'} if operation == 'split' else {'QIT001037'}
        if response.status == 409 and code in conflicts:
            raise LedgerConflict(contract_key, operation, source_code=code)
        if operation == 'split' and response.status == 422 and code in {'QIT001008', 'QIT001028', 'QIT001034'}:
            raise LedgerRefusedSplit(contract_key, source_code=code)
        self.logger.error('Unexpected Ledger response status=%s operation=%s code=%s', response.status, operation, code)
        raise InternalError()

    def _provision(self, snapshot, data, version, client_party, fee, reason):
        public = ContractDTO.public_items(data)
        payload = {
            'contract_key': snapshot['contract_key'], 'client_key': snapshot['client_key'], 'client_party_key': client_party,
            'split_version': version, 'rent_amount_in_cents': data['rent_amount_in_cents'],
            'platform_fee_percentage': format(Decimal(str(fee)), '.4f'),
            'items': [{
                'purpose_code': i['purpose_code'], 'beneficiary_party_key': i['party_key'], 'beneficiary_role': i['role'], 'type': i['type'],
                **({'value': i['value']} if 'value' in i else {}),
            } for i in sorted(public, key=lambda i: i['purpose_code'])],
            'actor': 'contract-api', 'reason': reason or 'provisioning contract ' + snapshot['external_reference'],
        }
        response = self._call('Ledger', self.ledger.provision_contract_ledger, payload)
        self._check_ledger_response(response, snapshot['contract_key'], 'split', {200, 201})
        self._confirmed_operation = f"{snapshot['contract_key']}:split:{version}"
        body = response.json
        try:
            if not isinstance(body, dict) or body['contract_key'] != snapshot['contract_key'] or type(body['split_version']) is not int or body['split_version'] != version:
                raise ValueError()
            if type(body['expected_amount_in_cents']) is not int or not isinstance(body['accounts'], list) or not body['accounts']:
                raise ValueError()
            accounts = body['accounts']
            for account in accounts:
                parsed = UUID(account['ledger_account_key'])
                if parsed.version != 4 or str(parsed) != account['ledger_account_key'] or not isinstance(account['purpose_code'], str):
                    raise ValueError()
                if account['owner_party_key'] is not None:
                    owner = UUID(account['owner_party_key'])
                    if owner.version != 4 or str(owner) != account['owner_party_key']:
                        raise ValueError()
                if account['owner_role'] not in {None, 'tenant', 'landlord', 'administrator', 'provider', 'platform'}:
                    raise ValueError()
            return accounts
        except (KeyError, TypeError, AttributeError, ValueError):
            self.logger.error('LEDGER_CONFIRMED_INVALID_RESPONSE operation=%s trace_id=%s', self._confirmed_operation, self.trace_id)
            raise DependencyUnavailable('Ledger: invalid provisioning response')
