\set ECHO none
-- Роли базы: роль миграций audit_migrate и роль сервиса audit_app (A85, A170). Исполняет psql
-- суперпользователем образа: на пустом томе — сам образ из /docker-entrypoint-initdb.d/, на
-- поднятом — та же программа через `docker compose exec -T db` (README, «Роли базы»). Пароли —
-- из окружения контейнера db командой \getenv, в файле их нет. Каждый запуск сводит роли к
-- одному виду: лишнее у роли сервиса отзывается, нужное выдаётся заново.
\set ON_ERROR_STOP on
\set VERBOSITY terse
\set migrate_role audit_migrate
\set app_role audit_app
\getenv db_name POSTGRES_DB
\getenv migrate_password POSTGRES_MIGRATE_PASSWORD
\getenv app_password POSTGRES_APP_PASSWORD
\if :{?db_name}
\else
  DO $$ BEGIN RAISE EXCEPTION 'roles.sql: не задана переменная POSTGRES_DB'; END $$;
\endif
\if :{?migrate_password}
\else
  DO $$ BEGIN RAISE EXCEPTION 'roles.sql: не задана переменная POSTGRES_MIGRATE_PASSWORD'; END $$;
\endif
\if :{?app_password}
\else
  DO $$ BEGIN RAISE EXCEPTION 'roles.sql: не задана переменная POSTGRES_APP_PASSWORD'; END $$;
\endif
\connect :"db_name"
-- Журнал сеанса без текста команд: в команде ниже — пароли ролей.
SET log_statement = 'none';
SET log_min_duration_statement = -1;
SET log_min_error_statement = panic;
BEGIN;
SET LOCAL lock_timeout = '120s';
SELECT pg_advisory_xact_lock(1001);
SELECT format('CREATE ROLE %I', :'migrate_role')
 WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'migrate_role') \gexec
ALTER ROLE :"migrate_role" LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
  PASSWORD :'migrate_password';
SELECT format('CREATE ROLE %I', :'app_role')
 WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'app_role') \gexec
ALTER ROLE :"app_role" LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
  PASSWORD :'app_password';
SELECT format('REVOKE %I FROM %I', g.rolname, m.rolname)
  FROM pg_auth_members a
  JOIN pg_roles g ON g.oid = a.roleid
  JOIN pg_roles m ON m.oid = a.member
 WHERE m.rolname IN (:'migrate_role', :'app_role') \gexec
REASSIGN OWNED BY :"app_role" TO :"migrate_role";
ALTER DATABASE :"db_name" OWNER TO :"migrate_role";
ALTER SCHEMA public OWNER TO pg_database_owner;
SELECT format('ALTER TABLE public.%I OWNER TO %I', tablename, :'migrate_role')
  FROM pg_tables WHERE schemaname = 'public' AND tableowner <> :'migrate_role' \gexec
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM :"app_role";
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM :"app_role";
REVOKE ALL ON SCHEMA public FROM :"app_role";
REVOKE ALL ON DATABASE :"db_name" FROM :"app_role";
ALTER DEFAULT PRIVILEGES FOR ROLE :"migrate_role" IN SCHEMA public
  REVOKE ALL ON TABLES FROM :"app_role";
ALTER DEFAULT PRIVILEGES FOR ROLE :"migrate_role" IN SCHEMA public
  REVOKE ALL ON SEQUENCES FROM :"app_role";
ALTER DEFAULT PRIVILEGES FOR ROLE :"migrate_role" REVOKE ALL ON TABLES FROM :"app_role";
ALTER DEFAULT PRIVILEGES FOR ROLE :"migrate_role" REVOKE ALL ON SEQUENCES FROM :"app_role";
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE :"migrate_role" IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO :"app_role";
ALTER DEFAULT PRIVILEGES FOR ROLE :"migrate_role" IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO :"app_role";
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO :"app_role";
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO :"app_role";
COMMIT;
