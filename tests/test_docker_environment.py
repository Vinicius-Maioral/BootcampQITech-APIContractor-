"""Preparation failure paths without running or changing Docker containers."""
from copy import deepcopy

import pytest

from tests.utils.docker_environment import DockerEnvironment, TEST_SETTINGS, suite_lock


def container(port, environment=None, status='running', health='healthy'):
    return {'State': {'Status': status, 'Health': {'Status': health}},
            'Config': {'Env': [f'{key}={value}' for key, value in (environment or {}).items()]},
            'NetworkSettings': {'Ports': {f'{port}/tcp': [{'HostPort': str(port + 1000)}]}}}


def fake_environment(tmp_path, monkeypatch):
    docker = DockerEnvironment(tmp_path)
    original = {**TEST_SETTINGS, 'PARTY_API_TIMEOUT': '3', 'LEDGER_API_TIMEOUT': '5',
                'DATABASE_URL': 'postgresql+psycopg2://user:p%40ss@db:5432/bootcamp'}
    containers = {'api': container(3000, original), 'db': container(5432),
                  'party-mock': container(8080), 'ledger-mock': container(8080)}
    monkeypatch.setattr(docker, 'wait_healthy', lambda service: containers[service])
    monkeypatch.setattr(docker, 'run', lambda *args, **kwargs: '{"services":{"api":{},"db":{},"party-mock":{},"ledger-mock":{}}}')
    calls = []

    def recreate(settings):
        calls.append(deepcopy(settings))
        return container(3000, {**original, **settings})
    monkeypatch.setattr(docker, 'recreate_api', recreate)
    return docker, original, calls


def test_preparation_uses_host_database_and_restores_after_test_failure(tmp_path, monkeypatch):
    docker, original, calls = fake_environment(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match='test failed'):
        with docker.prepared() as environment:
            from sqlalchemy.engine import make_url
            url = make_url(environment['database_url'])
            assert (url.host, url.port, url.password) == ('localhost', 6432, 'p@ss')
            assert environment['config']['services']['api']['environment']['LEDGER_API_TIMEOUT'] == '1'
            raise ValueError('test failed')
    assert calls == [TEST_SETTINGS, {name: original[name] for name in TEST_SETTINGS}]


def test_partial_preparation_failure_still_restores(tmp_path, monkeypatch):
    docker, original, calls = fake_environment(tmp_path, monkeypatch)

    def recreate(settings):
        calls.append(dict(settings))
        if len(calls) == 1:
            raise RuntimeError('startup failed')
        return container(3000, original)
    monkeypatch.setattr(docker, 'recreate_api', recreate)
    with pytest.raises(RuntimeError, match='startup failed'):
        with docker.prepared():
            pytest.fail('Preparation should not yield')
    assert calls[1] == {name: original[name] for name in TEST_SETTINGS}


def test_readiness_reports_unhealthy_and_timeout(tmp_path, monkeypatch):
    docker = DockerEnvironment(tmp_path)
    monkeypatch.setattr(docker, 'inspect', lambda service: container(3000, health='unhealthy'))
    with pytest.raises(RuntimeError, match='api failed'):
        docker.wait_healthy('api')
    with pytest.raises(RuntimeError, match='did not become healthy'):
        docker.wait_healthy('api', timeout=0)


def test_suite_lock_rejects_concurrent_owner_and_releases(tmp_path):
    path = tmp_path / 'suite.lock'
    with suite_lock(path):
        with pytest.raises(RuntimeError, match='Another pytest suite'):
            with suite_lock(path):
                pytest.fail('A second suite acquired the lock')
    with suite_lock(path):
        pass
