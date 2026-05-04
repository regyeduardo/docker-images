FROM python:3.12-alpine

# Instala dependências do sistema via apk (Alpine package manager)
# - nginx: servidor web reverso
# - ffmpeg: processamento de áudio/vídeo (extração de áudio para transcrição)
# - supervisor: gerenciamento de processos (nginx + gunicorn)
# - curl: healthcheck
# - chromium + chromium-chromedriver: cookies do navegador para yt-dlp
# - nodejs + npm: JS runtime para extração do YouTube (yt-dlp)
# - libxml2 + libxslt-dev: necessários para python-docx (processamento de XML do Word)
RUN apk add --no-cache \
    nginx \
    ffmpeg \
    supervisor \
    curl \
    chromium \
    chromium-chromedriver \
    nodejs \
    npm \
    libxml2 \
    libxslt-dev \
    && rm -rf /var/cache/apk/*

# Instala pacotes Python
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# Atualiza yt-dlp para a versão mais recente (essencial para evitar bloqueios do YouTube)
RUN pip install --no-cache-dir -U yt-dlp

# Verifica se yt-dlp foi instalado e está acessível
RUN which yt-dlp || echo "yt-dlp não encontrado no PATH" \
    && yt-dlp --version || echo "yt-dlp não funciona"

# Inicializa perfil do Chromium para que yt-dlp consiga usar --cookies-from-browser chromium
RUN chromium --headless --no-sandbox --disable-gpu --disable-dev-shm-usage --dump-dom --virtual-time-budget=100 about:blank 2>/dev/null; \
    mkdir -p /root/.config/chromium/Default && \
    chmod -R 755 /root/.config/chromium

# Cria arquivo de configuração do yt-dlp com opções anti-bloqueio
RUN mkdir -p /root/.config/yt-dlp && \
    cat > /root/.config/yt-dlp/config << 'YTCONF'
# yt-dlp config - anti-blocking settings
--retries 10
--extractor-retries 10
--retry-sleep linear=5:30
--sleep-requests 2.0
--sleep-interval 5
--max-sleep-interval 30
--user-agent "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
--add-header "Accept:text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"
--add-header "Accept-Language:pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7"
YTCONF

# Verifica Node.js e yt-dlp
RUN node --version && npm --version && yt-dlp --version

# Remove o site padrão do nginx (Alpine) que conflita com nossa config na porta 80
RUN rm -f /etc/nginx/http.d/default.conf

# Cria diretórios
RUN mkdir -p /app /run/nginx /var/log/supervisor /data/uploads

# Copia arquivos da aplicação
COPY app.py /app/app.py
COPY nginx.conf /etc/nginx/http.d/default.conf
COPY supervisord.conf /etc/supervisord.conf
COPY web/ /app/web/
COPY skills/ /app/skills/
COPY services/ /app/services/

# Ajusta permissões (Alpine usa usuário 'nginx' em vez de 'www-data')
RUN chown -R nginx:nginx /app /data

EXPOSE 80

CMD ["/usr/bin/supervisord", "-c", "/etc/supervisord.conf"]
