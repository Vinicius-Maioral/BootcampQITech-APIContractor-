from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import relationship
from models.base import Base


class SplitVersion(Base):
    __tablename__ = "split_version"

    id = Column(Integer, primary_key=True)
    contract_id = Column(Integer, ForeignKey("contract.id"), nullable=False)
    version = Column(Integer, nullable=False)
    rent_amount_in_cents = Column(Numeric(20, 0), nullable=False)
    # Nulo ate a confirmacao do Ledger; a transicao sera tratada na regra de negocio.
    provisioned_at = Column(DateTime(timezone=True))
    actor = Column(String(255), nullable=False)
    reason = Column(String(255))
    trace_id = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("contract_id", "version"),
        CheckConstraint("version >= 1"),
        CheckConstraint("rent_amount_in_cents > 0"),
    )

    contract = relationship(
        "Contract", foreign_keys=[contract_id], back_populates="split_versions", lazy="selectin"
    )
    items = relationship("SplitItem", back_populates="split_version")
