# Mocks oficiais de Cadastro e Ledger

Os serviços `party-mock` e `ledger-mock` executam `wiremock/wiremock:3.9.1`.
As cópias locais em `tests/mocks` são a fonte usada pelo Docker e pelos testes.
O simulador anterior em Python foi substituído pelos arquivos oficiais.

## Origem e atualização

Os arquivos vêm de `bootcamp-qi-tech-docs/docs/mocks/party-api/mappings` e
`bootcamp-qi-tech-docs/docs/mocks/ledger-api/mappings`, fornecidos pelo projeto.
São 21 mappings de Cadastro e 9 de Ledger. `tests/mocks/manifest.json` registra
os nomes e hashes SHA-256 recebidos. `.gitattributes` preserva seus bytes.

Para atualizar, revise a nova versão oficial, copie os arquivos sem alterar
corpos ou cenários e atualize o manifesto com os hashes dos arquivos de origem.
Confirme a quantidade e execute os testes de fidelidade e HTTP. O clone externo
serve como origem da atualização; não é necessário para executar a aplicação.

## Ambiente

| Serviço | URL na rede Docker | Porta padrão no host |
|---|---|---:|
| Contrato | `api:3000` | 3002 |
| PostgreSQL | `db:5432` | 5434 |
| Cadastro mock | `party-mock:8080` | 8091 |
| Ledger mock | `ledger-mock:8080` | 8093 |

`PARTY_MOCK_PORT` e `LEDGER_MOCK_PORT` permitem trocar as portas publicadas.
O Compose aponta a API para os nomes internos dos mocks. Para integração real,
configure `PARTY_API_URL=http://host.docker.internal:3001` e
`LEDGER_API_URL=http://host.docker.internal:3003`.

Os timeouts normais continuam 3 segundos para Cadastro e 5 para Ledger.
Nos testes de falha, ambos devem ser 1 segundo, pois os mappings atrasam 2 segundos.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start-local.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/demo-contract.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/test-contracts.ps1
```

O executor de testes configura as URLs locais e os timeouts no container da API,
executa a suíte e restaura os valores anteriores em `finally`. Não recria o banco.
Usa um bloqueio de arquivo para impedir dois executores de reiniciar os mesmos
cenários simultaneamente. Os testes HTTP com mocks compartilhados são sequenciais.

## Gatilhos oficiais

| Requisição | Gatilho | Resultado |
|---|---|---|
| GET client | `c1c1…` | Alfa active, administradora `1111…`, taxa `1.0000` |
| GET client | `9c9c…` | Plataforma active, taxa `0.0000` |
| GET client | `c404…` ou chave desconhecida | 404 QIT002005 |
| GET client | `c503…` | Atraso de 2 segundos; 503 sem corpo |
| GET party | `aaaa…` | pending |
| GET party | `bbbb…` ou chave desconhecida | 404 QIT002001 |
| GET party | `cccc…` | Atraso de 2 segundos; 503 sem corpo |
| GET party | `dddd…` | active com pix_key nula |
| GET party | `eeee…` / `ffff…` | active com `timeout@mock` / `erro@mock` |
| POST contract_ledgers | aluguel 422008 | 422 QIT001008 |
| POST contract_ledgers | aluguel 409032 | 409 QIT001032 |
| POST contract_ledgers | aluguel 503503 | Atraso de 2 segundos; 503 sem corpo |
| POST contract_ledgers | aluguel 408200 | Primeiro 201 atrasado; depois 200 imediato |
| POST contract_ledgers | demais aluguéis | 201 |
| POST closure | reason `mock:saldo-retido` | 409 QIT001037 |
| POST closure | reason `mock:fora-do-ar` | Atraso de 2 segundos; 503 sem corpo |
| POST closure | demais reasons | 200 |

Os mappings de provisionamento devolvem sempre `expected_amount_in_cents: 640000`
e as oito contas do exemplo. Apenas `contract_key` e `split_version` ecoam a
requisição. Closure devolve `closed_accounts: 6`. Esses mocks não calculam valores,
persistem contas ou implementam a máquina de estados financeira real.

O controller valida os tipos, a identidade, a versão e as contas retornadas.
O total informado pelo Ledger não é comparado ao cálculo local, permitindo os
cenários oficiais de reajuste e replay com respostas fixas.

## Headers e diário de requisições

Os mappings não autenticam `INTERNAL-TOKEN`, nem validam `X-Request-ID` ou o body
completo. A API de Contrato mantém sua autenticação e seus schemas. Nos testes,
`GET /__admin/requests` comprova os headers e os campos enviados aos mocks.

O WireMock registra requisições e mantém o cenário `ativacao-408200` em memória.
`POST /__admin/reset` recarrega os mappings, limpa o diário e reinicia cenários.
`POST /__admin/scenarios/reset` reinicia somente os cenários. Os testes fazem reset
antes e depois de cada caso; não é necessário reiniciar containers para trocar respostas.

Os testes unitários e PostgreSQL mantêm a cobertura de idempotência semântica,
versões pendentes, concorrência, falhas de commit, 404 QIT001035 e 409 QIT001011,
que os mappings publicados não simulam. Nenhum mapping adicional foi inventado.

## Registro do acréscimo à base

1. Princípio tocado: o Compose da base sobe a API e seu banco.
2. Motivo: testar as integrações exige respostas HTTP das dependências.
3. Implementação: um WireMock por dependência, configurado por mappings JSON.
4. Limite: mocks de desenvolvimento e testes, sem estado financeiro persistente;
   não substituem a integração com serviços reais.

O PostgreSQL e seu volume continuam preservados. O volume SQLite do simulador
anterior permanece no Docker, sem uso e sem exclusão automática.
