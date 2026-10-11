"""PostgreSQL checks use a disposable schema; never reset the application's public schema."""
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

import database
from controllers import ContractController
from errors import QIException
from .test_contract_controller import LedgerMock, RegistryMock, payload


@pytest.fixture
def pg_factory(monkeypatch):
    url = os.environ.get('CONTRACT_TEST_DATABASE_URL')
    if not url:
        pytest.skip('Set CONTRACT_TEST_DATABASE_URL to run PostgreSQL concurrency/trigger checks')
    schema = 'contract_test_' + uuid4().hex
    admin = create_engine(url)
    with admin.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA {schema}')
    engine = create_engine(url, connect_args={'options': f'-csearch_path={schema}'})
    try:
        sql = (Path(__file__).resolve().parents[2] / 'database' / 'database.sql').read_text(encoding='utf-8')
        sql = sql[sql.index('CREATE FUNCTION append_only_guard()'):]
        with engine.begin() as connection:
            with connection.connection.cursor() as cursor:
                cursor.execute(sql)
        factory = sessionmaker(bind=engine, autoflush=False)
        monkeypatch.setattr(database, 'SessionLocal', factory)
        yield factory
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA {schema} CASCADE')
        admin.dispose()


def build_controller(payload, shared=None):
    database.open_context()
    controller = ContractController()
    controller.registry = RegistryMock(controller.session, payload['parties'][2]['party_key'])
    controller.ledger = LedgerMock(controller.session)
    if shared:
        controller.ledger.operations = shared.operations
        controller.ledger.calls = shared.calls
        controller.ledger.lock = shared.lock
    return controller


def close_controller(controller):
    controller.session.close()
    database.clear_context()


def setup_contract(payload, active=False):
    controller = build_controller(payload)
    try:
        key = controller.create(payload, 'setup')['contract_key']
        if active:
            controller.activate(key, 'setup')
        return key, controller.ledger
    finally:
        close_controller(controller)


def test_concurrent_duplicate_reference(pg_factory, payload):
    barrier = Barrier(2)
    def worker():
        controller = build_controller(payload)
        original = controller.repository.get_by_external_reference
        def simultaneous_lookup(*args):
            result = original(*args)
            barrier.wait(timeout=10)
            return result
        controller.repository.get_by_external_reference = simultaneous_lookup
        try:
            return controller.create(payload, 'race')
        except QIException as error:
            return error.code
        finally:
            close_controller(controller)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: worker(), range(2)))
    assert results.count('QIT003002') == 1
    assert sum(isinstance(r, dict) for r in results) == 1


def test_concurrent_split_versions(pg_factory, payload):
    key, ledger = setup_contract(payload, active=True)
    barrier = Barrier(2)
    def worker():
        controller = build_controller(payload, ledger)
        try:
            barrier.wait(timeout=10)
            return controller.create_split_version(key, payload['split'], 'race')['version']
        finally:
            close_controller(controller)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: worker(), range(2)))
    assert sorted(results) == [2, 3]
    assert len(ledger.operations) == 3


def test_concurrent_activation(pg_factory, payload):
    key, ledger = setup_contract(payload)
    barrier = Barrier(2)
    def worker():
        controller = build_controller(payload, ledger)
        original = controller.registry.get_client
        def after_snapshot(client_key):
            barrier.wait(timeout=10)
            return original(client_key)
        controller.registry.get_client = after_snapshot
        try:
            return controller.activate(key, 'race')['status']
        except QIException as error:
            return error.code
        finally:
            close_controller(controller)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: worker(), range(2)))
    assert sorted(results) == ['QIT003003', 'active']
    assert len(ledger.operations) == 1


def test_version_racing_closure(pg_factory, payload):
    key, ledger = setup_contract(payload, active=True)
    barrier = Barrier(2)
    def worker(operation):
        controller = build_controller(payload, ledger)
        try:
            barrier.wait(timeout=10)
            if operation == 'split':
                return controller.create_split_version(key, payload['split'], 'race')
            return controller.terminate(key, 'race')
        except QIException as error:
            return error.code
        finally:
            close_controller(controller)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(worker, ['split', 'closure']))
    assert results[1]['status'] == 'terminated'
    assert results[0] == 'QIT003003' or results[0]['version'] == 2
    assert ledger.calls[-1][0] == 'closure'
    controller = build_controller(payload, ledger)
    try:
        final = controller.get_by_key(key)
        assert final['status'] == 'terminated'
        assert len(final['split_versions']) == (1 if results[0] == 'QIT003003' else 2)
    finally:
        close_controller(controller)


def test_append_only_history_and_numeric_precision(pg_factory, payload):
    payload['split']['rent_amount_in_cents'] = 99999999999999999999
    key, _ = setup_contract(payload)
    controller = build_controller(payload)
    try:
        assert controller.get_by_key(key)['split_versions'][0]['rent_amount_in_cents'] == 99999999999999999999
    finally:
        close_controller(controller)
    for sql in ("UPDATE contract_status_event SET actor='modified'", 'DELETE FROM contract_status_event',
                'TRUNCATE contract_status_event'):
        with pytest.raises(DBAPIError, match='append-only'):
            with pg_factory.kw['bind'].begin() as connection:
                connection.exec_driver_sql(sql)
