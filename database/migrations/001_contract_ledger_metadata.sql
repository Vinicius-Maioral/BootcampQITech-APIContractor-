-- Apply after the contract schema from database.sql, on existing databases.
-- No destructive changes; do not fill these fields without a Ledger confirmation.
BEGIN;
ALTER TABLE contract ADD COLUMN IF NOT EXISTS ledger_key VARCHAR(255);
ALTER TABLE contract ADD COLUMN IF NOT EXISTS platform_fee_percentage NUMERIC(7,4);
COMMIT;
