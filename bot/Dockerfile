# Contexto de build = raiz do repositório (ver docker-compose.yml), porque a
# imagem precisa de `db/schema.sql`, que é compartilhado com o painel.
FROM python:3.12-slim

WORKDIR /app

COPY bot/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# O navegador do Playwright e as bibliotecas de sistema que ele exige.
#
# É o passo caro desta imagem (~400 MB) e não tem como evitar: grupo do Facebook
# é montado por JavaScript atrás de login, então não existe versão "só HTTP"
# desta fonte. `--with-deps` instala as libs do sistema (fontes, nss, libx11...)
# que o Chromium precisa e que a imagem slim não traz — sem elas o navegador
# instala, sobe e morre com um erro de biblioteca ausente.
#
# `PLAYWRIGHT_BROWSERS_PATH=0` guarda o browser dentro do próprio pacote, em vez
# de em /root/.cache. Sem isso o browser some quando o container roda com outro
# usuário — e o sintoma é um erro de "executable doesn't exist" só em produção.
ENV PLAYWRIGHT_BROWSERS_PATH=0
RUN playwright install --with-deps chromium && \
    rm -rf /var/lib/apt/lists/*

# Código do bot.
#
# Curinga, e nao a lista de arquivos que estava aqui antes. A lista custou uma
# manha de producao: um modulo novo (relogin.py) entrou no repositorio, nao
# entrou na lista, e a imagem subiu sem ele. O bot quebrava no import e
# reiniciava em laco: sem Telegram, sem coleta, e com tudo parecendo no ar do
# lado de fora.
#
# bot/*.py nao pega tools/ nem tests/: sao subpastas, e o curinga nao desce.
COPY bot/*.py ./

# Configuração do filtro: profile.md, lista de grupos e termos de busca.
# `main.py` resolve os caminhos a partir de `<pasta do main>/config/` — mexer
# aqui exige mexer no CONFIG_DIR de lá.
COPY bot/config/ ./config/

# Schema do Postgres, aplicado no boot. `store.py` procura em ../db/schema.sql
# relativo a si mesmo; como o código está em /app, o schema vai em /app/../db —
# então copiamos para /db.
COPY db/schema.sql /db/schema.sql

# Estado persistido — e aqui mora um arquivo que os outros projetos não tinham:
# `fb_state.json`, a sessão do Facebook. Perder o volume não custa só o
# histórico, custa um login manual. Ver bot/tools/facebook_login.py.
VOLUME ["/app/data"]
ENV DATA_DIR=/app/data

CMD ["python", "-u", "main.py"]
