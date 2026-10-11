from datetime import timezone
from decimal import Decimal


class ContractDTO:
    @staticmethod
    def instant(value):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')

    @staticmethod
    def simplified(contract):
        return {
            'contract_key': contract.contract_key, 'client_key': contract.client_key,
            'external_reference': contract.external_reference,
            'property_reference': contract.property_reference,
            'start_date': contract.start_date.isoformat(),
            'end_date': contract.end_date.isoformat() if contract.end_date else None,
            'status': contract.status.enumerator,
        }

    @staticmethod
    def split_data(split):
        return {
            'rent_amount_in_cents': int(split.rent_amount_in_cents),
            'items': [{
                'purpose_code': i.purpose_code, 'party_key': i.contract_party.party_key,
                'role': i.contract_party.role.enumerator, 'allocation_type': i.allocation_type,
                **({'percentage': format(i.percentage, '.4f')} if i.percentage is not None else {}),
                **({'amount_in_cents': int(i.amount_in_cents)} if i.amount_in_cents is not None else {}),
            } for i in sorted(split.items, key=lambda i: i.purpose_code)],
        }

    @staticmethod
    def public_items(data):
        return [{
            'purpose_code': i['purpose_code'], 'party_key': i['party_key'], 'role': i['role'],
            'type': i['allocation_type'],
            **({'value': format(Decimal(str(i['percentage'])), '.4f')} if 'percentage' in i else {}),
            **({'value': int(i['amount_in_cents'])} if 'amount_in_cents' in i else {}),
        } for i in data['items']]

    @staticmethod
    def snapshot(contract):
        result = ContractDTO.simplified(contract)
        result['parties'] = [{'party_key': p.party_key, 'role': p.role.enumerator} for p in contract.parties]
        result['platform_fee_percentage'] = str(contract.platform_fee_percentage) if contract.platform_fee_percentage is not None else None
        result['split_versions'] = [{
            **ContractDTO.split_data(s), 'version': s.version,
            'provisioned_at': ContractDTO.instant(s.provisioned_at),
            'actor': s.actor, 'reason': s.reason, 'trace_id': s.trace_id,
        } for s in sorted(contract.split_versions, key=lambda s: s.version)]
        return result

    @staticmethod
    def obj_to_dict(contract):
        result = ContractDTO.simplified(contract)
        result['parties'] = [{'party_key': p.party_key, 'role': p.role.enumerator} for p in contract.parties]
        provisioned = [s for s in contract.split_versions if s.provisioned_at is not None]
        split = (min(contract.split_versions, key=lambda s: s.version) if result['status'] == 'draft' or not provisioned else max(provisioned, key=lambda s: s.version))
        data = ContractDTO.split_data(split)
        result.update(split_version=split.version, provisioned_at=ContractDTO.instant(split.provisioned_at), rent_amount_in_cents=data['rent_amount_in_cents'], split=ContractDTO.public_items(data))
        result['status_events'] = [{
            'from_status': e.from_status.enumerator if e.from_status else None,
            'to_status': e.to_status.enumerator, 'actor': e.actor, 'reason': e.reason,
            'trace_id': e.trace_id, 'event_datetime': ContractDTO.instant(e.event_datetime),
        } for e in contract.status_events]
        return result
