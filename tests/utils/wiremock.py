"""WireMock's real administrative HTTP API; no simulator of its behavior."""
import json

import requests


class WireMock:
    def __init__(self, base_url):
        self.base_url = base_url

    def admin(self, method, path):
        response = requests.request(method, self.base_url + '/__admin' + path, timeout=10)
        response.raise_for_status()
        return response

    def reset(self):
        # Reload original mappings, clear the journal and return scenarios to Started.
        self.admin('POST', '/reset')

    def journal(self):
        return [entry['request'] for entry in self.admin('GET', '/requests').json()['requests']]

    def mappings(self):
        return self.admin('GET', '/mappings').json()

    @staticmethod
    def header(request, name):
        for key, values in request['headers'].items():
            if key.lower() == name.lower():
                return values[0] if isinstance(values, list) else values
        return None

    @staticmethod
    def body(request):
        return json.loads(request['body'])
