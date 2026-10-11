# API de contratos ? integra??o 01b

O controller coordena as regras, o Cadastro e o Ledger. Repositories persistem
sem commit; connectors transportam HTTP. Os schemas validam entradas p?blicas.

## Rotas e formatos

Todas as rotas de contratos exigem INTERNAL-TOKEN. Escritas recebem actor e
reason opcional. X-Request-ID ? propagado para as depend?ncias e para os logs.
Chaves p?blicas s?o UUID v4; dinheiro ? inteiro em centavos; percentual ? string
com at? quatro casas decimais. Instantes saem em UTC com Z.

| M?todo e rota | Resultado |
|---|---|
| POST /contracts | 201, contract_key e status draft |
| POST /contracts/{key}/activation | 200, contract_key, status, split_version, ledger_accounts |
| POST /contracts/{key}/split_versions | 201 nova vers?o; 200 conclus?o de pendente; mesmos campos da ativa??o |
| POST /contracts/{key}/termination | 200, contract_key e status terminated |
| GET /contracts/{key} | contrato, parties, split_version, provisioned_at, rent_amount_in_cents, split e status_events |
| GET /contracts | contracts e is_last_page |

O formato de cria??o est? em [examples/contract.json](../examples/contract.json).
O aluguel fica na raiz e split ? uma lista de itens com purpose_code, party_key,
role, type e value quando aplic?vel. Percentage exige value string; fixed exige
value inteiro; remainder n?o aceita value. O formato anterior n?o ? aceito.

Ativa??o e encerramento recebem {"actor":"erp-alfa"}, com reason opcional.
Reajuste recebe actor, reason opcional, rent_amount_in_cents e a lista split completa.

A criação valida o schema, consulta apenas o cliente e depois valida as regras
de negócio, antes da transação. Uma transação grava draft, evento inicial,
participações e versão 1 não provisionada. Partes pending podem constar no
rascunho; todas precisam estar active na ativação. Participações insuficientes
retornam 422 QIT003004; estrutura inválida continua retornando 400 QIT000001.

Exige-se um tenant, um landlord, um administrator e zero ou mais providers.
Landlord deve diferir de tenant; administrator deve corresponder ao cliente.
RENT ? remainder do landlord; OWN ? percentage do administrator; despesas s?o
fixed de providers. Cada benefici?rio deve constar nas participa??es com o papel.
Exatamente um remainder; soma dos percentuais menor que 100. A comparação entre
a taxa da plataforma e a administração é feita pelo Ledger no provisionamento,
sem impedir a criação do rascunho. Se o Ledger recusar com 422 QIT001034, o
Contrato responde 422 QIT003009 com QIT001034 na description. Sem OWN, a
administração é considerada zero pelo Ledger.

Erros traduzidos das dependências preservam o código de origem na description
quando ele estiver presente na resposta. Timeouts e respostas sem código não
geram um código de origem fictício.

Ativa??o de contrato active retorna 200 sem consultar depend?ncias, com
contract_key, status e split_version, omitindo ledger_accounts. Terminated n?o
pode ser ativado. A ativa??o confirma primeiro o Ledger e depois faz o commit local.
As contas v?m de accounts na resposta externa e n?o s?o armazenadas no Contrato.

Reajustes exigem active. Uma nova vers?o ? persistida pendente antes do Ledger.
Falha mant?m a pendente; reenvio igual conclui essa vers?o, sem criar outra.
A igualdade normaliza aluguel e itens, ignorando ordem, actor, reason e request ID.
Conte?do diferente retorna 409 QIT003013. O reajuste reutiliza a taxa da plataforma
da ativa??o e a participa??o administrator como client_party_key; n?o consulta Cadastro.

O GET apresenta a maior vers?o provisionada. Draft apresenta vers?o 1; terminated
sem provisionamento tamb?m apresenta vers?o 1, com provisioned_at nulo. Vers?es
pendentes posteriores n?o substituem a vers?o vigente.

A listagem exige uma alternativa: party_key OU client_key + external_reference.
Filtros incompletos, misturados ou ausentes geram 400 QIT000001. page come?a em
0; limit padr?o 10, m?ximo 100. No filtro por parte, roles lista seus pap?is sem
duplicar o contrato. A busca natural devolve zero ou um contrato, permitindo ao ERP
recuperar a chave depois de um timeout no POST. offset e status n?o s?o filtros p?blicos.

