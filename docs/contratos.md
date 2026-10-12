# API de contratos — integração 01b

O controller coordena as regras, o Cadastro e o Ledger. Repositories persistem
sem commit; connectors transportam HTTP. Os schemas validam entradas públicas.

## Rotas e formatos

Todas as rotas de contratos exigem INTERNAL-TOKEN. Escritas recebem actor e
reason opcional. X-Request-ID é propagado para as dependências e para os logs.
Chaves públicas são UUID v4; dinheiro é inteiro em centavos; percentual é string
com até quatro casas decimais. Instantes saem em UTC com Z.

| Método e rota | Resultado |
|---|---|
| POST /contracts | 201, contract_key e status draft |
| POST /contracts/{key}/activation | 200, contract_key, status, split_version, ledger_accounts |
| POST /contracts/{key}/split_versions | 201 nova versão; 200 conclusão de pendente; mesmos campos da ativação, mais version |
| POST /contracts/{key}/termination | 200, contract_key e status terminated |
| GET /contracts/{key} | contrato, parties, split_version, provisioned_at, rent_amount_in_cents, split e status_events |
| GET /contracts | data, limit, page e is_last_page |

O formato de criação está em [examples/contract.json](../examples/contract.json).
O aluguel fica na raiz e split é uma lista de itens com purpose_code, party_key,
role, type e value quando aplicável. Percentage exige value string; fixed exige
value inteiro; remainder não aceita value. O formato anterior não é aceito.

Ativação e encerramento recebem {"actor":"erp-alfa"}, com reason opcional.
Reajuste recebe actor, reason opcional, rent_amount_in_cents e a lista split completa.

A criação valida o schema, consulta apenas o cliente e depois valida as regras
de negócio, antes da transação. Uma transação grava draft, evento inicial,
participações e versão 1 não provisionada. Partes pending podem constar no
rascunho; todas precisam estar active na ativação. Participações insuficientes
retornam 422 QIT003004; estrutura inválida continua retornando 400 QIT000001.

Exige-se um tenant, um landlord, um administrator e zero ou mais providers.
Landlord deve diferir de tenant; administrator deve corresponder ao cliente.
RENT é remainder do landlord; OWN é percentage do administrator; despesas são
fixed de providers. Cada beneficiário deve constar nas participações com o papel.
Exatamente um remainder; soma dos percentuais menor que 100. A comparação entre
a taxa da plataforma e a administração é feita pelo Ledger no provisionamento,
sem impedir a criação do rascunho. Se o Ledger recusar com 422 QIT001034, o
Contrato responde 422 QIT003009 com QIT001034 na description. Sem OWN, a
administração é considerada zero pelo Ledger.

Erros traduzidos das dependências preservam o código de origem na description
quando ele estiver presente na resposta. Timeouts e respostas sem código não
geram um código de origem fictício.

Ativação de contrato active retorna 200 sem consultar dependências, com
contract_key, status e split_version, omitindo ledger_accounts. Terminated não
pode ser ativado. A ativação confirma primeiro o Ledger e depois faz o commit local.
As contas vêm de accounts na resposta externa e não são armazenadas no Contrato.

Reajustes exigem active. Uma nova versão é persistida pendente antes do Ledger.
Falha mantém a pendente; reenvio igual conclui essa versão, sem criar outra.
A igualdade normaliza aluguel e itens, ignorando ordem, actor, reason e request ID.
Conteúdo diferente retorna 409 QIT003013. O reajuste reutiliza a taxa da plataforma
da ativação e a participação administrator como client_party_key; não consulta Cadastro.

O GET apresenta a maior versão provisionada. Draft apresenta versão 1; terminated
sem provisionamento também apresenta versão 1, com provisioned_at nulo. Versões
pendentes posteriores não substituem a versão vigente.

A listagem exige uma alternativa: party_key OU client_key + external_reference.
Filtros incompletos, misturados ou ausentes geram 400 QIT000001. page começa em
0; limit padrão 10, máximo 100. No filtro por parte, roles lista seus papéis sem
duplicar o contrato. A busca natural devolve zero ou um contrato, permitindo ao ERP
recuperar a chave depois de um timeout no POST. offset e status não são filtros públicos.

## Dependências e erros

Cadastro: GET /clients/{client_key} na criação e ativação; GET /parties/{party_key}
para cada participação apenas na ativação. Cliente ou parte inexistente gera
422 QIT003008; status diferente de active gera 409 QIT003007. Timeout/5xx gera
503 QIT003010.

Ledger: POST /contract_ledgers recebe contract_key, client_key, client_party_key,
split_version, rent_amount_in_cents, platform_fee_percentage, items, actor e reason.
Items usa beneficiary_party_key, beneficiary_role, type e value. Percentuais são
enviados com quatro casas. Taxa da plataforma não é item e não desconta o aluguel
do landlord. Expected amount é aluguel mais valores fixed.

O Ledger confirma com 201, ou 200 no replay, devolvendo contract_key, split_version,
expected_amount_in_cents e accounts. A idempotência pertence ao par contrato/versão
com comparação semântica de itens, aluguel e taxa; não depende de Idempotency-Key.

| Erro externo no provisionamento | Tradução |
|---|---|
| 400 QIT000001 | 500: erro de integração do Contrato |
| 409 QIT001032 ou QIT001033 | 409 QIT003013 |
| 422 QIT001008, QIT001028 ou QIT001034 | 422 QIT003009 |
| Timeout ou 5xx | 503 QIT003010 |

Closure externo é POST /contract_ledgers/{contract_key}/closure, com actor e reason.
200 ou 404 QIT001035 permite encerrar draft ou active. 409 QIT001037 vira
409 QIT003013; 409 QIT001011 vira 503 QIT003010; timeout/5xx vira 503 QIT003010.
Falhas preservam o estado local. Encerramento local repetido retorna 409 QIT003003.

## Transações e concorrência

Nenhuma chamada externa ocorre com transação SQL aberta. Operações mutáveis de
um mesmo contrato são serializadas por advisory lock de sessão PostgreSQL em
conexão dedicada sem pool; espera limitada a 5 segundos gera 503 QIT003010.
O bloqueio cobre leitura, chamada externa e commit, sendo liberado mesmo em erro.
SQLite usa bloqueio local apenas nos testes isolados.

Confirmação do Ledger seguida de falha de commit gera LEDGER_CONFIRMED_LOCAL_FAILURE.
Resposta externa confirmada mas inválida gera LEDGER_CONFIRMED_INVALID_RESPONSE.
Rollback SQL não desfaz o Ledger. Reenvie a operação com o mesmo conteúdo para
recuperar resultados desconhecidos; não existem retries automáticos de POST.

## Docker e banco, sem migrations

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start-local.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/demo-contract.ps1
docker compose ps
```

O script salva pg_dump em backups, preserva e verifica o volume PostgreSQL, aplica
SQL e verifica models em transação. Não use down -v para preparar o ambiente.
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
