# Contratos

O `ContractController` concentra as regras e coordena Cadastro, Ledger e banco.
Repositories persistem sem commit; conectores transportam HTTP; resources leem JSON
preservando percentuais com Decimal. Os schemas usam JSON Schema 2020-12.

## Operações

Todas as rotas exigem `INTERNAL-TOKEN`. Escritas recebem `actor` e `reason` opcional;
`trace_id` vem de `X-Request-ID` ou do identificador gerado pelo middleware.

| Método e rota | Resultado |
|---|---|
| POST /contracts | 201, contract_key; cria draft com split v1 |
| GET /contracts/{contract_key} | 200, contrato, partes, versões e eventos |
| GET /contracts?limit=10&offset=0&status=active | 200, contracts e is_last_page |
| POST /contracts/{contract_key}/activation | 200, contract_key e status |
| POST /contracts/{contract_key}/split_versions | 201, contract_key e version |
| POST /contracts/{contract_key}/closure | 200, contract_key e status |

A listagem também aceita `client_key`, `external_reference` e vários `status`.
Os estados são draft → active → terminated. Versões novas exigem active.
Não há edição das partes ou versões existentes neste fluxo.

Exemplo de criação (as chaves precisam corresponder aos registros no Cadastro):

```json
{
  "client_key": "00000000-0000-0000-0000-000000000001",
  "external_reference": "ERP-101",
  "property_reference": "APT-101",
  "start_date": "2026-01-01",
  "actor": "operator@example.com",
  "parties": [
    {"party_key": "00000000-0000-0000-0000-000000000002", "role": "tenant"},
    {"party_key": "00000000-0000-0000-0000-000000000003", "role": "landlord"},
    {"party_key": "00000000-0000-0000-0000-000000000004", "role": "administrator"}
  ],
  "split": {
    "rent_amount_in_cents": 125000,
    "items": [
      {"purpose_code": "RENT", "party_key": "00000000-0000-0000-0000-000000000003", "role": "landlord", "allocation_type": "remainder"},
      {"purpose_code": "OWN", "party_key": "00000000-0000-0000-0000-000000000004", "role": "administrator", "allocation_type": "percentage", "percentage": "10.0000"}
    ]
  }
}
```

Ativação e encerramento recebem `{"actor":"operator@example.com"}`.
Nova versão recebe `{"actor":"operator@example.com","split":{...}}`, com o split completo.
Percentuais aceitam números JSON ou strings decimais; respostas usam strings.
Dinheiro é inteiro em centavos. OWN e despesas são opcionais; RENT é obrigatório.
A soma de percentuais do split deve ser menor que 100.

## Integrações provisórias — documento 01b pendente

Configurar `REGISTRY_API_URL`, `REGISTRY_API_INTERNAL_TOKEN`, `REGISTRY_API_TIMEOUT`,
`LEDGER_API_URL`, `LEDGER_API_INTERNAL_TOKEN` e `LEDGER_API_TIMEOUT`.
No Docker, os defaults apontam para mocks no host nas portas 8081 e 8082.
Ao executar diretamente no Windows, use URLs com localhost.

Este contrato de adaptação é uma premissa testada com mocks, não uma confirmação do 01b:

- GET /clients/{key}: 200 com `status`, `party_key`, `platform_fee_percentage`; 404 para ausente.
- GET /parties/{key}: 200 com `status`; 404 para ausente. Atividade é `status = active`.
- POST /contract_ledgers: recebe `contract_key`, `client_key`, `version`, `parties`,
  `platform_fee_percentage` e `split`. 200/201 com `contract_ledger_key` confirma provisionamento.
- POST /contract_ledgers/{contract_ledger_key}/closure: 200/204 confirma fechamento.
- 409 com `code = LEDGER_CONFLICT` é conflito. 422 com `code = LEDGER_REFUSED_SPLIT`
  é recusa de provisionamento. Outros 4xx não são convertidos indiscriminadamente.
- Timeout, falha de transporte, 429 e 5xx resultam em DependencyUnavailable.

O cliente é consultado na criação e ativação. Cada parte distinta é consultada na ativação.
Todas essas chamadas ocorrem fora de transações locais. Ativação salva a identidade
do Ledger e o snapshot da taxa da plataforma; versões novas usam esse snapshot.
A taxa não é item do split e não entra na soma dos percentuais nesta implementação.

O Ledger é chamado sob bloqueio do contrato, antes das escritas locais.
São enviados `X-Request-ID` e `Idempotency-Key`:
`{contract_key}:split:{version}` ou `{contract_key}:closure`.
Payloads de split têm ordenação e representação decimal estáveis.
Não existem retries automáticos de POST.

Antes de usar serviços reais, confirmar payloads, status, códigos, identidade e idempotência
com o 01b. A recusa específica de fechamento permanece sem código de domínio definido;
respostas desconhecidas são registradas e retornam InternalError.

Se o Ledger confirmar e o commit falhar, o rollback local não desfaz o efeito externo.
O log `LEDGER_CONFIRMED_LOCAL_FAILURE` identifica operação e trace_id para reconciliação.
Repetir a mesma operação só é seguro quando a idempotência do Ledger estiver garantida.
Timeout também pode ocorrer depois de o serviço remoto executar a operação.

## Banco existente

Banco novo usa `database/database.sql`, incluindo tabelas, seeds e triggers append-only.
Se as tabelas de contratos já existem, aplicar `database/migrations/001_contract_ledger_metadata.sql`
antes de utilizar esta versão. Ela adiciona `ledger_key` e `platform_fee_percentage`.
Se ainda não existem, aplicar primeiro o bloco CONTRATO de database.sql.
Não recriar ou apagar o banco para instalar a mudança. Nenhuma migração é executada no startup.

Contratos antigos em active sem esses metadados precisam de reconciliação com o Ledger;
não preencher as colunas inventando uma confirmação.

## Erros

QIT003001–QIT003013 têm usos e testes nas regras do controller. Foram acrescentados
QIT003014 (cliente ausente, 422) e QIT003015 (cliente inativo, 409), pois cliente e parte
são conceitos diferentes. Falhas de schema retornam 400/QIT000001.

## Testes no PowerShell

```powershell
.\.venv\Scripts\python.exe -m pytest tests/contracts -q
```

Os testes locais usam SQLite descartável e mocks; não exigem Cadastro/Ledger em execução.
Testes ASGI exercitam os resources e middlewares da aplicação.
Para incluir os testes de infraestrutura e healthcheck, execute `python -m pytest -q`.
Para incluir concorrência, triggers e precisão de NUMERIC no PostgreSQL:

```powershell
$env:CONTRACT_TEST_DATABASE_URL = $env:DATABASE_URL
.\.venv\Scripts\python.exe -m pytest tests/contracts -q
```

Defina a variável com a URL do banco de desenvolvimento se DATABASE_URL não estiver
no ambiente do PowerShell. Os testes PostgreSQL criam e removem somente schemas
`contract_test_*` próprios; o schema public e seus dados não são resetados.
