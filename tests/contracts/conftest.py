import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))

from copy import deepcopy
import json
import os
import subprocess
from uuid import uuid4

import pytest

from tests.utils.wiremock import WireMock


@pytest.fixture(scope='session')
def docker_config():
    if os.environ.get('CONTRACT_DOCKER_TESTS') != '1':
        pytest.skip('Run scripts/test-contracts.ps1 for WireMock/Docker integration tests')
    if os.environ.get('PYTEST_XDIST_WORKER'):
        pytest.fail('Shared WireMock scenarios require a sequential test run')
    return json.loads(subprocess.check_output(
        ['docker', 'compose', 'config', '--format', 'json'], encoding='utf-8'))


@pytest.fixture
def official_mocks(docker_config):
    mocks = {name: WireMock('http://localhost:' + str(
        docker_config['services'][name + '-mock']['ports'][0]['published']))
        for name in ['party', 'ledger']}
    for mock in mocks.values():
        mock.reset()
    yield mocks
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
