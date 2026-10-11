from datetime import date, datetime, timezone
from uuid import uuid4

from sqlalchemy.orm import selectinload

from models import Contract, ContractParty, ContractRole, ContractStatus, ContractStatusEvent, SplitItem, SplitVersion


class ContractRepository:
    """Persistence only; transactions and business decisions belong to the controller."""

    def __init__(self, context):
        self.session = context.db_session

    def get_by_key(self, key, lock=False):
        query = self.session.query(Contract).filter(Contract.contract_key == key)
        if lock:
            query = query.with_for_update(of=Contract).populate_existing()
        return query.first()

    def get_by_external_reference(self, client_key, external_reference):
        return self.session.query(Contract).filter_by(
            client_key=client_key, external_reference=external_reference
        ).first()

    def get_status(self, enumerator):
        return self.session.query(ContractStatus).filter_by(enumerator=enumerator).one()

    def create(self, data):
        contract = Contract(
            contract_key=str(uuid4()), client_key=data['client_key'],
            external_reference=data['external_reference'], property_reference=data['property_reference'],
            start_date=date.fromisoformat(data['start_date']),
            end_date=date.fromisoformat(data['end_date']) if data.get('end_date') else None,
            status=self.get_status(ContractStatus.DRAFT),
        )
        for party in data['parties']:
            role = self.session.query(ContractRole).filter_by(enumerator=party['role']).one()
            contract.parties.append(ContractParty(party_key=party['party_key'], role=role))
        self.session.add(contract)
        return contract

    def append_status_event(self, contract, old_status, new_status, actor, reason, trace_id):
        target = self.get_status(new_status)
        contract.status_events.append(ContractStatusEvent(
            from_status=self.get_status(old_status) if old_status else None,
            to_status=target, actor=actor, reason=reason, trace_id=trace_id,
            event_datetime=datetime.now(timezone.utc),
        ))
        contract.status = target

    def add_split(self, contract, data, version, actor, reason, trace_id, provisioned_at=None):
        parties = {(p.party_key, p.role.enumerator): p for p in contract.parties}
        split = SplitVersion(
            version=version, rent_amount_in_cents=data['rent_amount_in_cents'],
            actor=actor, reason=reason, trace_id=trace_id, provisioned_at=provisioned_at,
        )
        for item in data['items']:
            split.items.append(SplitItem(
                contract_party=parties[(item['party_key'], item['role'])],
                purpose_code=item['purpose_code'], allocation_type=item['allocation_type'],
                percentage=item.get('percentage'), amount_in_cents=item.get('amount_in_cents'),
            ))
        contract.split_versions.append(split)
        return split

    def list_page(self, limit, offset, filters):
        query = self.session.query(Contract).options(selectinload(Contract.status))
        for name in ('client_key', 'external_reference'):
            if filters.get(name) is not None:
                query = query.filter(getattr(Contract, name) == filters[name])
        if filters.get('status_enumerators'):
            query = query.join(Contract.status).filter(
                ContractStatus.enumerator.in_(filters['status_enumerators'])
            )
        return query.order_by(Contract.created_at.desc(), Contract.id.desc()).limit(limit + 1).offset(offset).all()
