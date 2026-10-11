from sqlalchemy import Column, DateTime, Integer, String, UniqueConstraint, func
from models.base import Base


class ContractRole(Base):
    __tablename__ = "contract_role"

    id = Column(Integer, primary_key=True)
    enumerator = Column(String(50), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (UniqueConstraint("enumerator"),)

    TENANT = "tenant"
    LANDLORD = "landlord"
    ADMINISTRATOR = "administrator"
    PROVIDER = "provider"
