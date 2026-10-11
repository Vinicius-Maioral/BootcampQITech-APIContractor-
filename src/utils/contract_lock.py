"""Serialize contract operations without holding an SQL transaction during HTTP."""
from contextlib import contextmanager
from hashlib import sha256
from threading import Lock, RLock
from weakref import WeakValueDictionary

from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import NullPool

from errors import DependencyUnavailable


_local_locks = WeakValueDictionary()
_guard = Lock()


@contextmanager
def contract_lock(session, contract_key):
    bind = session.get_bind()
    if bind.dialect.name != 'postgresql':
        # SQLite is used only by isolated tests; production locks span processes.
        with _guard:
            lock = _local_locks.setdefault(contract_key, RLock())
        with lock:
            yield
        return

    key = int.from_bytes(sha256(contract_key.encode()).digest()[:8], 'big', signed=True)
    # A separate, unpooled connection prevents waiting requests exhausting the
    # application's pool while an operation needs a connection to commit.
    engine = create_engine(bind.url, poolclass=NullPool, isolation_level='AUTOCOMMIT')
    try:
        with engine.connect() as connection:
            try:
                connection.exec_driver_sql("SET lock_timeout = '5s'")
                connection.execute(text('SELECT pg_advisory_lock(:key)'), {'key': key})
            except OperationalError as error:
                if getattr(error.orig, 'pgcode', None) == '55P03':
                    raise DependencyUnavailable('Contract operation contention') from error
                raise
            try:
                yield
            finally:
                try:
                    connection.execute(text('SELECT pg_advisory_unlock(:key)'), {'key': key})
                except Exception:
                    # Never return a connection with an uncertain lock to a pool.
                    connection.invalidate()
                    raise
    finally:
        engine.dispose()
