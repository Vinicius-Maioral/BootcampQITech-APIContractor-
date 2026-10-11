from urllib.parse import quote

from connectors.rest_connector import RestConnector
from constants import LEDGER_API_URL, LEDGER_API_TIMEOUT, LEDGER_API_INTERNAL_TOKEN


class LedgerConnector(RestConnector):
    """01b adapter; idempotency belongs to contract_key and split_version."""

    def __init__(self):
        super().__init__(__name__, LEDGER_API_URL, LEDGER_API_TIMEOUT, LEDGER_API_INTERNAL_TOKEN)

    def provision_contract_ledger(self, payload):
        return self.send('/contract_ledgers', 'POST', payload)

    def close_contract_ledger(self, contract_key, payload):
        return self.send(f'/contract_ledgers/{quote(contract_key, safe="")}/closure', 'POST', payload)
