import os


SERVICE_ROOT = os.path.abspath(os.path.dirname(__file__))

# Onde moram os arquivos de schema — os .json que descrevem o formato
# que cada requisicao precisa ter. Quem le essa pasta e o
# src/utils/schema_handler.py.
SCHEMA_PATH = os.path.join(SERVICE_ROOT, "schemas")

APP_ENV = os.environ.get("APP_ENV", "local")
SERVICE_NAME = os.environ.get("SERVICE_NAME", "bootcamp-api")

DATABASE_URL = os.environ.get("DATABASE_URL")
INTERNAL_TOKEN = os.environ.get("INTERNAL_TOKEN")

# 01b: use localhost outside Docker; Compose supplies internal service URLs.
PARTY_API_URL = os.environ.get("PARTY_API_URL", "http://localhost:3001")
PARTY_API_INTERNAL_TOKEN = os.environ.get("PARTY_API_INTERNAL_TOKEN", "default_token")
PARTY_API_TIMEOUT = int(os.environ.get("PARTY_API_TIMEOUT", "3"))
LEDGER_API_URL = os.environ.get("LEDGER_API_URL", "http://localhost:3003")
LEDGER_API_INTERNAL_TOKEN = os.environ.get("LEDGER_API_INTERNAL_TOKEN", "default_token")
LEDGER_API_TIMEOUT = int(os.environ.get("LEDGER_API_TIMEOUT", "5"))

# Rotas públicas: não exigem o header INTERNAL-TOKEN. São as duas que
# precisam responder pra quem ainda não tem token nenhum: a raiz, que
# diz quem é este serviço, e o health check, que o Docker consulta pra
# saber se a API já está de pé.
BYPASS_ENDPOINTS = [
    "/",
    "/health_check",
]

REQUIRED_VARIABLES = ["DATABASE_URL", "INTERNAL_TOKEN"]


def check_variables():
    missing = []
    for name in REQUIRED_VARIABLES:
        if not globals().get(name):
            missing.append(name)

    if missing:
        raise EnvironmentError(
            f"Faltam variáveis de ambiente: {', '.join(missing)}. "
            "Rodando com 'docker compose up' elas já vêm preenchidas. "
            "Fora do Docker, copie o .env.example para .env."
        )
