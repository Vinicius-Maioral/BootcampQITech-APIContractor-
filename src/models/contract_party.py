from sqlalchemy import CHAR, Column, DateTime, ForeignKey, Index, Integer, UniqueConstraint, func
from sqlalchemy.orm import relationship
from models.base import Base


class ContractParty(Base):
    __tablename__ = "contract_party"

    id = Column(Integer, primary_key=True)
    contract_id = Column(Integer, ForeignKey("contract.id"), nullable=False)
    party_key = Column(CHAR(36), nullable=False)
    contract_role_id = Column(Integer, ForeignKey("contract_role.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("contract_id", "party_key", "contract_role_id"),
        Index("contract_party_by_party", "party_key"),
    )

    contract = relationship(
        "Contract", foreign_keys=[contract_id], back_populates="parties", lazy="selectin"
    )
    role = relationship("ContractRole", foreign_keys=[contract_role_id], lazy="selectin")
    split_items = relationship("SplitItem", back_populates="contract_party")
