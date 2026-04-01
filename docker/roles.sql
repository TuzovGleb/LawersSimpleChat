-- Bootstrap roles, schemas and extensions for PostgREST + GoTrue.
-- Runs as postgres superuser on first DB init.

-- Roles
CREATE ROLE anon            NOLOGIN NOINHERIT;
CREATE ROLE authenticated   NOLOGIN NOINHERIT;
CREATE ROLE service_role    NOLOGIN NOINHERIT BYPASSRLS;
CREATE ROLE authenticator   NOINHERIT LOGIN PASSWORD 'postgres';
CREATE ROLE supabase_auth_admin LOGIN PASSWORD 'postgres' NOINHERIT;
ALTER ROLE supabase_auth_admin SET search_path = 'auth';

GRANT anon              TO authenticator;
GRANT authenticated     TO authenticator;
GRANT service_role      TO authenticator;
GRANT supabase_auth_admin TO authenticator;

-- Extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Auth schema (GoTrue will create its tables here on startup)
CREATE SCHEMA IF NOT EXISTS auth AUTHORIZATION supabase_auth_admin;
GRANT USAGE  ON SCHEMA auth TO supabase_auth_admin, service_role;
ALTER DEFAULT PRIVILEGES IN SCHEMA auth GRANT ALL ON TABLES    TO supabase_auth_admin, service_role;
ALTER DEFAULT PRIVILEGES IN SCHEMA auth GRANT ALL ON SEQUENCES TO supabase_auth_admin, service_role;

-- Public schema grants for PostgREST
GRANT USAGE, CREATE ON SCHEMA public TO anon, authenticated, service_role, supabase_auth_admin;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES    TO anon, authenticated, service_role;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO anon, authenticated, service_role;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON ROUTINES  TO anon, authenticated, service_role;
