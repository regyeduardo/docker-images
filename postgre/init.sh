#!/bin/bash
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" <<-EOSQL
    SELECT 'CREATE DATABASE ssoversi'
    WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'ssoversi') 
    \gexec

    \c ssoversi;

    DO \$\$
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'admin') THEN
            CREATE USER admin WITH PASSWORD 'admin';
        END IF;

        GRANT ALL PRIVILEGES ON DATABASE ssoversi TO admin;
        GRANT ALL ON schema public TO admin;
    END
    \$\$;

    \c

    SELECT 'CREATE DATABASE plannerversi'
    WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'plannerversi') 
    \gexec

    \c plannerversi;

    DO \$\$
    BEGIN
        GRANT ALL PRIVILEGES ON DATABASE plannerversi TO admin;
        GRANT ALL ON schema public TO admin;
    END
    \$\$;
EOSQL
