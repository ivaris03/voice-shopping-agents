-- Restore the required HEADPHONES connectivity attribute for the original demo catalog.
-- Safe to run repeatedly against an existing database.
BEGIN;

UPDATE products
SET attributes = attributes || '{"connectivity":"bluetooth"}'::jsonb
WHERE id IN (
    '20000000-0000-4000-8000-000000000001'::uuid,
    '20000000-0000-4000-8000-000000000002'::uuid,
    '20000000-0000-4000-8000-000000000003'::uuid
)
  AND NOT (attributes ? 'connectivity');

COMMIT;
