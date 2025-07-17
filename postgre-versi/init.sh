#!/bin/bash
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" <<-EOSQL

-- Criação do usuário admin com senha
DO \$\$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'admin') THEN
        CREATE ROLE admin WITH LOGIN PASSWORD 'admin';
    END IF;
END
\$\$;

DO \$\$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'dario') THEN
        CREATE ROLE dario WITH LOGIN PASSWORD 'Planner2525';
    END IF;
END
\$\$;

-- Criação dos bancos, se não existirem
SELECT 'CREATE DATABASE ssoversi'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'ssoversi')
\gexec

SELECT 'CREATE DATABASE plannerversi'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'plannerversi')
\gexec

SELECT 'CREATE DATABASE benchmarkingversi'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'benchmarkingversi')
\gexec

EOSQL

# Conceder permissões no nível de cada banco
for DB in ssoversi plannerversi benchmarkingversi; do
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname="$DB" <<-EOSQL
    -- Permissões no banco
    GRANT ALL PRIVILEGES ON DATABASE $DB TO admin;

    -- Permissões no schema padrão
    GRANT ALL ON SCHEMA public TO admin;

    -- Permissões em todas as tabelas atuais
    GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO admin;

    -- Permissões em todas as sequências atuais
    GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO admin;

    -- Permissões em todas as funções atuais (caso existam)
    GRANT ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA public TO admin;

    -- Permissões automáticas para objetos futuros
    ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO admin;
    ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO admin;
    ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON FUNCTIONS TO admin;
EOSQL
done
