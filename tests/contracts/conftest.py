import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))

from copy import deepcopy
import json
from uuid import uuid4

import pytest

from tests.utils.wiremock import WireMock
from tests.utils.docker_environment import DockerEnvironment


@pytest.fixture(scope='session')
def docker_environment():
    with DockerEnvironment().prepared() as environment:
        yield environment


@pytest.fixture(scope='session')
def docker_config(docker_environment):
    return docker_environment['config']


@pytest.fixture(scope='session')
def contract_database_url(docker_environment):
    return docker_environment['database_url']


@pytest.fixture
def official_mocks(docker_config):
    mocks = {name: WireMock('http://localhost:' + str(
        docker_config['services'][name + '-mock']['ports'][0]['published']))
        for name in ['party', 'ledger']}
    try:
        for mock in mocks.values():
            mock.reset()
        yield mocks
    finally:
        for mock in mocks.values():
            mock.reset()


@pytest.fixture
def live(docker_config, official_mocks):
    api = docker_config['services']['api']
    base = 'http://localhost:' + str(api['ports'][0]['published'])
    headers = {'INTERNAL-TOKEN': api['environment']['INTERNAL_TOKEN'],
               'X-Request-ID': 'docker-test-' + uuid4().hex}
    source = Path(__file__).resolve().parents[2] / 'examples/contract.json'
    payload = deepcopy(json.loads(source.read_text(encoding='utf-8')))
    payload['external_reference'] = 'DOCKER-' + uuid4().hex
    return base, headers, payload, docker_config, official_mocks
