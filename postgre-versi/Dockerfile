FROM postgres:17

# Copia o script de inicialização
COPY ./init.sh /docker-entrypoint-initdb.d/init.sh
RUN chmod +x /docker-entrypoint-initdb.d/init.sh

# Copia arquivos de configuração — cuidado: esses só funcionam no primeiro volume
COPY ./postgresql.conf /etc/postgresql/postgresql.conf
COPY ./pg_hba.conf /etc/postgresql/pg_hba.conf

# Opcional: use parâmetros para apontar para esses arquivos
# Isso precisa ser definido no comando de entrada ou via docker-compose
