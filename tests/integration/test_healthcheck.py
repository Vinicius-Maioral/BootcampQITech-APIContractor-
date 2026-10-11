from app import create_app
from tests.contracts.test_contract_http import request


class TestHealthCheck:
    def test_home(self):
        assert request(create_app(), 'GET', '/', token=False)[0] == 200

    def test_health_check(self):
        assert request(create_app(), 'GET', '/health_check', token=False)[0] == 204

    def test_no_token(self):
        status, body = request(create_app(), 'PUT', '/sample', token=False)
        assert (status, body['code']) == (403, 'QIT000002')

    def test_sink(self):
        status, body = request(create_app(), 'PUT', '/sample')
        assert (status, body['code']) == (404, 'QIT000404')

    def test_method_not_allowed(self):
        status, body = request(create_app(), 'PUT', '/health_check')
        assert (status, body['code']) == (405, 'QIT000405')

    def test_error_translation_keeps_accents(self):
        status, forbidden = request(create_app(), 'PUT', '/sample', token=False)
        assert status == 403
        assert forbidden['translation'] == 'Requisição precisa ser interna'
        status, missing = request(create_app(), 'PUT', '/sample')
        assert status == 404
        assert 'não pode ser encontrado' in missing['translation']
