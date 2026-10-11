class ContractDTO:
    @staticmethod
    def only_obj_key(contract):
        return {'contract_key': contract.contract_key}

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
                **({'percentage': str(i.percentage)} if i.percentage is not None else {}),
                **({'amount_in_cents': int(i.amount_in_cents)} if i.amount_in_cents is not None else {}),
            } for i in split.items],
        }

    @staticmethod
    def obj_to_dict(contract):
        result = ContractDTO.simplified(contract)
        result['parties'] = [{'party_key': p.party_key, 'role': p.role.enumerator} for p in contract.parties]
        result['split_versions'] = [{
            **ContractDTO.split_data(s), 'version': s.version,
            'provisioned_at': s.provisioned_at.isoformat() if s.provisioned_at else None,
            'actor': s.actor, 'reason': s.reason, 'trace_id': s.trace_id,
        } for s in sorted(contract.split_versions, key=lambda s: s.version)]
        result['status_events'] = [{
            'from_status': e.from_status.enumerator if e.from_status else None,
            'to_status': e.to_status.enumerator, 'actor': e.actor, 'reason': e.reason,
            'trace_id': e.trace_id, 'event_datetime': e.event_datetime.isoformat(),
        } for e in contract.status_events]
        return result
