CREATE OR REPLACE FUNCTION append_only_guard() RETURNS TRIGGER AS $func$
BEGIN
    RAISE EXCEPTION '% e append-only', TG_TABLE_NAME;
END;
$func$ LANGUAGE plpgsql;

CREATE TABLE IF NOT EXISTS contract_status(
    id              SERIAL PRIMARY KEY,
    enumerator      VARCHAR(50) NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT(NOW()),
    UNIQUE(enumerator)
);
INSERT INTO contract_status (enumerator) VALUES ('draft'), ('active'), ('terminated') ON CONFLICT (enumerator) DO NOTHING;

CREATE TABLE IF NOT EXISTS contract_role(
    id              SERIAL PRIMARY KEY,
    enumerator      VARCHAR(50) NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT(NOW()),
    UNIQUE(enumerator)
);
-- 'platform' NAO e papel de contrato: a taxa da plataforma e acordo
-- comercial com o cliente, nao clausula da locacao
INSERT INTO contract_role (enumerator) VALUES ('tenant'), ('landlord'), ('administrator'), ('provider') ON CONFLICT (enumerator) DO NOTHING;

CREATE TABLE IF NOT EXISTS contract(
    id                  SERIAL PRIMARY KEY,
    contract_key        CHAR(36) NOT NULL,
    client_key          CHAR(36) NOT NULL,          -- Cadastro; nao e FK
    contract_status_id  INTEGER NOT NULL REFERENCES contract_status(id),
    external_reference  VARCHAR(100) NOT NULL,      -- codigo no ERP: a chave natural
    property_reference  VARCHAR(255) NOT NULL,      -- opaco: APT-101
    ledger_key          VARCHAR(255),              -- confirmado pelo Ledger
    platform_fee_percentage NUMERIC(7,4),          -- snapshot na ativacao; nao e item do split
    start_date          DATE NOT NULL,
    end_date            DATE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT(NOW()),
    UNIQUE(contract_key),
    UNIQUE(client_key, external_reference)
);

CREATE TABLE IF NOT EXISTS contract_status_event(
    id              SERIAL PRIMARY KEY,
    contract_id     INTEGER NOT NULL REFERENCES contract(id),
    from_status_id  INTEGER REFERENCES contract_status(id),     -- nulo ao nascer
    to_status_id    INTEGER NOT NULL REFERENCES contract_status(id),
    actor           VARCHAR(255) NOT NULL,
    reason          VARCHAR(255),
    trace_id        VARCHAR(64) NOT NULL,
    event_datetime  TIMESTAMPTZ NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT(NOW())
);

CREATE TABLE IF NOT EXISTS contract_party(
    id                  SERIAL PRIMARY KEY,
    contract_id         INTEGER NOT NULL REFERENCES contract(id),
    party_key           CHAR(36) NOT NULL,          -- Cadastro; nao e FK
    contract_role_id    INTEGER NOT NULL REFERENCES contract_role(id),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT(NOW()),
    UNIQUE(contract_id, party_key, contract_role_id)
);

CREATE TABLE IF NOT EXISTS split_version(
    id                      SERIAL PRIMARY KEY,
    contract_id             INTEGER NOT NULL REFERENCES contract(id),
    version                 INTEGER NOT NULL,
    rent_amount_in_cents    NUMERIC(20,0) NOT NULL,
    -- nulo ate o Ledger confirmar; uma transicao so, irreversivel
    provisioned_at          TIMESTAMPTZ,
    actor                   VARCHAR(255) NOT NULL,
    reason                  VARCHAR(255),
    trace_id                VARCHAR(64) NOT NULL,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT(NOW()),
    UNIQUE(contract_id, version),
    CHECK (version >= 1),
    CHECK (rent_amount_in_cents > 0)
);

CREATE TABLE IF NOT EXISTS split_item(
    id                  SERIAL PRIMARY KEY,
    split_version_id    INTEGER NOT NULL REFERENCES split_version(id),
    contract_party_id   INTEGER NOT NULL REFERENCES contract_party(id),  -- beneficiario + papel
    purpose_code        VARCHAR(50) NOT NULL,
    allocation_type     VARCHAR(20) NOT NULL,
    percentage          NUMERIC(7,4),
    amount_in_cents     NUMERIC(20,0),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT(NOW()),
    UNIQUE(split_version_id, purpose_code),
    CHECK (purpose_code IN ('RENT', 'OWN', 'IPTU', 'CONDO', 'WATER', 'ELECTRICITY')),
    CHECK (allocation_type IN ('percentage', 'fixed', 'remainder')),
    CHECK ((allocation_type = 'percentage') = (percentage IS NOT NULL)),
    CHECK ((allocation_type = 'fixed')      = (amount_in_cents IS NOT NULL))
);

ALTER TABLE contract ADD COLUMN IF NOT EXISTS ledger_key VARCHAR(255);
ALTER TABLE contract ADD COLUMN IF NOT EXISTS platform_fee_percentage NUMERIC(7,4);

DO $guard$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid='contract_status_event'::regclass AND tgname='contract_status_event_no_change') THEN
        CREATE TRIGGER contract_status_event_no_change BEFORE UPDATE OR DELETE ON contract_status_event
        FOR EACH ROW EXECUTE FUNCTION append_only_guard();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid='contract_status_event'::regclass AND tgname='contract_status_event_no_truncate') THEN
        CREATE TRIGGER contract_status_event_no_truncate BEFORE TRUNCATE ON contract_status_event
        FOR EACH STATEMENT EXECUTE FUNCTION append_only_guard();
    END IF;
END;
$guard$;
CREATE INDEX IF NOT EXISTS contract_party_by_party ON contract_party (party_key);
