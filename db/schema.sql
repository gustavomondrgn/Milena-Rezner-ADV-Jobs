-- Schema do ADV Jobs — fonte única da verdade.
--
-- Aplicado pelo BOT no boot (bot/store.py -> Store.migrar) e usado pelo PAINEL.
-- Só existe em um lugar de propósito: schema duplicado em dois lugares diverge
-- no primeiro dia em que alguém tem pressa.
--
-- Todo comando aqui precisa ser idempotente: roda a cada deploy do bot.
--
-- Nota de linhagem: este projeto nasceu de um bot de vagas, e a tentação era
-- reaproveitar as colunas antigas (`work_mode`, `seniority`, `salary`) para
-- guardar tipo de demanda, UF e honorário. Não foi feito. Uma coluna chamada
-- `seniority` contendo "SP" funciona por seis meses e depois custa uma tarde de
-- alguém. Banco novo, nomes certos.

CREATE TABLE IF NOT EXISTS job_events (
    id            BIGSERIAL PRIMARY KEY,
    uid           TEXT        NOT NULL,
    source        TEXT        NOT NULL,
    -- O que o post diz
    title         TEXT        NOT NULL DEFAULT '',
    autor         TEXT        NOT NULL DEFAULT '',
    url           TEXT        NOT NULL DEFAULT '',
    published_at  TEXT        NOT NULL DEFAULT '',
    -- O que o classificador entendeu
    status        TEXT        NOT NULL,
    category      TEXT        NOT NULL DEFAULT '',   -- relevant/borderline/irrelevant
    categoria     TEXT        NOT NULL DEFAULT '',   -- área do direito
    tipo_demanda  TEXT        NOT NULL DEFAULT '',   -- lead_cliente/parceria/vaga/divulgacao
    resumo_demanda TEXT       NOT NULL DEFAULT '',   -- "usucapião de terreno"
    uf            TEXT        NOT NULL DEFAULT '',
    comarca       TEXT        NOT NULL DEFAULT '',
    exige_presenca BOOLEAN    NOT NULL DEFAULT false,
    tem_contato   BOOLEAN     NOT NULL DEFAULT false,
    valor         TEXT        NOT NULL DEFAULT '',
    language      TEXT        NOT NULL DEFAULT '',
    score         INTEGER     NOT NULL DEFAULT 0,
    reason        TEXT        NOT NULL DEFAULT '',
    -- Operação
    local_day     DATE        NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    telegram_message_id BIGINT,
    closed_at     TIMESTAMPTZ
);

-- Colunas acrescentadas depois da primeira versão vão como ALTER, e não dentro
-- do CREATE acima, porque o CREATE só roda em banco novo: em banco que já
-- existe ele é ignorado pelo IF NOT EXISTS e a coluna nunca apareceria.
ALTER TABLE job_events ADD COLUMN IF NOT EXISTS autor          TEXT NOT NULL DEFAULT '';
ALTER TABLE job_events ADD COLUMN IF NOT EXISTS tipo_demanda   TEXT NOT NULL DEFAULT '';
ALTER TABLE job_events ADD COLUMN IF NOT EXISTS resumo_demanda TEXT NOT NULL DEFAULT '';
ALTER TABLE job_events ADD COLUMN IF NOT EXISTS uf             TEXT NOT NULL DEFAULT '';
ALTER TABLE job_events ADD COLUMN IF NOT EXISTS comarca        TEXT NOT NULL DEFAULT '';
ALTER TABLE job_events ADD COLUMN IF NOT EXISTS valor          TEXT NOT NULL DEFAULT '';
ALTER TABLE job_events ADD COLUMN IF NOT EXISTS exige_presenca BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE job_events ADD COLUMN IF NOT EXISTS tem_contato    BOOLEAN NOT NULL DEFAULT false;

-- O painel quase sempre pergunta "o que aconteceu nos últimos N dias, por
-- status" — daí o índice composto em vez de um por coluna.
CREATE INDEX IF NOT EXISTS job_events_day_status_idx ON job_events (local_day DESC, status);
CREATE INDEX IF NOT EXISTS job_events_source_idx     ON job_events (source, local_day DESC);
CREATE INDEX IF NOT EXISTS job_events_created_idx    ON job_events (created_at DESC);

-- Um mesmo uid pode reaparecer (fila devolvida, reenvio), mas nunca duas vezes
-- no mesmo status: sem isso um redeploy no meio do ciclo duplicaria a métrica.
CREATE UNIQUE INDEX IF NOT EXISTS job_events_uid_status_key ON job_events (uid, status);

CREATE TABLE IF NOT EXISTS bot_config (
    id         INTEGER PRIMARY KEY DEFAULT 1,
    data       JSONB       NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT bot_config_single_row CHECK (id = 1)
);

INSERT INTO bot_config (id, data) VALUES (1, '{}'::jsonb)
ON CONFLICT (id) DO NOTHING;

CREATE TABLE IF NOT EXISTS users (
    id            BIGSERIAL PRIMARY KEY,
    email         TEXT        NOT NULL UNIQUE,
    password_hash TEXT        NOT NULL,
    name          TEXT        NOT NULL DEFAULT '',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
