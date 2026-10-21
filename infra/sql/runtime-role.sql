-- Execute as the migration/database owner AFTER python -m scripts.migrate.
-- Dedicated SupportOps database only. No password is stored in this file.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'supportops_runtime') THEN
    CREATE ROLE supportops_runtime LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
  END IF;
END $$;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO supportops_runtime;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO supportops_runtime;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO supportops_runtime;
REVOKE INSERT, UPDATE, DELETE ON alembic_version, checkpoint_migrations FROM supportops_runtime;
REVOKE UPDATE, DELETE ON audit_events FROM supportops_runtime;
-- Set a generated password through a secure DB-admin workflow and store it in
-- Secrets Manager. Use these runtime credentials for API + worker only.
-- Re-run grants after future migrations; migration credentials stay separate.
