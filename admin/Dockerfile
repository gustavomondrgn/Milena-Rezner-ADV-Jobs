# Contexto de build = raiz do repositório (ver docker-compose.yml).
#
# Três estágios para a imagem final não carregar nem o código-fonte nem as
# dependências de build: só o servidor `standalone` que o Next produz.

# --- 1. dependências -------------------------------------------------------
FROM node:22-alpine AS deps
WORKDIR /app
COPY admin/package.json admin/package-lock.json ./
# `npm ci` respeita o lockfile — build reproduzível, sem versão nova entrando
# sozinha entre um deploy e outro.
RUN npm ci

# --- 2. build --------------------------------------------------------------
FROM node:22-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY admin/ ./
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

# --- 2b. dependências dos scripts de manutenção ----------------------------
# Fora da árvore do `standalone` de propósito. O node_modules que o Next gera é
# podado pelo tracing dos imports do servidor: `bcryptjs` e `pg` não sobrevivem
# lá, e um script de manutenção que os importe morre com ERR_MODULE_NOT_FOUND.
# Como ESM ignora NODE_PATH, a saída é os scripts morarem ao lado do próprio
# node_modules, em /opt/manut.
FROM node:22-alpine AS manut
WORKDIR /opt/manut
RUN npm init -y > /dev/null  && npm install --no-fund --no-audit bcryptjs@2.4.3 pg@8.13.1

# --- 3. execução -----------------------------------------------------------
FROM node:22-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0

# Usuário sem privilégios: se alguém escapar do processo, não escapa como root.
RUN addgroup -g 1001 -S nodejs && adduser -S nextjs -u 1001

# `standalone` já traz um node_modules podado com só o que o servidor usa —
# inclusive `pg` e `bcryptjs`, que os scripts de manutenção também precisam.
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

# Scripts de manutenção (criar usuário, aplicar schema) com as dependências
# deles ao lado, em /opt/manut. Rodam por `docker exec` sem precisar de outra
# imagem:  docker exec adv-jobs-admin node /opt/manut/scripts/criar-usuario.mjs
COPY --from=manut --chown=nextjs:nodejs /opt/manut/node_modules /opt/manut/node_modules
COPY --from=manut --chown=nextjs:nodejs /opt/manut/package.json /opt/manut/package.json
COPY --from=builder --chown=nextjs:nodejs /app/scripts /opt/manut/scripts
COPY --chown=nextjs:nodejs db/ /opt/manut/db/

USER nextjs
EXPOSE 3000

# O Coolify só encaminha tráfego quando o healthcheck passa.
HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
  CMD node -e "fetch('http://127.0.0.1:3000/login').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"

# A semeadura do primeiro usuário roda antes do servidor e nunca o impede de
# subir (`;`, não `&&`): painel no ar sem usuário ainda dá para consertar; painel
# que não sobe, não.
CMD ["sh", "-c", "node /opt/manut/scripts/semear-usuario.mjs; node server.js"]
