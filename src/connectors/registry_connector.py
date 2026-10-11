from connectors.rest_connector import RestConnector
from constants import PARTY_API_URL, PARTY_API_TIMEOUT, PARTY_API_INTERNAL_TOKEN


class RegistryConnector(RestConnector):
    def __init__(self):
        super().__init__(__name__, PARTY_API_URL, PARTY_API_TIMEOUT, PARTY_API_INTERNAL_TOKEN)

    def get_client(self, client_key):
        return self.send(f'/clients/{client_key}', 'GET')

    def get_party(self, party_key):
        return self.send(f'/parties/{party_key}', 'GET')
