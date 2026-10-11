from sqlalchemy import CHAR, Column, Date, DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import relationship
from models.base import Base


class Contract(Base):
    __tablename__ = "contract"

    id = Column(Integer, primary_key=True)
    contract_key = Column(CHAR(36), nullable=False)
    client_key = Column(CHAR(36), nullable=False)
    contract_status_id = Column(Integer, ForeignKey("contract_status.id"), nullable=False)
    external_reference = Column(String(100), nullable=False)
    property_reference = Column(String(255), nullable=False)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date)
    # Ledger identity and fee snapshot, confirmed during activation.
    ledger_key = Column(String(255))
    platform_fee_percentage = Column(Numeric(7, 4))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("contract_key"),
        UniqueConstraint("client_key", "external_reference"),
    )

    status = relationship("ContractStatus", foreign_keys=[contract_status_id], lazy="selectin")
    status_events = relationship(
        "ContractStatusEvent",
        back_populates="contract",
        order_by="(ContractStatusEvent.event_datetime, ContractStatusEvent.id)",
    )
    parties = relationship("ContractParty", back_populates="contract")
    split_versions = relationship(
        "SplitVersion", back_populates="contract", order_by="SplitVersion.version"
    )
