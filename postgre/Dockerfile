FROM postgres:17

COPY ./init.sh /docker-entrypoint-initdb.d/

COPY ./postgresql.conf /var/lib/postgresql/data/postgresql.conf
COPY ./pg_hba.conf /var/lib/postgresql/data/ph_hba.conf