from urllib.parse import quote

from connectors.rest_connector import RestConnector
from constants import LEDGER_API_URL, LEDGER_API_TIMEOUT, LEDGER_API_INTERNAL_TOKEN


class LedgerConnector(RestConnector):
    """Provisional 01b adapter. No automatic retry of operations with external effects."""

    def __init__(self):
        super().__init__(__name__, LEDGER_API_URL, LEDGER_API_TIMEOUT, LEDGER_API_INTERNAL_TOKEN)

    def provision_contract_ledger(self, payload, operation_key):
        return self.send('/contract_ledgers', 'POST', payload, {'Idempotency-Key': operation_key})

    def close_contract_ledger(self, ledger_key, operation_key):
        return self.send(f'/contract_ledgers/{quote(ledger_key, safe="")}/closure', 'POST',
                         headers={'Idempotency-Key': operation_key})
