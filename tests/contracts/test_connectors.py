from decimal import Decimal
from types import SimpleNamespace

from connectors.ledger_connector import LedgerConnector
from connectors.registry_connector import RegistryConnector
from utils.request_context import set_request_id


def test_connectors_send_configured_requests_and_preserve_decimals(monkeypatch):
    calls = []
    def send(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return SimpleNamespace(status_code=200, content=b'{"platform_fee_percentage":2.1234}')
    monkeypatch.setattr('connectors.rest_connector.requests.request', send)
    set_request_id('connector-test')
    try:
        registry = RegistryConnector()
        response = registry.get_client('client-key')
        assert response.json['platform_fee_percentage'] == Decimal('2.1234')
        registry.get_party('party-key')
        ledger = LedgerConnector()
        ledger.provision_contract_ledger({'split_version': 1})
        ledger.close_contract_ledger('contract/key', {'actor':'contract-api'})
        assert calls[0][1] == registry.base_url + '/clients/client-key'
        assert calls[1][1] == registry.base_url + '/parties/party-key'
        assert calls[2][0] == 'POST'
        assert 'Idempotency-Key' not in calls[2][2]['headers']
        assert calls[3][1] == ledger.base_url + '/contract_ledgers/contract%2Fkey/closure'
        assert all(c[2]['timeout'] > 0 for c in calls)
        assert all(c[2]['headers']['X-Request-ID'] == 'connector-test' for c in calls)
        assert all('INTERNAL-TOKEN' in c[2]['headers'] for c in calls)
    finally:
        set_request_id('-')
