from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import relationship
from models.base import Base


class ContractStatusEvent(Base):
    __tablename__ = "contract_status_event"

    id = Column(Integer, primary_key=True)
    contract_id = Column(Integer, ForeignKey("contract.id"), nullable=False)
    from_status_id = Column(Integer, ForeignKey("contract_status.id"))
    to_status_id = Column(Integer, ForeignKey("contract_status.id"), nullable=False)
    actor = Column(String(255), nullable=False)
    reason = Column(String(255))
    trace_id = Column(String(64), nullable=False)
    event_datetime = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    contract = relationship(
        "Contract", foreign_keys=[contract_id], back_populates="status_events", lazy="selectin"
    )
    from_status = relationship("ContractStatus", foreign_keys=[from_status_id], lazy="selectin")
    to_status = relationship("ContractStatus", foreign_keys=[to_status_id], lazy="selectin")
