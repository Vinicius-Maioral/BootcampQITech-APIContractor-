from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import relationship
from models.base import Base


class SplitItem(Base):
    __tablename__ = "split_item"

    id = Column(Integer, primary_key=True)
    split_version_id = Column(Integer, ForeignKey("split_version.id"), nullable=False)
    contract_party_id = Column(Integer, ForeignKey("contract_party.id"), nullable=False)
    purpose_code = Column(String(50), nullable=False)
    allocation_type = Column(String(20), nullable=False)
    percentage = Column(Numeric(7, 4))
    amount_in_cents = Column(Numeric(20, 0))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("split_version_id", "purpose_code"),
        CheckConstraint("purpose_code IN ('RENT', 'OWN', 'IPTU', 'CONDO', 'WATER', 'ELECTRICITY')"),
        CheckConstraint("allocation_type IN ('percentage', 'fixed', 'remainder')"),
        CheckConstraint("(allocation_type = 'percentage') = (percentage IS NOT NULL)"),
        CheckConstraint("(allocation_type = 'fixed') = (amount_in_cents IS NOT NULL)"),
    )

    split_version = relationship(
        "SplitVersion", foreign_keys=[split_version_id], back_populates="items", lazy="selectin"
    )
    contract_party = relationship(
        "ContractParty", foreign_keys=[contract_party_id], back_populates="split_items", lazy="selectin"
    )
