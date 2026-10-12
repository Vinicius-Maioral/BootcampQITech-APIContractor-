"""The only direct database access left in the tests that drive the real API.

Why: no HTTP route can prove that the database itself refuses UPDATE/DELETE on
the history, nor that PostgreSQL answers on the published port (guide §12).
Every other assertion reads the API (`GET /contracts/{key}`).
"""
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError


class ContractDatabase:
    def __init__(self, url):
        self.engine = create_engine(url)

    def dispose(self):
        self.engine.dispose()

    def ping(self):
        with self.engine.connect() as connection:
            return connection.execute(text('SELECT 1')).scalar_one()

    def refused_event_change(self, contract_key, statement):
        """Try UPDATE/DELETE on the contract's events; always roll back. Returns the refusal message or None."""
        with self.engine.connect() as connection:
            transaction = connection.begin()
            try:
                connection.execute(text(statement + '''
                    WHERE contract_id = (SELECT id FROM contract WHERE contract_key = :key)
                '''), {'key': contract_key})
            except DBAPIError as error:
                return str(error.orig)
            finally:
                transaction.rollback()
        return None
