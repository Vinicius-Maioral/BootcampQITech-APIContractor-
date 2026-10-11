"""Session-scoped Docker preparation, shared by pytest on Windows and Linux."""
import json
import os
from contextlib import contextmanager
from pathlib import Path
import subprocess
import time

from sqlalchemy.engine import make_url


ROOT = Path(__file__).resolve().parents[2]
API_SETTINGS = ('PARTY_API_URL', 'LEDGER_API_URL', 'PARTY_API_TIMEOUT', 'LEDGER_API_TIMEOUT')
TEST_SETTINGS = dict(zip(API_SETTINGS, ('http://party-mock:8080', 'http://ledger-mock:8080', '1', '1')))


class DockerEnvironment:
    def __init__(self, root=ROOT):
        self.root = Path(root)

    def run(self, *arguments, overrides=None, timeout=120):
        environment = os.environ.copy()
        environment.update(overrides or {})
        try:
            result = subprocess.run(
                ['docker', *arguments], cwd=self.root, env=environment,
                capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise RuntimeError(f'Docker command unavailable or timed out: {arguments[:3]}') from error
        if result.returncode:
            # Do not print Compose configuration: it can contain credentials.
            raise RuntimeError(f'Docker command failed: {arguments[:3]} (exit {result.returncode})')
        return result.stdout

    def inspect(self, service):
        identifier = self.run('compose', 'ps', '-a', '-q', service).strip()
        if not identifier:
            return None
        return json.loads(self.run('inspect', identifier))[0]

    def wait_healthy(self, service, timeout=120):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            container = self.inspect(service)
            if container:
                state = container['State']
                health = state.get('Health', {}).get('Status')
                if state['Status'] == 'running' and health == 'healthy':
                    return container
                if state['Status'] in ('exited', 'dead') or health == 'unhealthy':
                    raise RuntimeError(f'Docker service {service} failed: {state["Status"]}, health={health}')
            time.sleep(1)
        raise RuntimeError(f'Docker service {service} did not become healthy within {timeout}s; run docker compose up')

    @staticmethod
    def environment(container):
        return dict(entry.split('=', 1) for entry in container['Config']['Env'])

    @staticmethod
    def published_port(container, target):
        bindings = container['NetworkSettings']['Ports'].get(f'{target}/tcp')
        if not bindings:
            raise RuntimeError(f'Container has no published port for {target}/tcp')
        return bindings[0]['HostPort']

    def recreate_api(self, settings):
        self.run('compose', 'up', '-d', '--no-deps', 'api', overrides=settings)
        return self.wait_healthy('api')

    @contextmanager
    def prepared(self):
        if os.environ.get('PYTEST_XDIST_WORKER'):
            raise RuntimeError('Shared WireMock scenarios require a sequential pytest run')
        with suite_lock(self.root / '.pytest_cache' / 'wiremock.lock'):
            containers = {name: self.wait_healthy(name) for name in ('db', 'party-mock', 'ledger-mock', 'api')}
            original = self.environment(containers['api'])
            if any(name not in original for name in API_SETTINGS):
                raise RuntimeError('Running API is missing dependency URLs or timeouts')
            restore = {name: original[name] for name in API_SETTINGS}
            changed = False
            try:
                changed = True  # Restore even if recreation partially fails.
                containers['api'] = self.recreate_api(TEST_SETTINGS)
                config = json.loads(self.run('compose', 'config', '--format', 'json', overrides=TEST_SETTINGS))
                for name, target in (('api', 3000), ('db', 5432), ('party-mock', 8080), ('ledger-mock', 8080)):
                    config['services'][name]['ports'] = [{'published': self.published_port(containers[name], target), 'target': target}]
                api_environment = self.environment(containers['api'])
                config['services']['api']['environment'] = api_environment
                database_url = make_url(api_environment['DATABASE_URL']).set(
                    host='localhost', port=int(self.published_port(containers['db'], 5432)),
                ).render_as_string(hide_password=False)
                yield {'config': config, 'database_url': database_url}
            finally:
                if changed:
                    try:
                        self.recreate_api(restore)
                    except Exception as error:
                        raise RuntimeError('Failed to restore API configuration after pytest') from error


@contextmanager
def suite_lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as handle:
        if handle.tell() == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        acquired = False
        try:
            try:
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError as error:
                raise RuntimeError('Another pytest suite is using the shared WireMock servers') from error
            yield
        finally:
            if acquired:
                handle.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