## Depend?ncias e erros

Cadastro: GET /clients/{client_key} na cria??o e ativa??o; GET /parties/{party_key}
para cada participa??o apenas na ativa??o. Cliente ou parte inexistente gera
422 QIT003008; status diferente de active gera 409 QIT003007. Timeout/5xx gera
503 QIT003010. Os antigos QIT003014/15 n?o s?o emitidos nesses fluxos.

Ledger: POST /contract_ledgers recebe contract_key, client_key, client_party_key,
split_version, rent_amount_in_cents, platform_fee_percentage, items, actor e reason.
Items usa beneficiary_party_key, beneficiary_role, type e value. Percentuais s?o
enviados com quatro casas. Taxa da plataforma n?o ? item e n?o desconta o aluguel
do landlord. Expected amount ? aluguel mais valores fixed.

O Ledger confirma com 201, ou 200 no replay, devolvendo contract_key, split_version,
expected_amount_in_cents e accounts. A idempot?ncia pertence ao par contrato/vers?o
com compara??o sem?ntica de itens, aluguel e taxa; n?o depende de Idempotency-Key.

| Erro externo no provisionamento | Tradu??o |
|---|---|
| 400 QIT000001 | 500: erro de integra??o do Contrato |
| 409 QIT001032 ou QIT001033 | 409 QIT003013 |
| 422 QIT001008, QIT001028 ou QIT001034 | 422 QIT003009 |
| Timeout ou 5xx | 503 QIT003010 |

Closure externo ? POST /contract_ledgers/{contract_key}/closure, com actor e reason.
200 ou 404 QIT001035 permite encerrar draft ou active. 409 QIT001037 vira
409 QIT003013; 409 QIT001011 vira 503 QIT003010; timeout/5xx vira 503 QIT003010.
Falhas preservam o estado local. Encerramento local repetido retorna 409 QIT003003.

## Transa??es e concorr?ncia

Nenhuma chamada externa ocorre com transa??o SQL aberta. Opera??es mut?veis de
um mesmo contrato s?o serializadas por advisory lock de sess?o PostgreSQL em
conex?o dedicada sem pool; espera limitada a 5 segundos gera 503 QIT003010.
O bloqueio cobre leitura, chamada externa e commit, sendo liberado mesmo em erro.
SQLite usa bloqueio local apenas nos testes isolados.

Confirma??o do Ledger seguida de falha de commit gera LEDGER_CONFIRMED_LOCAL_FAILURE.
Resposta externa confirmada mas inv?lida gera LEDGER_CONFIRMED_INVALID_RESPONSE.
Rollback SQL n?o desfaz o Ledger. Reenvie a opera??o com o mesmo conte?do para
recuperar resultados desconhecidos; n?o existem retries autom?ticos de POST.

## Docker e banco, sem migrations

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start-local.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/demo-contract.ps1
docker compose ps
```

O script salva pg_dump em backups, preserva e verifica o volume PostgreSQL, aplica
SQL e verifica models em transa??o. N?o use down -v para preparar o ambiente.
As portas no host são 3002 (Contrato), 5434 (PostgreSQL), 8091 (Cadastro mock) e
8093 (Ledger mock). A API usa db:5432 para o banco, party-mock:8080 e
ledger-mock:8080 para os mocks. Para serviços reais, configure as URLs com
host.docker.internal:3001 e host.docker.internal:3003. Os timeouts normais
são 3 segundos e 5 segundos. 8081 e 8082 ficam reservadas ao bureau e Pix.

Os mocks usam WireMock 3.9.1 com os 30 mappings oficiais, sem alterações.
Consulte [mocks.md](mocks.md) para origem, hashes, gatilhos e limitações.
As respostas do Ledger têm valor esperado e contas fixos. O controller valida
seus tipos e identidades, sem comparar o valor esperado ao cálculo local.

## Testes

```powershell
docker compose up
# Em outro terminal:
pytest
```

As fixtures do pytest configuram timeouts de 1 segundo para os atrasos oficiais de 2 segundos
e restaura a configuração anterior em finally. Os cenários compartilhados são
reiniciados antes e depois de cada teste. Os headers e bodies são conferidos no
diário HTTP real do WireMock. PostgreSQL usa schemas temporários isolados.
