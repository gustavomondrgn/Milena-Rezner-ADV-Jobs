"""ADV Jobs Bot — garimpa demandas jurídicas em grupos do Facebook.

Lê posts de grupos do Facebook, separa demanda de ruído para o perfil da
Dra. Milena Rezner descrito em `profile.md`, e publica o que sobra no grupo do
Telegram. As fontes ficam em `sources.py` e `facebook.py`; aqui mora o pipeline:

    buscar → deduplicar → filtros baratos → classificar → ENFILEIRAR → despachar

A fila (`dispatch.py`) separa aprovar de publicar. Neste projeto o teto diário
nasce em ZERO — ou seja, sem teto: a Milena quer ver tudo que passar no filtro.
A fila continua existindo mesmo assim, por dois motivos: ela ordena por nota (o
melhor sai primeiro numa rajada) e o teto pode ser ligado pelo painel, sem
redeploy, no dia em que o grupo inundar.

A diferença de fundo em relação a um bot de vagas: aqui a maior parte do que se
lê **não é oferta de trabalho**. Grupo de advogado é dominado por advogado se
anunciando. O filtro mais importante deste projeto não é de área nem de lugar —
é o que separa "alguém precisa de advogado" de "sou advogado, me contrate".
"""

from __future__ import annotations

import base64
import hashlib
import html
import json
import logging
import os
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import requests
from dotenv import load_dotenv

import filters
import vitality
from bot_control import CommandListener
from dispatch import SendQueue
from facebook import FacebookSource
from publicadas import RegistroPublicadas
from sources import (
    AuthError, GupySource, IndeedSource, Job, LinkedInSource, SourceError,
)
from store import Store
from telemetry import DailyStats

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore[assignment]

try:
    from google import genai
    from google.genai import types as genai_types
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False

# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------

load_dotenv()

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("adv-jobs-bot")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "600"))
DATA_DIR = Path(os.getenv("DATA_DIR", "."))

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite").strip()

# Os arquivos de configuração do filtro moram ao lado do código, em bot/config/.
# O caminho é resolvido a partir DESTE arquivo, não do diretório de trabalho:
# assim `python bot/main.py` funciona de qualquer pasta e o container não
# depende de onde o CMD foi chamado.
BASE_DIR = Path(__file__).resolve().parent
CONFIG_DIR = BASE_DIR / "config"


def _config_file(env_var: str, nome: str) -> Path:
    """Resolve um arquivo de configuração, tolerando caminho velho no ambiente.

    Cuidado que já custou caro uma vez: até 12/08 estes arquivos ficavam na raiz
    e o painel do Coolify guarda `PROFILE_FILE=profile.md` de lá. Honrar esse
    valor cegamente depois da mudança de pastas apontaria para um arquivo
    inexistente — e o bot trata "sem profile.md" como **filtro desligado**, ou
    seja, publicaria tudo. Se o caminho do ambiente não existir, cai para
    `bot/config/`, que existe sempre porque vai dentro da imagem.
    """
    padrao = CONFIG_DIR / nome
    bruto = os.getenv(env_var, "").strip()
    if not bruto:
        return padrao
    caminho = Path(bruto)
    if caminho.exists():
        return caminho
    log.warning("%s=%r não existe — usando %s", env_var, bruto, padrao)
    return padrao


PROFILE_FILE = _config_file("PROFILE_FILE", "profile.md")
TERMS_FILE = _config_file("TERMS_FILE", "search_terms.txt")
# O LinkedIn tem lista própria e curta: lá cada vaga custa duas requisições.
LINKEDIN_TERMS_FILE = _config_file("LINKEDIN_TERMS_FILE", "search_terms_linkedin.txt")
# Quais grupos do Facebook ler. É o arquivo que define o alcance do bot inteiro.
FACEBOOK_GROUPS_FILE = _config_file("FACEBOOK_GROUPS_FILE", "facebook_groups.txt")

# Fontes ativas, separadas por vírgula. Permite desligar uma sem mexer no código.
# O Facebook é a fonte deste projeto; as outras vieram testadas do projeto
# anterior e ficam desligadas, prontas caso um dia se queira vaga de emprego
# formal de advogado (aí `search_terms.txt` já está escrito para isso).
ENABLED_SOURCES = os.getenv("SOURCES", "facebook")

TELEGRAM_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

SEEN_FILE = DATA_DIR / "seen_ids.json"
SKIPPED_LOG_FILE = DATA_DIR / "skipped_jobs.jsonl"
QUOTA_LOG_FILE = DATA_DIR / "quota_log.jsonl"
STATS_FILE = DATA_DIR / "daily_stats.json"
QUEUE_FILE = DATA_DIR / "send_queue.json"
PUBLICADAS_FILE = DATA_DIR / "publicadas.json"

# --- Facebook ---------------------------------------------------------------
# A sessão do navegador, criada por `bot/tools/facebook_login.py`. Fica no
# volume: é o único arquivo cuja perda obriga alguém a logar de novo na mão.
FACEBOOK_STATE_FILE = Path(
    os.getenv("FACEBOOK_STATE_FILE", "").strip() or (DATA_DIR / "fb_state.json")
)
# A mesma sessão, em base64, vinda do ambiente. É como ela chega no servidor:
# sem isto o arquivo teria de ser copiado para dentro do volume na mão depois de
# cada primeiro deploy (`docker cp`), e um bot sem sessão não dá erro — ele roda
# e o grupo emudece. Gerar com `python bot/tools/sessao_para_env.py`.
FACEBOOK_STATE_B64 = os.getenv("FACEBOOK_STATE_B64", "").strip()

FACEBOOK_MAX_POSTS = int(os.getenv("FACEBOOK_MAX_POSTS", "25"))
FACEBOOK_SCROLLS = int(os.getenv("FACEBOOK_SCROLLS", "4"))

# O container roda em UTC; tudo que é "dia" para o usuário é dia de Brasília.
TIMEZONE_NAME = os.getenv("TIMEZONE", "America/Sao_Paulo")
REPORT_HOUR = int(os.getenv("REPORT_HOUR", "22"))
# Para onde vai o relatório diário: "grupo" ou "privado".
REPORT_TO = os.getenv("REPORT_TO", "grupo").strip().lower()
# Quem recebe o relatório quando REPORT_TO=privado. Não dá poder nenhum sobre o
# bot — desde 12/08 não existe comando administrativo.
REPORT_CHAT_IDS = [
    p.strip() for p in os.getenv("REPORT_CHAT_IDS", "").replace(";", ",").split(",")
    if p.strip()
]

# Chats que tinham menu de comandos administrativos antes de 12/08. Não dá
# nenhum poder a eles — serve só para o bot conseguir APAGAR o menu antigo, que
# o Telegram guarda por escopo de chat e sobrevive à remoção dos comandos.
# Reaproveita TELEGRAM_ADMIN_IDS porque é exatamente essa a lista, e ela já está
# cadastrada no Coolify.
def _ids(bruto: str) -> set[int]:
    saida: set[int] = set()
    for pedaco in (bruto or "").replace(";", ",").split(","):
        pedaco = pedaco.strip()
        if pedaco.lstrip("-").isdigit():
            saida.add(int(pedaco))
    return saida


CHATS_MENU_LEGADO = _ids(os.getenv("TELEGRAM_ADMIN_IDS", ""))


def _flag(nome: str, padrao: bool) -> bool:
    bruto = os.getenv(nome, "").strip().lower()
    if not bruto:
        return padrao
    return bruto in ("1", "true", "sim", "yes", "on")


# --- Controle de volume e horário -----------------------------------------
# ZERO = SEM TETO. É o pedido explícito do Gustavo para este projeto: o bot
# identifica e envia, sem cota diária. A fila continua no caminho porque ela
# ordena por nota — numa rajada, a melhor demanda sai primeiro — e porque ligar
# um teto vira um campo no painel em vez de um redeploy, no dia em que o grupo
# começar a inundar. O amortecedor que existe de fato é a janela de horário
# abaixo: nada é publicado de madrugada.
DAILY_LIMIT = int(os.getenv("DAILY_LIMIT", "0"))
SEND_WINDOW_START = int(os.getenv("SEND_WINDOW_START", "6"))
SEND_WINDOW_END = int(os.getenv("SEND_WINDOW_END", "23"))
QUEUE_TTL_DAYS = int(os.getenv("QUEUE_TTL_DAYS", "3"))
MIN_SCORE = int(os.getenv("MIN_SCORE", "0"))

# --- Demanda encerrada -----------------------------------------------------
# O que fazer quando o post some do Facebook: "marcar" mantém a mensagem no
# grupo, riscada e com aviso (padrão), "apagar" a remove, "nada" desliga a
# revisão inteira.
#
# O padrão era "apagar" e virou "marcar" em 16/08/2026, por decisão do Gustavo:
# quem já tinha visto a demanda no grupo entende o que aconteceu com ela; quando
# a mensagem some, o histórico fica com um buraco sem explicação.
#
# Vale a ressalva de expectativa: no Facebook isso rende menos do que rendia num
# portal de vagas. Portal tira o anúncio do ar quando a vaga é preenchida; já
# quem posta num grupo raramente volta para apagar depois de resolver. O que
# esta verificação pega de verdade é post removido pelo moderador, post apagado
# pelo autor e grupo que fechou — não "a demanda já foi atendida".
ACAO_VAGA_ENCERRADA = os.getenv("ACAO_VAGA_ENCERRADA", "marcar").strip().lower()
RECHECK_HORAS = int(os.getenv("RECHECK_HORAS", "24"))
# Baixo de propósito: no Facebook cada verificação é uma página carregada num
# navegador, não uma requisição de API. Oito por ciclo de 10 min dá ~48/hora,
# folgado para reexaminar a cada 24h tudo que foi publicado num mês.
RECHECK_POR_CICLO = int(os.getenv("RECHECK_POR_CICLO", "8"))
RECHECK_DIAS = int(os.getenv("RECHECK_DIAS", "30"))

# --- Regras de corte -------------------------------------------------------
REJECT_ENGLISH = _flag("REJECT_ENGLISH", True)

# O filtro que carrega este projeto. Grupo de advogado é dominado por advogado
# se anunciando; sem esta regra o grupo da Milena vira um mural de concorrentes.
REJEITAR_DIVULGACAO = _flag("REJEITAR_DIVULGACAO", True)


def _ufs(bruto: str) -> tuple[str, ...]:
    return tuple(
        p.strip().upper()[:2]
        for p in (bruto or "").replace(";", ",").split(",")
        if p.strip()
    )


# UFs que ela atende. **Vazio = aceita qualquer UF, e vazio é o padrão.**
#
# Já foi a lista dos 12 estados de milenarezner.com.br. Foi esvaziada por decisão
# do Gustavo em 16/08/2026, e a decisão é coerente com as fontes: os grupos que o
# bot lê são nacionais, de dúvida aberta ao público, e o escritório é 100%
# digital. Filtrar por estado ali descartaria cliente por um dado que o próprio
# post não traz — a UF é o único corte deste bot baseado em algo INFERIDO, e num
# grupo nacional a inferência não tem de onde sair.
#
# Continua fazendo o que sempre fez quando alguém preencher a lista de novo (no
# painel, sem redeploy): morder só em demanda que exige presença física.
UFS_ATENDIDAS = _ufs(os.getenv("UFS_ATENDIDAS", ""))
# Post que não declara lugar nenhum: aceitar? Sim, por padrão — em grupo
# regional o lugar está implícito no grupo, e o atendimento dela é 100% digital.
# Descartar por omissão mataria a maior parte das demandas boas.
ACEITAR_SEM_LOCAL = _flag("ACEITAR_SEM_LOCAL", True)

# --- Apresentação do bot no privado ---------------------------------------
SITE_URL = os.getenv("SITE_URL", "https://www.milenarezner.com.br").strip()
INSTAGRAM_URL = os.getenv(
    "INSTAGRAM_URL", "https://instagram.com/advmilenarezner"
).strip()
SUPORTE_TELEGRAM = os.getenv("SUPORTE_TELEGRAM", "").strip()


def _tz() -> Any:
    """Fuso local. Cai em UTC-3 fixo se a base de fusos não existir na imagem."""
    if ZoneInfo is not None:
        try:
            return ZoneInfo(TIMEZONE_NAME)
        except Exception as exc:  # noqa: BLE001
            log.warning("Fuso %s indisponível (%s) — usando UTC-3 fixo", TIMEZONE_NAME, exc)
    from datetime import timedelta
    return timezone(timedelta(hours=-3))


def agora_local() -> datetime:
    return datetime.now(_tz())


def hoje_local() -> str:
    """O "dia" do bot.

    Precisa ser o dia LOCAL, não o UTC. Com UTC, os contadores viravam às 21h de
    Brasília e o relatório das 22h somava só a última hora — era esse o bug do
    relatório reportado pelo Gabriel.
    """
    return agora_local().strftime("%Y-%m-%d")


REQUEST_TIMEOUT = 30
TELEGRAM_RATE_LIMIT_SECONDS = 1.0
DESCRIPTION_MAX_CHARS = 300

Category = Literal["relevant", "borderline", "irrelevant"]

# Quem escreveu o post e o que ele quer. É o eixo que decide se há trabalho.
TipoDemanda = Literal[
    "lead_cliente",       # pessoa/empresa com problema jurídico procurando advogado
    "parceria_advogado",  # colega passando caso, correspondente, divisão de honorários
    "vaga_emprego",       # escritório contratando advogado
    "divulgacao",         # advogado se anunciando — o ruído dominante
    "nao_informado",
]

# O tipo que NUNCA passa: não é oportunidade, é concorrência se apresentando.
TIPOS_RECUSADOS: tuple[str, ...] = ("divulgacao",)

# As áreas do perfil, mais um escape. O painel liga e desliga cada uma por
# fonte, então isto precisa ser uma lista FECHADA: se o classificador pudesse
# inventar categoria, apareceria opção nova no painel a cada semana e o que
# fosse desligado ali não corresponderia a nada.
# Mudar esta lista exige mudar `CATEGORIAS` em admin/src/lib/config-tipos.ts.
CATEGORIAS: tuple[str, ...] = (
    "imobiliario",          # usucapião, despejo, compra e venda, vícios, renovatória
    "condominio",           # assessoria a condomínio, cota, assembleia, convenção
    "empresarial",          # societário, contrato social, sócios, compliance, LGPD
    "contratos",            # elaboração e revisão, notificação extrajudicial, distrato
    "cobranca",             # ação de cobrança, execução, monitória, inadimplência
    "trabalhista_empresa",  # defesa da EMPRESA em reclamação trabalhista
    "tributario_fiscal",    # execução fiscal, auto de infração, IPTU/ITBI de imóvel
    "outro",                # é da área dela, mas não encaixa em nenhuma acima
)

# Siglas de UF, para validar o que o classificador devolve.
UFS_VALIDAS: frozenset[str] = frozenset(
    "AC AL AP AM BA CE DF ES GO MA MT MS MG PA PB PR PE PI RJ RN RS RO RR SC SP SE TO"
    .split()
)

STATS = DailyStats(STATS_FILE, historico=QUOTA_LOG_FILE)
FILA = SendQueue(QUEUE_FILE, validade_dias=QUEUE_TTL_DAYS)
PUBLICADAS = RegistroPublicadas(PUBLICADAS_FILE, dias_de_vida=RECHECK_DIAS)
STORE = Store()


# ---------------------------------------------------------------------------
# Configuração efetiva (ambiente + painel)
# ---------------------------------------------------------------------------

def config_atual() -> dict[str, Any]:
    """Junta o que veio do ambiente com o que o painel escreveu no banco.

    O painel ganha quando define a chave. Sem banco, sobra só o ambiente — que
    é o modo como o bot rodou até agora e continua rodando se o Postgres cair.
    """
    base: dict[str, Any] = {
        "daily_limit": DAILY_LIMIT,
        "window_start": SEND_WINDOW_START,
        "window_end": SEND_WINDOW_END,
        "min_score": MIN_SCORE,
        "reject_english": REJECT_ENGLISH,
        "rejeitar_divulgacao": REJEITAR_DIVULGACAO,
        "aceitar_sem_local": ACEITAR_SEM_LOCAL,
        "ufs_atendidas": list(UFS_ATENDIDAS),
        "sources": {},
    }
    base["acao_vaga_encerrada"] = ACAO_VAGA_ENCERRADA
    base["recheck_horas"] = RECHECK_HORAS
    base.update({k: v for k, v in STORE.config().items() if v is not None})
    return base


def regra(cfg: dict[str, Any], fonte: str, chave: str) -> Any:
    """Valor de uma regra para uma fonte: o específico dela, senão o geral.

    É o que permite afrouxar uma regra numa fonte só, sem afrouxar no resto —
    por exemplo aceitar qualquer UF num grupo nacional e manter o corte nos
    grupos regionais.
    """
    por_fonte = (cfg.get("sources") or {}).get(fonte) or {}
    if chave in por_fonte and por_fonte[chave] is not None:
        return por_fonte[chave]
    return cfg.get(chave)


def fonte_ligada(cfg: dict[str, Any], fonte: str) -> bool:
    por_fonte = (cfg.get("sources") or {}).get(fonte) or {}
    return bool(por_fonte.get("enabled", True))


def local_aceito(cfg: dict[str, Any], fonte: str, uf: str,
                 exige_presenca: bool) -> tuple[bool, str]:
    """A demanda é atendível de onde a Milena está? Devolve (aceita, motivo).

    A regra de lugar aqui é o oposto da de um bot de vagas, e a inversão é o
    ponto: o escritório é 100% digital, então **lugar só importa quando alguém
    precisa estar fisicamente lá**. Contrato, parecer, notificação e petição se
    fazem de qualquer lugar — cortar isso por causa da comarca jogaria fora
    justamente o trabalho que ela mais consegue pegar.

    Quando há presença exigida (audiência, diligência, assembleia, protocolo),
    aí sim a UF manda: mandar a Milena para uma audiência a 800 km é pior do que
    não ter mostrado a demanda.
    """
    if not exige_presenca:
        return True, ""

    aceitas = tuple(regra(cfg, fonte, "ufs_atendidas") or ())
    if not aceitas:
        return True, ""

    uf = (uf or "").upper()[:2]
    if not uf:
        if regra(cfg, fonte, "aceitar_sem_local"):
            return True, ""
        return False, "exige presença e não diz onde"

    if uf in aceitas:
        return True, ""
    return False, f"exige presença em {uf}, fora das UFs atendidas"


def categoria_ligada(cfg: dict[str, Any], fonte: str, categoria: str) -> bool:
    """Esta fonte pode trazer vagas desta família?

    Ausente = ligada. O padrão é permissivo porque o painel só grava o que o
    Gabriel mexeu: uma categoria nova, criada depois da última vez que ele
    salvou, precisa nascer funcionando em vez de sumir em silêncio.
    """
    por_fonte = (cfg.get("sources") or {}).get(fonte) or {}
    categorias = por_fonte.get("categorias")
    if not isinstance(categorias, dict):
        return True
    return categorias.get(categoria, True) is not False


# ---------------------------------------------------------------------------
# Persistência: IDs já vistos + chaves de deduplicação entre fontes
# ---------------------------------------------------------------------------

def load_seen() -> tuple[set[str], set[str], set[str]]:
    """Lê o estado salvo: (uids vistos, chaves de dedup, fontes já inicializadas).

    O arquivo é o que impede o bot de republicar tudo a cada deploy. Se ele
    estiver corrompido, começar vazio é a escolha certa: a fonte nova entra em
    modo de inicialização silenciosa e registra o acervo sem notificar, então o
    pior caso é um ciclo sem novidade — não uma enxurrada no grupo.
    """
    if not SEEN_FILE.exists():
        return set(), set(), set()
    try:
        with SEEN_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, ValueError, OSError) as exc:
        log.warning("Falha lendo %s: %s — começando vazio", SEEN_FILE, exc)
        return set(), set(), set()

    if not isinstance(data, dict):
        log.warning("%s em formato inesperado — começando vazio", SEEN_FILE)
        return set(), set(), set()

    uids = {str(x) for x in (data.get("uids") or [])}
    keys = {str(x) for x in (data.get("dedup_keys") or [])}
    inicializadas = {str(x) for x in (data.get("initialized_sources") or [])}
    return uids, keys, inicializadas


def save_seen(uids: set[str], keys: set[str], inicializadas: set[str]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = SEEN_FILE.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump({
            "uids": sorted(uids),
            "dedup_keys": sorted(keys),
            "initialized_sources": sorted(inicializadas),
        }, f)
    tmp.replace(SEEN_FILE)


def _normalize(texto: str) -> str:
    """Minúsculas, sem acento e sem pontuação — para comparar título/empresa."""
    sem_acento = (
        unicodedata.normalize("NFKD", texto or "")
        .encode("ascii", "ignore")
        .decode("ascii")
    )
    return re.sub(r"[^a-z0-9]+", "", sem_acento.lower())


def dedup_key(job: Job) -> str:
    """Chave para reconhecer a MESMA vaga anunciada em fontes diferentes.

    A mesma vaga costuma aparecer no Indeed e no LinkedIn com IDs distintos; sem
    isso o grupo receberia a mesma coisa duas vezes.
    """
    return f"{_normalize(job.title)}|{_normalize(job.company)}"


def parece_remoto(job: Job) -> bool:
    """Heurística barata: a fonte diz que é remoto, ou o texto diz."""
    if job.remote_hint == "remoto":
        return True
    return filters.texto_menciona_remoto(f"{job.title}\n{job.description}")


# ---------------------------------------------------------------------------
# Classificador (Gemini)
# ---------------------------------------------------------------------------

_genai_client: Any = None
_profile_text: str | None = None
_profile_mtime: float | None = None

CLASSIFIER_INSTRUCTIONS = """Você lê **posts de grupos do Facebook** em português
brasileiro e faz três coisas: (A) decide se aquilo é uma oportunidade de trabalho
para o perfil informado logo abaixo destas instruções, (B) extrai os dados e
(C) dá uma nota de qualidade à oportunidade.

== O QUE VOCÊ ESTÁ LENDO ==

Não são anúncios de vaga. São posts de rede social, escritos no celular, por
pessoas comuns e por advogados. Espere texto sem estrutura, sem pontuação, em
caixa alta, com erro de digitação, abreviação (`adv`, `p/`, `vc`, `c/v`) e sigla
jurídica solta (`USUCAP`, `AIJ`, `TJSP`, `JEC`, `RT`). A maior parte do que
circula nesses grupos **não é demanda de trabalho** — é conversa, desabafo,
propaganda e pedido de indicação.

A pergunta que você faz em todo post, antes de qualquer outra: **alguém aqui
precisa contratar um advogado, ou alguém está se oferecendo como advogado?**
Só o primeiro caso é oportunidade. O segundo é concorrência, e é o que mais
aparece.

== (A) CLASSIFICAÇÃO ==

- O PERFIL DO USUÁRIO é a única fonte de verdade sobre o que interessa. Não
  presuma nenhuma área por conta própria — siga o que o perfil diz.
- Descrições têm grafia inconsistente, abreviações, gírias e erros de digitação.
  Trate variações como sinônimos ("assistente virtual"/"AV"/"assistente
  vitual", "secretária remota"/"secretariado remoto", "home office"/"homeoffice"),
  inclusive sem acento.
- Considere o título, a profissão/área, as skills E a descrição. Às vezes a
  descrição é vaga mas a categoria/skills denuncia que é da área.
- Se houver QUALQUER dúvida razoável, retorne "borderline".
- "irrelevant" só para casos claramente fora do perfil.
- EXCEÇÃO ao viés de "borderline": se o perfil marcar alguma regra como
  OBRIGATÓRIA e a vaga violar essa regra explicitamente, retorne "irrelevant"
  mesmo que a função encaixe bem.

== (B) EXTRAÇÃO ==

Extraia SOMENTE o que estiver escrito no post. NUNCA invente, deduza ou complete
com suposição. Se a informação não estiver no texto, devolva string vazia ("")
ou "nao_informado" — isso é esperado e correto, e acontece na maioria dos posts.

- tipo_demanda: quem escreveu e o que quer. É o campo mais importante.
  - "lead_cliente" — pessoa física ou empresa com um problema jurídico,
    procurando advogado. ("meu inquilino não paga há 5 meses, preciso de
    advogado", "alguém indica advogado pra usucapião?")
  - "parceria_advogado" — colega com um trabalho concreto sobrando: passar caso,
    procurar parceiro, correspondente para um ato, dividir honorários.
    ("preciso de colega para audiência em Londrina dia 12")
  - "vaga_emprego" — escritório ou empresa contratando advogado (CLT, PJ,
    associado). É oportunidade, mas de outro tipo.
  - "divulgacao" — advogado, escritório ou empresa SE ANUNCIANDO. Currículo,
    portfólio, tabela de honorários, "atuo em todo o Brasil", "faço petições",
    propaganda de curso, mentoria, venda de modelo de petição ou de software.
  - "nao_informado" — não dá pra dizer pelo texto.

  A distinção que mais erra: um advogado escrevendo NÃO é automaticamente
  "divulgacao". "Sou advogado e peguei um caso de usucapião em SP que não vou
  conseguir tocar, alguém tem interesse?" é "parceria_advogado" — há trabalho
  concreto sobrando. O que define é OFERECER TRABALHO A ALGUÉM versus
  OFERECER-SE PARA TRABALHAR.

- categoria: em qual área o trabalho se encaixa MELHOR. Escolha exatamente uma;
  na dúvida entre duas, use a que descreve a maior parte do trabalho.
  - "imobiliario" — usucapião, adjudicação compulsória, despejo, compra e venda
    de imóvel, escritura, registro, matrícula, distrato, vícios construtivos,
    ação renovatória, regularização, alienação fiduciária, aluguel.
  - "condominio" — assessoria a condomínio, convenção, regimento, cobrança de
    cota condominial, assembleia, conflito entre condôminos, síndico.
  - "empresarial" — societário, contrato social, sócios, acordo de quotistas,
    abertura/dissolução de sociedade, departamento jurídico, compliance, LGPD.
  - "contratos" — elaboração, revisão e negociação de contrato, prestação de
    serviços, fornecimento, parceria, NDA, notificação extrajudicial, rescisão.
  - "cobranca" — ação de cobrança, execução de título, monitória, inadimplência,
    protesto, recuperação de crédito.
  - "trabalhista_empresa" — SÓ o lado da empresa: defesa em reclamação
    trabalhista, passivo, contrato de trabalho, rescisão, consultoria
    preventiva. Empregado buscando direitos NÃO é isto.
  - "tributario_fiscal" — execução fiscal, auto de infração, defesa fiscal,
    parcelamento, IPTU e ITBI ligados a imóvel.
  - "outro" — é de Imobiliário ou Empresarial, mas não encaixa acima.

- uf: sigla do estado (2 letras maiúsculas) da demanda. Deduza de cidade,
  comarca, foro ou tribunal quando der: "TJSP" → "SP", "Foro de Cascavel" → "PR",
  "Recife" → "PE", "comarca de Blumenau" → "SC". Se o texto não permitir
  determinar o estado, devolva "" — não chute.
- comarca: a cidade, comarca ou vara citada, como está escrita no texto
  ("2ª Vara Cível de Londrina", "Santos", "Foz do Iguaçu"). Se não houver, "".
- exige_presenca: true quando o trabalho pede alguém FISICAMENTE em algum lugar
  — audiência, sustentação oral, diligência, protocolo em cartório, perícia,
  assembleia presencial, reunião presencial, visita a imóvel. false quando o
  trabalho se faz de qualquer lugar: elaborar ou revisar contrato, parecer,
  petição, notificação extrajudicial, consultoria, assessoria a distância.
  Na dúvida, false — o escritório é 100% digital e o custo de errar para
  "presencial" é descartar trabalho que ela pegaria.
- tem_contato: true se o post traz um jeito direto de responder — telefone,
  WhatsApp, e-mail, "chama no PV", "me chama no direct", link de contato.
- valor: honorário ou pagamento citado NO TEXTO, como está escrito
  ("R$ 500 pela audiência", "50% dos honorários", "a combinar"). Se o texto não
  citar nada sobre pagamento, devolva "".
- autor: nome de quem publicou, se aparecer no texto. Se não, "".
- resumo_demanda: em 2-6 palavras, o que a pessoa precisa ("usucapião de
  terreno", "despejo por falta de pagamento", "revisão de contrato social").
  Se não der pra determinar, "".
- language: idioma em que o POST está escrito — "pt", "en", "es" ou "outro".
- summary: resumo do post em 1-2 frases curtas (até 250 chars), em português,
  dizendo qual é a demanda e o contexto relevante (desde quando, qual imóvel ou
  empresa, o que já foi tentado). Sem repetir o texto inteiro e sem inventar
  nada. Se o post for vazio ou inútil, devolva "".

== (C) NOTA DE QUALIDADE (score, 0 a 100) ==

A nota decide a ordem em que as demandas chegam ao grupo e serve de corte
mínimo. Numa rajada de dez posts, é ela que põe o cliente direto na frente do
"alguém indica um advogado?".

**Use a faixa inteira e seja severo.** Não concentre tudo entre 70 e 80. Se
metade dos posts receber nota parecida, a nota não serviu para nada.

Comece em 50 e ajuste:

  +25 lead_cliente com o problema descrito de forma concreta (é cliente direto,
      o mais valioso que existe aqui)
  +15 parceria_advogado com trabalho específico e delimitado
  +15 demanda inequivocamente dentro de Imobiliário ou Empresarial
  +10 há informação suficiente para dimensionar o caso: o que houve, desde
      quando, qual imóvel ou empresa, o que já foi tentado
  +10 valor de honorário citado, ou disposição explícita de pagar
  +10 não exige presença física (trabalho que ela faz de onde estiver), OU
      exige presença numa UF que ela atende
   +5 contato direto no post
   +5 urgência real declarada: prazo correndo, audiência marcada, notificação
      recebida, citação. Quem tem prazo contrata rápido.

  -15 texto vago demais para julgar ("preciso de um advogado", e nada mais)
  -15 exige presença física em UF que ela não atende
  -20 parece pedido de consulta jurídica de graça, sem intenção de contratar
  -20 a classificação foi "borderline"
  -25 pede trabalho de graça, "por indicação", ou oferece pagamento apenas no
      êxito de uma causa duvidosa

Referências de calibragem:
  90+  cliente direto, imobiliário, caso concreto, UF atendida, contato no post
  70   parceria com colega para demanda empresarial bem definida
  50   demanda da área, mas com pouca informação para dimensionar
  30   borderline: pode ser da área, pode não ser
  10   passou por pouco, quase não vale o clique
"""

CLASSIFIER_SCHEMA = {
    "type": "object",
    "properties": {
        "category": {
            "type": "string",
            "enum": ["relevant", "borderline", "irrelevant"],
        },
        "reason": {"type": "string"},
        "tipo_demanda": {
            "type": "string",
            "enum": ["lead_cliente", "parceria_advogado", "vaga_emprego",
                     "divulgacao", "nao_informado"],
        },
        "categoria": {"type": "string", "enum": list(CATEGORIAS)},
        "uf": {"type": "string"},
        "comarca": {"type": "string"},
        "exige_presenca": {"type": "boolean"},
        "tem_contato": {"type": "boolean"},
        "language": {"type": "string", "enum": ["pt", "en", "es", "outro"]},
        "score": {"type": "integer"},
        "autor": {"type": "string"},
        "resumo_demanda": {"type": "string"},
        "summary": {"type": "string"},
        "valor": {"type": "string"},
    },
    "required": [
        "category", "reason", "tipo_demanda", "categoria", "uf", "comarca",
        "exige_presenca", "tem_contato", "language", "score", "autor",
        "resumo_demanda", "summary", "valor",
    ],
}


def load_profile() -> str | None:
    """Lê profile.md (cache por mtime — edições valem em runtime)."""
    global _profile_text, _profile_mtime
    if not PROFILE_FILE.exists():
        if _profile_text is not None:
            log.warning("Profile file %s was removed", PROFILE_FILE)
            _profile_text = None
            _profile_mtime = None
        return None
    mtime = PROFILE_FILE.stat().st_mtime
    if _profile_text is None or mtime != _profile_mtime:
        try:
            _profile_text = PROFILE_FILE.read_text(encoding="utf-8")
            _profile_mtime = mtime
            log.info("Loaded profile from %s (%d chars)", PROFILE_FILE, len(_profile_text))
        except OSError as exc:
            log.error("Failed to read profile %s: %s", PROFILE_FILE, exc)
            return None
    return _profile_text


def get_genai_client() -> Any | None:
    """Lazy init do client do Gemini. None se não configurado/disponível."""
    global _genai_client
    if not GENAI_AVAILABLE or not GEMINI_API_KEY:
        return None
    if _genai_client is None:
        try:
            _genai_client = genai.Client(api_key=GEMINI_API_KEY)
            log.info("Gemini client initialized (model=%s)", GEMINI_MODEL)
        except Exception as exc:  # noqa: BLE001
            log.error("Failed to init Gemini client: %s", exc)
            return None
    return _genai_client


# O free tier do Gemini tem DOIS tetos: por minuto e por dia. Estourar o de
# minuto é contornável com retry; o DIÁRIO não é — só volta no dia seguinte, e
# até lá os posts são adiados, e cada ciclo tenta de novo.
GEMINI_MAX_ATTEMPTS = int(os.getenv("GEMINI_MAX_ATTEMPTS", "4"))
GEMINI_RETRY_CAP_SECONDS = 65.0
_RETRY_DELAY_RE = re.compile(r"retryDelay['\"]?:\s*['\"]?(\d+(?:\.\d+)?)s")
_QUOTA_ID_RE = re.compile(r"quotaId['\"]?:\s*['\"]([^'\"]+)")
_QUOTA_VALUE_RE = re.compile(r"quotaValue['\"]?:\s*['\"]?(\d+)")


def _is_rate_limit(exc: Exception) -> bool:
    text = str(exc)
    return "RESOURCE_EXHAUSTED" in text or "429" in text


def _quota_scope(exc: Exception) -> str:
    """Distingue teto diário de teto por minuto: 'day', 'minute' ou '?'."""
    match = _QUOTA_ID_RE.search(str(exc))
    if not match:
        return "?"
    quota_id = match.group(1)
    if "PerDay" in quota_id:
        return "day"
    if "PerMinute" in quota_id:
        return "minute"
    return "?"


def _quota_value(exc: Exception) -> str:
    match = _QUOTA_VALUE_RE.search(str(exc))
    return match.group(1) if match else "?"


def _retry_delay_seconds(exc: Exception, attempt: int) -> float:
    """Usa o retryDelay que a própria API sugere; senão, backoff exponencial."""
    match = _RETRY_DELAY_RE.search(str(exc))
    if match:
        return min(float(match.group(1)) + 1.0, GEMINI_RETRY_CAP_SECONDS)
    return min(2.0 ** attempt, GEMINI_RETRY_CAP_SECONDS)


# ---------------------------------------------------------------------------
# Telemetria do dia
# ---------------------------------------------------------------------------

def _bump(key: str, fonte: str | None = None, n: int = 1) -> None:
    STATS.bump(hoje_local(), key, fonte, n=n)


def log_filter_stats() -> None:
    """Loga o placar do dia. Chamado ao fim de cada ciclo."""
    totais = STATS.totais()
    if not totais:
        return
    adiadas = totais.get("adiada", 0)
    line = (
        f"FILTRO (hoje {STATS.dia}): {totais.get('classified', 0)} classificados, "
        f"{totais.get('prefiltered', 0)} cortados no pre-filtro, "
        f"{totais.get('divulgacao', 0)} divulgacao, "
        f"{totais.get('local', 0)} fora das UFs, "
        f"{totais.get('ingles', 0)} em ingles, "
        f"{totais.get('categoria', 0)} de area desligada, "
        f"{totais.get('deduped', 0)} duplicados, "
        f"{totais.get('queued', 0)} enfileirados, "
        f"{totais.get('sent', 0)} publicados, "
        f"{totais.get('rate_limited', 0)} rate-limits, "
        f"{adiadas} ADIADOS por falha do classificador"
    )
    if adiadas:
        log.warning("%s  <-- classificador instavel; os adiados voltam no proximo ciclo",
                    line)
    else:
        log.info(line)


def _fallback_analysis(reason: str, falhou: bool = True) -> dict[str, Any]:
    """Análise vazia para quando o classificador está indisponível.

    **Aqui está uma inversão deliberada em relação ao projeto anterior.** Lá, o
    filtro fora do ar significava "aprova tudo": nunca se perdia uma vaga, e o
    estrago era contido porque havia teto de 8/dia e fila ordenada por nota — a
    vaga sem filtro nascia com nota 25 e só saía se não houvesse nada melhor.

    Neste projeto **não há teto**. "Aprova tudo" com o classificador fora do ar
    não significa uma vaga duvidosa no fim da fila: significa despejar no grupo
    da Milena todo advogado que se anunciou no Facebook naquela hora, que é
    exatamente o ruído que o produto existe para remover.

    Então o padrão aqui é o oposto: `falhou=True` faz o post **não ser marcado
    como visto**, e ele volta a ser classificado no ciclo seguinte. Post de
    Facebook não desaparece em dez minutos — dá para esperar. Não se perde nada
    e não se publica lixo; o custo é o atraso de um ciclo enquanto a API do
    Gemini estiver de pé de novo.
    """
    return {
        "category": "borderline",
        "reason": reason,
        "falhou": falhou,
        "tipo_demanda": "nao_informado",
        "categoria": "outro",
        "uf": "",
        "comarca": "",
        "exige_presenca": False,
        "tem_contato": False,
        "language": "pt",
        "score": 25,
        "autor": "",
        "resumo_demanda": "",
        "summary": "",
        "valor": "",
    }


def analyze_job(job: Job, cfg: dict[str, Any]) -> dict[str, Any]:
    """Classifica e extrai os dados de uma vaga com o Gemini.

    Em caso de erro ou falta de config, cai no fallback seguro
    (category='relevant') — nunca se perde vaga por falha do filtro.
    """
    profile = load_profile()
    client = get_genai_client()

    if profile is None or client is None:
        _bump("adiada", job.source)
        return _fallback_analysis("filtro desativado (sem profile.md ou GEMINI_API_KEY)")

    prompt = (
        f"{CLASSIFIER_INSTRUCTIONS}\n\n"
        f"=== PERFIL DO USUÁRIO ===\n{profile}\n\n"
        f"=== VAGA A ANALISAR ===\n{job.to_classifier_text()}"
    )

    data: dict[str, Any] | None = None
    for attempt in range(1, GEMINI_MAX_ATTEMPTS + 1):
        try:
            resp = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=genai_types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=CLASSIFIER_SCHEMA,
                    temperature=0.1,
                ),
            )
            data = json.loads(resp.text)
            _bump("classified", job.source)
            break
        except Exception as exc:  # noqa: BLE001
            if _is_rate_limit(exc):
                _bump("rate_limited", job.source)

                # Cota diária esgotada: retry é inútil, só volta amanhã.
                if _quota_scope(exc) == "day":
                    if STATS.marcar_cota_anunciada():
                        log.error(
                            "COTA DIARIA DO GEMINI ESGOTADA (limite=%s/dia, model=%s). "
                            "A partir de agora os posts sao ADIADOS ate a cota "
                            "resetar — nada e publicado sem filtro. Gerar uma chave "
                            "propria para este projeto ou migrar para modelo pago.",
                            _quota_value(exc), GEMINI_MODEL,
                        )
                    _bump("adiada", job.source)
                    return _fallback_analysis(
                        "cota diária do Gemini esgotada — post adiado para o próximo ciclo"
                    )

                if attempt < GEMINI_MAX_ATTEMPTS:
                    delay = _retry_delay_seconds(exc, attempt)
                    log.warning(
                        "Rate limit por minuto na vaga %s (tentativa %d/%d) — aguardando %.0fs",
                        job.uid, attempt, GEMINI_MAX_ATTEMPTS, delay,
                    )
                    time.sleep(delay)
                    continue

            log.error("Classificação falhou em %s: %s — post adiado, será "
                      "reavaliado no próximo ciclo", job.uid, exc)
            _bump("adiada", job.source)
            return _fallback_analysis(f"erro no classificador ({type(exc).__name__})")

    if data is None:
        _bump("adiada", job.source)
        return _fallback_analysis("classificador sem resposta")

    category: Category = data.get("category", "borderline")
    reason: str = (data.get("reason") or "").strip()[:200]
    if category not in ("relevant", "borderline", "irrelevant"):
        log.warning("Analysis returned invalid category %r for %s", category, job.uid)
        category = "borderline"
        reason = reason or "categoria inválida do classificador"

    tipo_demanda = data.get("tipo_demanda") or "nao_informado"
    if tipo_demanda not in ("lead_cliente", "parceria_advogado", "vaga_emprego",
                            "divulgacao", "nao_informado"):
        log.warning("tipo_demanda inválido %r em %s", tipo_demanda, job.uid)
        tipo_demanda = "nao_informado"

    language = data.get("language") or "pt"
    exige_presenca = bool(data.get("exige_presenca"))
    tem_contato = bool(data.get("tem_contato"))

    categoria = data.get("categoria") or "outro"
    if categoria not in CATEGORIAS:
        log.warning("Categoria inválida %r em %s — usando 'outro'", categoria, job.uid)
        categoria = "outro"

    # UF: o modelo às vezes devolve o nome do estado por extenso ou uma sigla
    # que não existe. Sigla desconhecida vira vazio — "não sei onde é" é uma
    # resposta honesta, "PB" inventado num caso de Santos não é.
    uf = (data.get("uf") or "").strip().upper()[:2]
    if uf and uf not in UFS_VALIDAS:
        log.debug("UF inválida %r em %s — tratando como não informada", uf, job.uid)
        uf = ""
    # O grupo tem uma UF presumida (`job.location`). Ela só entra se o texto do
    # post não disser nada: o que está escrito ganha sempre do que foi presumido.
    if not uf:
        presumida = (job.location or "").strip().upper()[:2]
        if presumida in UFS_VALIDAS:
            uf = presumida

    try:
        score = max(0, min(100, int(data.get("score", 50))))
    except (TypeError, ValueError):
        score = 50

    analise = {
        "category": category,
        "reason": reason,
        "tipo_demanda": tipo_demanda,
        "categoria": categoria,
        "uf": uf,
        "comarca": (data.get("comarca") or "").strip()[:120],
        "exige_presenca": exige_presenca,
        "tem_contato": tem_contato,
        "language": language,
        "score": score,
        "autor": (data.get("autor") or "").strip()[:120],
        "resumo_demanda": (data.get("resumo_demanda") or "").strip()[:80],
        "summary": (data.get("summary") or "").strip()[:300],
        "valor": (data.get("valor") or "").strip()[:80],
    }

    # As regras duras rodam DEPOIS do classificador e passam por cima da
    # categoria dele. O modelo é bom lendo o post e ruim obedecendo regra
    # absoluta — então quem lê é ele, quem decide é o código.

    # A regra que sustenta o produto: advogado se anunciando não é oportunidade.
    if regra(cfg, job.source, "rejeitar_divulgacao") and tipo_demanda == "divulgacao":
        log.info("Post %s cortado: divulgação de advogado", job.uid)
        analise["category"] = "irrelevant"
        analise["reason"] = "advogado/escritório se anunciando — não é demanda"
        analise["motivo_corte"] = "divulgacao"
        return analise

    ok_local, motivo_local = local_aceito(cfg, job.source, uf, exige_presenca)
    if not ok_local:
        log.info("Post %s cortado: %s", job.uid, motivo_local)
        analise["category"] = "irrelevant"
        analise["reason"] = motivo_local
        analise["motivo_corte"] = "local"
        return analise

    if regra(cfg, job.source, "reject_english") and language == "en":
        log.info("Post %s cortado: escrito em inglês", job.uid)
        analise["category"] = "irrelevant"
        analise["reason"] = "post escrito em inglês"
        analise["motivo_corte"] = "ingles"
        return analise

    if not categoria_ligada(cfg, job.source, categoria):
        log.info("Job %s dropped: categoria %s desligada em %s",
                 job.uid, categoria, job.source)
        analise["category"] = "irrelevant"
        analise["reason"] = f"categoria '{categoria}' desligada para esta fonte"
        analise["motivo_corte"] = "categoria"
        return analise

    return analise


def log_skipped_job(job: Job, analysis: dict[str, Any]) -> None:
    """Anexa o post descartado em skipped_jobs.jsonl pra revisão posterior.

    É o arquivo que responde "o filtro está apertado demais?". Guarda o texto
    do post junto, e não só o motivo: ler a recusa sem ler o que foi recusado
    não permite julgar se a recusa estava certa.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    entry = {
        "skipped_at": datetime.now(timezone.utc).isoformat(),
        "uid": job.uid,
        "source": job.source,
        "title": job.title,
        "reason": analysis.get("reason", ""),
        "motivo_corte": analysis.get("motivo_corte", ""),
        "tipo_demanda": analysis.get("tipo_demanda", ""),
        "categoria": analysis.get("categoria", ""),
        "uf": analysis.get("uf", ""),
        "exige_presenca": analysis.get("exige_presenca", False),
        "score": analysis.get("score", 0),
        "texto": (job.description or "")[:600],
        "url": job.url,
    }
    try:
        with SKIPPED_LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as exc:
        log.error("Failed to log skipped job %s: %s", job.uid, exc)


def registrar(job: Job, status: str, analysis: dict[str, Any] | None = None) -> None:
    """Espelha o destino do post no Postgres, para o painel. No-op sem banco."""
    a = analysis or {}
    STORE.registrar_evento(
        uid=job.uid, source=job.source, status=status, local_day=hoje_local(),
        title=job.title, autor=a.get("autor") or job.company, url=job.url,
        category=a.get("category", ""), categoria=a.get("categoria", ""),
        tipo_demanda=a.get("tipo_demanda", ""),
        resumo_demanda=a.get("resumo_demanda", ""),
        uf=a.get("uf", ""), comarca=a.get("comarca", ""),
        exige_presenca=bool(a.get("exige_presenca")),
        tem_contato=bool(a.get("tem_contato")),
        valor=a.get("valor", ""), language=a.get("language", ""),
        score=int(a.get("score") or 0), reason=a.get("reason", ""),
        published_at=job.published_at,
    )


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

CHAT_ID_FILE = DATA_DIR / "chat_id.json"


def _carregar_chat_id() -> str:
    """Chat de destino, já com a migração para supergrupo aplicada, se houve.

    Só honra o valor salvo se ele **descende** do `TELEGRAM_CHAT_ID` atual. Sem
    essa checagem, trocar o grupo no Coolify não teria efeito: o bot continuaria
    publicando no grupo antigo por causa de um arquivo no volume, e o sintoma
    seria "mudei a variável e nada aconteceu".
    """
    if not CHAT_ID_FILE.exists():
        return TELEGRAM_CHAT_ID
    try:
        dados = json.loads(CHAT_ID_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, ValueError, OSError):
        return TELEGRAM_CHAT_ID
    salvo = str(dados.get("chat_id") or "")
    if salvo and str(dados.get("migrado_de") or "") == TELEGRAM_CHAT_ID:
        log.info("Chat de destino migrado: %s → %s (registrado em %s)",
                 TELEGRAM_CHAT_ID, salvo, dados.get("em", "?"))
        return salvo
    return TELEGRAM_CHAT_ID


CHAT_ID_ATUAL = _carregar_chat_id()


def _gravar_chat_id(novo: str) -> None:
    global CHAT_ID_ATUAL
    CHAT_ID_ATUAL = str(novo)
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        CHAT_ID_FILE.write_text(json.dumps({
            "chat_id": str(novo),
            "migrado_de": TELEGRAM_CHAT_ID,
            "em": datetime.now(timezone.utc).isoformat(),
        }), encoding="utf-8")
    except OSError as exc:
        log.error("Não consegui gravar o chat_id novo em %s: %s", CHAT_ID_FILE, exc)


def _migrou_para(resp: requests.Response) -> str:
    """Lê o `migrate_to_chat_id` de um erro do Telegram, se for esse o caso.

    Um grupo básico vira supergrupo sozinho — ao passar de 200 membros, ao ganhar
    username público, ao ter o histórico aberto para novos membros. Quando isso
    acontece **o chat ID muda**, o antigo morre e o Telegram responde 400 com o
    ID novo dentro de `parameters`. Sem tratar isso, o bot para de publicar e o
    único sintoma é o silêncio — que é o modo de falha mais caro que existe num
    produto cujo valor é chegar mensagem.
    """
    try:
        dados = resp.json()
    except ValueError:
        return ""
    novo = (dados.get("parameters") or {}).get("migrate_to_chat_id")
    return str(novo) if novo else ""


def send_telegram(text: str, chat_id: str | int | None = None,
                  _pode_retentar: bool = True) -> int | None:
    """Publica a mensagem. Devolve o `message_id`, que é o que dá o link dela.

    O painel não linka para o post na origem — linka para a mensagem no grupo.
    Guardar esse id é o que torna isso possível: sem ele, depois de publicada a
    mensagem não teria como ser reencontrada.
    """
    destino = CHAT_ID_ATUAL if chat_id is None else chat_id
    resp = requests.post(
        TELEGRAM_URL,
        json={
            "chat_id": destino,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=REQUEST_TIMEOUT,
    )
    if not resp.ok:
        novo = _migrou_para(resp)
        if novo and chat_id is None and _pode_retentar:
            log.warning("O grupo virou supergrupo: chat %s → %s. Regravando e "
                        "reenviando esta mensagem.", destino, novo)
            _gravar_chat_id(novo)
            return send_telegram(text, None, _pode_retentar=False)
        log.error("Telegram error %s: %s", resp.status_code, resp.text)
        resp.raise_for_status()
    try:
        return int(resp.json()["result"]["message_id"])
    except (ValueError, KeyError, TypeError):
        # A mensagem foi entregue; só não deu para ler o id. Não é motivo para
        # tratar o envio como falho e reenviar o post.
        return None


def apagar_mensagem(message_id: int) -> bool:
    """Apaga uma mensagem do grupo. O bot é admin com `can_delete_messages`,
    então não vale o limite de 48h que existe para bot comum."""
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/deleteMessage",
            json={"chat_id": CHAT_ID_ATUAL, "message_id": message_id},
            timeout=REQUEST_TIMEOUT,
        )
        if r.ok:
            return True
        # "message to delete not found" = alguém já apagou. Missão cumprida.
        if "not found" in r.text.lower():
            return True
        log.warning("deleteMessage %s falhou: %s", message_id, r.text[:150])
    except requests.RequestException as exc:
        log.warning("deleteMessage %s falhou: %s", message_id, exc)
    return False


def marcar_mensagem_encerrada(message_id: int, html_original: str) -> bool:
    """Reescreve a mensagem avisando que a vaga saiu do ar.

    Preferido ao apagar como padrão: quem já tinha visto a vaga entende o que
    aconteceu, em vez de achar que a mensagem sumiu do nada. O texto original
    fica riscado, então o histórico do grupo continua fazendo sentido.
    """
    aviso = "🔴 <b>POST REMOVIDO</b> — não está mais disponível no Facebook"
    corpo = f"{aviso}\n\n<s>{_sem_tags(html_original)}</s>"
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/editMessageText",
            json={"chat_id": CHAT_ID_ATUAL, "message_id": message_id,
                  "text": corpo[:4000], "parse_mode": "HTML",
                  "disable_web_page_preview": True},
            timeout=REQUEST_TIMEOUT,
        )
        if r.ok or "not modified" in r.text.lower():
            return True
        log.warning("editMessageText %s falhou: %s", message_id, r.text[:150])
    except requests.RequestException as exc:
        log.warning("editMessageText %s falhou: %s", message_id, exc)
    return False


_TAGS_RE = re.compile(r"</?(?:b|i|s|u|code|pre)>")


def _sem_tags(html_texto: str) -> str:
    """Tira a formatação interna antes de riscar tudo.

    O `<s>` não pode envolver `<b>` aninhado sem o Telegram reclamar de HTML
    inválido, e o link vira texto para não convidar ao clique numa vaga morta.
    """
    limpo = _TAGS_RE.sub("", html_texto)
    limpo = re.sub(r'<a href="[^"]*">([^<]*)</a>', lambda m: m.group(1), limpo)
    return limpo


def is_transient_send_error(exc: requests.RequestException) -> bool:
    """Diz se vale retentar o envio no próximo ciclo.

    Falha de rede, timeout, 429 e 5xx são passageiros — a vaga volta para a
    fila. Um 4xx (fora o 429) é problema da mensagem em si (HTML inválido, chat
    errado, bot removido do grupo): retentar todo ciclo só repetiria o mesmo
    erro para sempre e travaria as vagas seguintes.
    """
    resp = exc.response
    if resp is None:
        return True
    return resp.status_code == 429 or resp.status_code >= 500


def truncate_description(text: str, max_chars: int = DESCRIPTION_MAX_CHARS) -> str:
    text = (text or "").strip()
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    space = cut.rfind(" ")  # corta na última palavra completa
    if space > 0:
        cut = cut[:space]
    return cut.rstrip(" ,.;:-") + "..."


TIPO_LABELS: dict[str, str] = {
    "lead_cliente": "🔎 CLIENTE",
    "parceria_advogado": "🤝 PARCERIA",
    "vaga_emprego": "💼 VAGA",
    "nao_informado": "📄 DEMANDA",
}

AREA_LABELS: dict[str, str] = {
    "imobiliario": "Imobiliário",
    "condominio": "Condomínio",
    "empresarial": "Empresarial",
    "contratos": "Contratos",
    "cobranca": "Cobrança",
    "trabalhista_empresa": "Trabalhista (empresa)",
    "tributario_fiscal": "Tributário / Fiscal",
    "outro": "Outra área",
}


def _data_legivel(published_at: str) -> str:
    """ISO → "12/08 às 14:32". Devolve o cru se não for ISO."""
    try:
        return datetime.fromisoformat(published_at).strftime("%d/%m às %H:%M")
    except (TypeError, ValueError):
        return published_at


def format_job(job: Job, analysis: dict[str, Any] | None = None,
               source_label: str = "") -> str:
    """Monta a mensagem do grupo.

    Princípio, herdado como ideia do projeto anterior e aplicado aqui desde o
    começo: **linha sem informação é omitida, não escrita**. Um post de Facebook
    tem quase sempre metade dos campos vazios; escrever "Valor não informado" e
    "Local não informado" em toda mensagem transforma o grupo num mural de
    ausências e faz a informação que existe desaparecer no meio.
    """
    analysis = analysis or _fallback_analysis("", falhou=False)
    category = analysis.get("category", "relevant")
    tipo = analysis.get("tipo_demanda", "nao_informado")

    cabecalho = TIPO_LABELS.get(tipo, TIPO_LABELS["nao_informado"])
    if category == "borderline":
        cabecalho = f"🤔 {cabecalho} (talvez)"
    if source_label:
        cabecalho = f"{cabecalho} · {html.escape(source_label)}"

    # O título é o resumo da demanda quando o classificador conseguiu produzir
    # um: "usucapião de terreno" diz mais que a primeira linha crua do post.
    resumo_demanda = analysis.get("resumo_demanda") or ""
    titulo = html.escape(resumo_demanda.capitalize() or job.title or "(post sem título)")

    linhas_meta: list[str] = []

    autor = analysis.get("autor") or job.company
    if autor:
        linhas_meta.append(f"👤 {html.escape(autor)}")

    area = AREA_LABELS.get(analysis.get("categoria", ""), "")
    if area:
        linhas_meta.append(f"⚖️ {html.escape(area)}")

    # Local: comarca é mais útil que a sigla, mas as duas juntas evitam a dúvida
    # de "Campinas de qual estado?".
    comarca = analysis.get("comarca") or ""
    uf = analysis.get("uf") or ""
    if comarca and uf:
        local = f"{comarca} ({uf})"
    else:
        local = comarca or uf
    if local:
        presenca = " · presencial" if analysis.get("exige_presenca") else ""
        linhas_meta.append(f"📍 {html.escape(local)}{presenca}")
    elif analysis.get("exige_presenca"):
        linhas_meta.append("📍 Exige presença — local não informado")

    if analysis.get("valor"):
        linhas_meta.append(f"💰 {html.escape(analysis['valor'])}")

    if analysis.get("tem_contato"):
        linhas_meta.append("📞 Contato direto no post")

    if job.published_at:
        linhas_meta.append(f"📅 {html.escape(_data_legivel(job.published_at))}")

    descricao = analysis.get("summary") or truncate_description(job.description)

    linhas = [cabecalho, "", f"📌 <b>{titulo}</b>", ""]
    linhas.extend(linhas_meta)
    if descricao:
        linhas.extend(["", f"<i>{html.escape(descricao)}</i>"])
    if category == "borderline" and analysis.get("reason"):
        linhas.extend(["", f"<i>🤖 Motivo: {html.escape(analysis['reason'])}</i>"])
    linhas.extend(["", f'🔗 <a href="{job.url}">Ver post no Facebook</a>'])

    return "\n".join(linhas)


# ---------------------------------------------------------------------------
# Relatório diário
# ---------------------------------------------------------------------------

def montar_relatorio(fontes: list[Any], cfg: dict[str, Any],
                     titulo: str = "Relatório do dia") -> str:
    """Monta o resumo do dia: quantas vagas foram ao grupo, e de onde vieram.

    O relatório é do cliente, não nosso: quantas demandas o grupo recebeu hoje
    e a quebra delas por área. Nada de recusadas, descartadas, repetidas,
    cortadas no pré-filtro ou saúde da cota de IA — isso é diagnóstico interno,
    e no meio do relatório dela só serviria pra fazer o número bom parecer
    pequeno.

    Os contadores continuam todos existindo (`telemetry.py`) e continuam
    visíveis no painel, que é o lugar certo pra diagnóstico.
    """
    hoje = hoje_local()
    STATS.virar_se_preciso(hoje)
    totais = STATS.totais()

    agora = agora_local()
    linhas = [f"📊 <b>{html.escape(titulo)}</b> — {agora.strftime('%d/%m/%Y')}", ""]

    enviadas = totais.get("sent", 0)
    limite = _limite_diario(cfg)
    if not enviadas:
        linhas.append("😴 Nenhuma demanda nova hoje")
    elif limite > 0:
        linhas.append(f"✅ <b>{enviadas}</b> de {limite} demanda(s) enviadas ao grupo")
    else:
        linhas.append(f"✅ <b>{enviadas}</b> demanda(s) enviadas ao grupo")

    # A quebra é por ÁREA, não por fonte. No projeto anterior a fonte era a
    # informação útil porque havia quatro; aqui só existe o Facebook, e "Facebook:
    # 7" não responde nada. O que a Milena quer saber é de onde está vindo o
    # trabalho — se o mês inteiro veio de imobiliário, isso é uma decisão de
    # negócio, não uma estatística.
    areas = [
        (AREA_LABELS.get(c, c), totais.get(f"area:{c}", 0)) for c in CATEGORIAS
    ]
    areas = [(rotulo, n) for rotulo, n in areas if n]
    if areas:
        linhas.extend(["", "<b>Por área</b>"])
        for rotulo, n in sorted(areas, key=lambda item: -item[1]):
            linhas.append(f"• {html.escape(rotulo)}: {n}")

    # Um aviso que só aparece quando importa. Post adiado não some — mas se isso
    # virar rotina, é sinal de que a cota do Gemini não está aguentando.
    adiadas = totais.get("adiada", 0)
    if adiadas:
        linhas.extend([
            "",
            f"⚠️ <i>{adiadas} post(s) não puderam ser analisados hoje "
            f"(classificador indisponível). Voltam sozinhos no próximo ciclo.</i>",
        ])

    return "\n".join(linhas)


def alertar_operacao(texto: str) -> None:
    """Aviso de manutenção, para o privado de quem cuida — nunca para o grupo.

    O grupo é o produto. "A sessão do Facebook expirou" não é informação para a
    Milena: é tarefa para quem mantém. Misturar as duas coisas ensina o cliente
    a ignorar as mensagens do bot, que é o oposto do que se quer.
    """
    if not REPORT_CHAT_IDS:
        log.warning("Sem REPORT_CHAT_IDS — este alerta ficou só no log: %s", texto)
        return
    corpo = f"🛠 <b>ADV Jobs — operação</b>\n\n{html.escape(texto)}"
    for destino in REPORT_CHAT_IDS:
        try:
            send_telegram(corpo, chat_id=destino)
        except requests.RequestException as exc:
            log.error("Falha enviando alerta de operação para %s: %s", destino, exc)


def enviar_relatorio(fontes: list[Any], cfg: dict[str, Any],
                     titulo: str = "Relatório do dia") -> bool:
    """Manda o resumo do dia. Devolve True se pelo menos um destino recebeu.

    O retorno existe para o laço não marcar como enviado um relatório que o
    Telegram recusou: marcar mesmo assim custa o relatório do dia inteiro por
    causa de uma falha de rede de trinta segundos. Falhou, ele tenta de novo no
    ciclo seguinte.
    """
    texto = montar_relatorio(fontes, cfg, titulo)

    destinos: list[str | int]
    if REPORT_TO in ("privado", "private", "dm"):
        destinos = list(REPORT_CHAT_IDS)
        if not destinos:
            log.warning("REPORT_TO=privado mas REPORT_CHAT_IDS está vazio — "
                        "mandando pro grupo")
            destinos = [CHAT_ID_ATUAL]
    else:
        destinos = [CHAT_ID_ATUAL]

    entregue = False
    for destino in destinos:
        try:
            send_telegram(texto, chat_id=destino)
            log.info("Relatório enviado para %s.", destino)
            entregue = True
        except requests.RequestException as exc:
            log.error("Falha enviando relatório para %s: %s", destino, exc)
    return entregue


# ---------------------------------------------------------------------------
# Fontes ativas
# ---------------------------------------------------------------------------

def _interval_for(nome: str, default: int) -> int:
    """Intervalo da fonte, sobrescrevível por `INTERVAL_<FONTE>` no ambiente."""
    bruto = os.getenv(f"INTERVAL_{nome.upper()}", "").strip()
    if not bruto:
        return default
    try:
        # Piso de 60s: intervalo menor que isso só serve pra tomar bloqueio.
        return max(60, int(bruto))
    except ValueError:
        log.warning("INTERVAL_%s=%r não é número — usando %ds", nome.upper(), bruto, default)
        return default


def build_sources() -> list[Any]:
    """Monta a lista de fontes ativas a partir da env `SOURCES`."""
    pedidas = [s.strip().lower() for s in ENABLED_SOURCES.split(",") if s.strip()]
    ativas: list[Any] = []

    for nome in pedidas:
        if nome == "facebook":
            ativas.append(FacebookSource(
                groups_file=FACEBOOK_GROUPS_FILE,
                state_file=FACEBOOK_STATE_FILE,
                interval_seconds=_interval_for("facebook", FacebookSource.default_interval),
                max_posts_por_grupo=FACEBOOK_MAX_POSTS,
                scrolls=FACEBOOK_SCROLLS,
                headless=_flag("FACEBOOK_HEADLESS", True),
            ))
        elif nome == "gupy":
            ativas.append(GupySource(
                terms_file=TERMS_FILE,
                interval_seconds=_interval_for("gupy", GupySource.default_interval),
            ))
        elif nome == "indeed":
            ativas.append(IndeedSource(
                terms_file=TERMS_FILE,
                interval_seconds=_interval_for("indeed", IndeedSource.default_interval),
            ))
        elif nome == "linkedin":
            ativas.append(LinkedInSource(
                terms_file=LINKEDIN_TERMS_FILE,
                interval_seconds=_interval_for("linkedin", LinkedInSource.default_interval),
            ))
        else:
            log.warning("Fonte desconhecida em SOURCES: %r — ignorando", nome)

    if not ativas:
        log.error("Nenhuma fonte ativa. Ajuste a variável SOURCES (atual: %r)", ENABLED_SOURCES)
        sys.exit(1)
    return ativas


def coletar(fontes: list[Any], cfg: dict[str, Any]) -> tuple[list[Job], set[str]]:
    """Busca em todas as fontes. Uma que falhe não derruba as outras.

    Retorna as vagas e o conjunto de fontes que responderam **sem erro** — uma
    fonte que só falhou não pode ser dada como inicializada, senão na próxima
    vez que ela responder o catálogo inteiro dela vira "novidade".
    """
    todas: list[Job] = []
    ok: set[str] = set()
    agora = time.monotonic()

    for fonte in fontes:
        # Desligada no painel: nem consulta.
        if not fonte_ligada(cfg, fonte.name):
            continue
        if not fonte.is_due(agora):
            continue
        # Marca antes de tentar: se a fonte estiver com problema, ela espera o
        # intervalo dela em vez de ser martelada a cada ciclo.
        fonte.mark_fetched(agora)

        inicio = time.monotonic()
        try:
            jobs = fonte.fetch()
        except AuthError as exc:
            # Sessão do Facebook morta. É a única falha deste bot que NÃO se
            # resolve sozinha e que não dá sintoma nenhum: sem sessão o feed
            # volta vazio, o bot segue rodando feliz e o grupo simplesmente
            # emudece. Por isso vira alerta no privado de quem mantém, uma vez
            # por dia, em vez de mais uma linha de log que ninguém lê.
            log.error("Fonte %s sem sessão válida: %s", fonte.name, exc)
            if STATS.marcar_alerta(f"auth:{fonte.name}", hoje_local()):
                alertar_operacao(
                    f"A sessão do {getattr(fonte, 'label', fonte.name)} expirou e o "
                    f"bot parou de coletar dessa fonte.\n\n"
                    f"Para resolver: rodar `python bot/tools/facebook_login.py` "
                    f"na máquina local e subir o fb_state.json novo para o volume.\n\n"
                    f"Detalhe técnico: {exc}"
                )
            continue
        except (SourceError, requests.RequestException) as exc:
            log.error("Fonte %s falhou: %s — seguindo com as outras", fonte.name, exc)
            continue
        except Exception as exc:  # noqa: BLE001
            log.exception("Fonte %s quebrou de forma inesperada: %s", fonte.name, exc)
            continue

        fonte.last_count = len(jobs)
        log.info("Fonte %s: %d vagas em %.1fs", fonte.name, len(jobs), time.monotonic() - inicio)
        todas.extend(jobs)
        ok.add(fonte.name)
    return todas, ok


# ---------------------------------------------------------------------------
# Despacho: tira da fila e publica
# ---------------------------------------------------------------------------

# Teto por tick, só para o caso de a fila estar entupida: sem teto diário, um
# ciclo que encontrasse 200 posts tentaria mandar 200 mensagens seguidas e
# tomaria rate limit do Telegram. O resto sai no tick seguinte.
MAX_ENVIOS_POR_TICK = 12


def _limite_diario(cfg: dict[str, Any]) -> int:
    """Teto do dia, com 0 significando SEM TETO.

    Escrito assim de propósito: `cfg.get("daily_limit") or DAILY_LIMIT` — que é
    o idioma natural — trata o 0 do painel como "não definido" e cai no valor do
    ambiente. Ou seja, desligar o teto no painel não teria efeito nenhum, e o
    sintoma seria "mudei e continua limitando".
    """
    valor = cfg.get("daily_limit")
    return DAILY_LIMIT if valor is None else int(valor)


def despachar(cfg: dict[str, Any]) -> None:
    """Publica o que estiver maduro na fila.

    Com teto diário, a fila devolve no máximo uma por tick e o espaçamento faz o
    gotejamento. Sem teto — o padrão deste projeto — ela devolve enquanto
    houver, até `MAX_ENVIOS_POR_TICK`, para uma rajada de demandas chegar junta
    em vez de pingar uma a cada dez minutos.
    """
    for _ in range(MAX_ENVIOS_POR_TICK):
        if not _despachar_um(cfg):
            return


def _despachar_um(cfg: dict[str, Any]) -> bool:
    """Publica uma da fila. Devolve False quando não há mais o que publicar."""
    agora = agora_local()
    hoje = hoje_local()

    item = FILA.proxima(
        agora=agora,
        hoje=hoje,
        limite_diario=_limite_diario(cfg),
        janela_inicio=int(cfg.get("window_start") if cfg.get("window_start") is not None
                          else SEND_WINDOW_START),
        janela_fim=int(cfg.get("window_end") if cfg.get("window_end") is not None
                       else SEND_WINDOW_END),
    )
    if item is None:
        return False

    try:
        message_id = send_telegram(item["html"])
    except requests.RequestException as exc:
        if is_transient_send_error(exc):
            log.error("Falha publicando %s: %s — volta pra fila", item["uid"], exc)
            FILA.devolver(item)
        else:
            log.error("Falha publicando %s: %s — erro permanente, post descartado",
                      item["uid"], exc)
            _bump("falhou", item.get("source"))
        # Nos dois casos para o tick aqui: insistir depois de uma falha de envio
        # só multiplica o mesmo erro e, se for rate limit, piora o bloqueio.
        return False

    FILA.confirmar_envio(agora, hoje)
    _bump("sent", item.get("source"))
    # Contador por área, que é a quebra do relatório diário.
    _bump(f"area:{item.get('categoria') or 'outro'}")

    # Guarda o suficiente para revisitar a vaga depois e, se ela tiver saído do
    # ar, alcançar esta mensagem no grupo.
    PUBLICADAS.registrar(
        uid=item["uid"], source=item.get("source", ""),
        source_id=item["uid"].split(":", 1)[-1], title=item.get("title", ""),
        message_id=message_id, agora=agora, html=item.get("html", ""),
        url=item.get("url", ""),
    )
    log.info("Publicada %s (nota %s) — %s",
             item["uid"], item.get("score"), item.get("title", "")[:70])

    STORE.registrar_evento(
        uid=item["uid"], source=item.get("source", ""), status="sent",
        local_day=hoje, title=item.get("title", ""),
        score=int(item.get("score") or 0), category=item.get("category", ""),
        published_at=item.get("published_at", ""),
        categoria=item.get("categoria", ""),
        tipo_demanda=item.get("tipo_demanda", ""),
        telegram_message_id=message_id,
    )

    # A API do Telegram tolera cerca de uma mensagem por segundo no mesmo chat.
    # Sem teto diário, uma rajada bate nesse limite sem esta pausa.
    time.sleep(TELEGRAM_RATE_LIMIT_SECONDS)
    return True


# ---------------------------------------------------------------------------
# Revisor: a vaga publicada ainda está aberta?
# ---------------------------------------------------------------------------

def revisar(fontes: list[Any], cfg: dict[str, Any]) -> None:
    """Reexamina alguns posts já publicados e trata os que saíram do ar.

    Roda a conta-gotas — alguns por ciclo, cada post no máximo uma vez por
    `recheck_horas`. No Facebook isso é mais caro que numa API: cada verificação
    é uma página carregada num navegador. O barateamento vem de duas coisas —
    um único navegador para o lote inteiro, e `a_checar` devolver lista vazia
    quando nada venceu, o que faz o navegador nem subir na maioria dos ciclos.

    O revisor **nunca** apaga por conta própria. Ele coleta evidência; quem
    decide é `publicadas.marcar_checada`, que exige duas confirmações seguidas.
    """
    agora = agora_local()
    acao = str(cfg.get("acao_vaga_encerrada") or ACAO_VAGA_ENCERRADA).lower()
    if acao == "nada":
        return

    pendentes = PUBLICADAS.a_checar(
        agora=agora,
        intervalo_horas=int(cfg.get("recheck_horas") or RECHECK_HORAS),
        limite=RECHECK_POR_CICLO,
    )
    if not pendentes:
        return

    # O Facebook não tem endpoint de detalhe: quem sabe dizer se o post ainda
    # existe é a própria fonte, com o navegador logado. Por isso ele é resolvido
    # aqui e não em `vitality.py`, que só fala HTTP.
    fonte_fb = next((f for f in fontes if f.name == "facebook"), None)
    itens_fb = [i for i in pendentes if i["source"] == "facebook"]
    estados_fb: dict[str, str] = {}
    if itens_fb and fonte_fb is not None:
        estados_fb = fonte_fb.verificar_posts(
            {i["uid"]: i.get("url", "") for i in itens_fb}
        )

    # O Indeed aceita várias chaves por requisição; as do lote são resolvidas de
    # uma vez só, o que derruba o custo dessa fonte a quase nada.
    chaves_indeed = [i["source_id"] for i in pendentes if i["source"] == "indeed"]
    estados_indeed = vitality.verificar_indeed(chaves_indeed) if chaves_indeed else {}

    encerradas = 0
    for item in pendentes:
        if item["source"] == "facebook":
            estado = estados_fb.get(item["uid"], "desconhecida")
        elif item["source"] == "indeed":
            estado = estados_indeed.get(item["source_id"], "desconhecida")
        else:
            estado = vitality.verificar(item["source"], item["source_id"])
        if estado == "desconhecida":
            continue  # nem conta como falta: não sabemos de nada

        virou = PUBLICADAS.marcar_checada(item["uid"], agora, estado == "fechada")
        if not virou:
            continue

        # Passou pelas confirmações necessárias: a vaga acabou mesmo.
        encerradas += 1
        message_id = int(item["message_id"])
        if acao == "apagar":
            ok = apagar_mensagem(message_id)
            verbo = "apagada"
        else:
            ok = marcar_mensagem_encerrada(message_id, item.get("html") or item["title"])
            verbo = "marcada como encerrada"
        log.info("Vaga %s encerrada na fonte — mensagem %s (%s)",
                 item["uid"], verbo, "ok" if ok else "falhou")

        STORE.marcar_encerrada(item["uid"], agora.isoformat())

    if encerradas:
        _bump("encerrada", None, n=encerradas)


# ---------------------------------------------------------------------------
# Loop principal
# ---------------------------------------------------------------------------

def check_new_jobs(fontes: list[Any], seen_uids: set[str], seen_keys: set[str],
                   inicializadas: set[str],
                   cfg: dict[str, Any]) -> tuple[set[str], set[str], set[str]]:
    """Executa um ciclo. Retorna (uids vistos, chaves vistas, fontes inicializadas)."""
    labels = {f.name: getattr(f, "label", f.name) for f in fontes}
    jobs, fontes_ok = coletar(fontes, cfg)
    if not fontes_ok:
        # Nenhuma fonte estava no horário dela — situação normal, não é erro.
        log.debug("Nenhuma fonte no horário neste tick.")
        return seen_uids, seen_keys, inicializadas
    if not jobs:
        log.warning("Fontes %s consultadas, nenhuma vaga retornada.",
                    ", ".join(sorted(fontes_ok)))
        return seen_uids, seen_keys, inicializadas

    uids_do_ciclo = {j.uid for j in jobs}

    # Fonte nova (ou primeira execução): registra o acervo existente SEM notificar.
    # Sem isto, ligar uma fonte despejaria o catálogo inteiro dela no grupo.
    novas = fontes_ok - inicializadas
    if novas:
        registradas = [j for j in jobs if j.source in novas]
        for j in registradas:
            seen_uids.add(j.uid)
            seen_keys.add(dedup_key(j))
        inicializadas |= novas
        log.info(
            "Primeira coleta de %s — %d vagas registradas sem notificar (evita flood).",
            ", ".join(sorted(novas)), len(registradas),
        )
        save_seen(seen_uids, seen_keys, inicializadas)

    novos = [j for j in jobs if j.uid not in seen_uids]
    if not novos:
        log.info("No new jobs (checked %d).", len(jobs))
        log_filter_stats()
        return seen_uids | uids_do_ciclo, seen_keys, inicializadas

    log.info("Found %d new job(s).", len(novos))

    # Mais antigo → mais recente. A ordem de publicação não depende mais disto
    # (quem ordena é a nota, na fila), mas mantém o log legível.
    novos.sort(key=lambda j: j.published_at or "")

    agora = agora_local()
    min_score = int(cfg.get("min_score") or 0)
    # Posts que o classificador não conseguiu avaliar. Ficam DE FORA de
    # `seen_uids` no fim, que é o que os traz de volta no próximo ciclo.
    adiados: set[str] = set()

    for job in novos:
        # 1) mesma vaga em outra fonte (ou já enviada antes)?
        chave = dedup_key(job)
        if chave in seen_keys:
            log.info("Job %s duplicada (já vista como %s) — ignorando", job.uid, chave[:40])
            _bump("deduped", job.source)
            seen_uids.add(job.uid)
            continue

        # 2) filtros de graça, antes de gastar cota de IA. A ordem é a do custo
        #    crescente e da certeza decrescente.
        if regra(cfg, job.source, "reject_english") and \
                filters.parece_ingles(job.title, job.description):
            log.info("Job %s cortado: anúncio em inglês (pré-filtro)", job.uid)
            _bump("ingles", job.source)
            registrar(job, "ingles")
            seen_uids.add(job.uid)
            seen_keys.add(chave)
            continue

        # Divulgação descarada — "atuo em todo o Brasil", "faço petições",
        # tabela de honorários. É o ruído dominante do grupo e o pré-filtro
        # de texto pega a maior parte de graça, antes de gastar cota de IA.
        # Só corta quando o sinal é forte; o caso duvidoso segue para o modelo.
        if regra(cfg, job.source, "rejeitar_divulgacao") and \
                filters.parece_divulgacao(job.description):
            log.info("Post %s cortado: divulgação (pré-filtro)", job.uid)
            _bump("divulgacao", job.source)
            registrar(job, "divulgacao")
            seen_uids.add(job.uid)
            seen_keys.add(chave)
            continue

        fonte = next((f for f in fontes if f.name == job.source), None)
        if fonte is not None and getattr(fonte, "prefilter_remote", False):
            if not parece_remoto(job):
                _bump("prefiltered", job.source)
                seen_uids.add(job.uid)
                seen_keys.add(chave)
                continue

        # 3) classificador
        analysis = analyze_job(job, cfg)

        # Classificador fora do ar: NÃO marca como visto. O post volta a ser
        # avaliado no próximo ciclo, em vez de ser publicado sem filtro. Ver a
        # justificativa longa em `_fallback_analysis`.
        if analysis.get("falhou"):
            adiados.add(job.uid)
            log.warning("Post %s adiado (%s) — será reavaliado no próximo ciclo",
                        job.uid, analysis.get("reason", ""))
            continue

        category = analysis["category"]
        log.info(
            "Post %s → %s (%s/%s, nota %s) — %s",
            job.uid, category, analysis["tipo_demanda"], analysis["categoria"],
            analysis["score"], analysis["reason"],
        )

        if category == "irrelevant":
            log_skipped_job(job, analysis)
            # Corte por regra dura tem contador próprio: é o que responde
            # "por que o grupo esvaziou?" no relatório.
            motivo = analysis.get("motivo_corte")
            _bump(motivo or "skipped", job.source)
            registrar(job, motivo or "skipped", analysis)
            seen_uids.add(job.uid)
            seen_keys.add(chave)
            continue

        if analysis["score"] < min_score:
            log.info("Job %s cortado por nota %s < %s", job.uid, analysis["score"], min_score)
            _bump("skipped", job.source)
            registrar(job, "skipped", analysis)
            seen_uids.add(job.uid)
            seen_keys.add(chave)
            continue

        # 4) aprovada — entra na fila e espera a vez
        FILA.push(
            uid=job.uid,
            source=job.source,
            title=job.title,
            html=format_job(job, analysis, labels.get(job.source, "")),
            score=analysis["score"],
            category=category,
            categoria=analysis.get("categoria", ""),
            tipo_demanda=analysis.get("tipo_demanda", ""),
            url=job.url,
            published_at=job.published_at,
            agora=agora,
        )
        _bump("queued", job.source)
        registrar(job, "queued", analysis)
        seen_uids.add(job.uid)
        seen_keys.add(chave)

    # Tudo que passou pelo ciclo vira "visto", MENOS o que foi adiado — senão o
    # post adiado por uma falha momentânea do classificador nunca mais seria
    # olhado, e a falha de um minuto viraria perda permanente.
    seen_uids |= (uids_do_ciclo - adiados)
    if adiados:
        log.warning("%d post(s) adiados neste ciclo — voltam no próximo",
                    len(adiados))
    save_seen(seen_uids, seen_keys, inicializadas)
    log_filter_stats()
    return seen_uids, seen_keys, inicializadas


def validate_env() -> None:
    missing = [
        name for name, val in {
            "TELEGRAM_TOKEN": TELEGRAM_TOKEN,
            "TELEGRAM_CHAT_ID": TELEGRAM_CHAT_ID,
        }.items() if not val
    ]
    if missing:
        log.error("Missing required env vars: %s", ", ".join(missing))
        sys.exit(1)


def semear_sessao_facebook() -> None:
    """Escreve a sessão do Facebook no volume a partir de FACEBOOK_STATE_B64.

    Só escreve quando o arquivo ainda não existe **ou** quando o conteúdo do
    ambiente mudou desde a última semeadura. A segunda condição não é firula: o
    Playwright reescreve o `fb_state.json` a cada ciclo com os cookies
    renovados, então sobrescrever a cada boot devolveria uma sessão velha ao
    volume e derrubaria a conta sozinho depois de alguns dias.

    Trocar a sessão em produção passa a ser: colar o novo base64 na variável e
    redeployar. Como o valor muda, a semeadura acontece.
    """
    if not FACEBOOK_STATE_B64:
        return

    marca = DATA_DIR / ".fb_state_seed"
    impressao = hashlib.sha256(FACEBOOK_STATE_B64.encode()).hexdigest()
    try:
        ja_semeada = marca.read_text(encoding="utf-8").strip()
    except OSError:
        ja_semeada = ""

    if FACEBOOK_STATE_FILE.exists() and ja_semeada == impressao:
        return

    try:
        bruto = base64.b64decode(FACEBOOK_STATE_B64, validate=True)
        dados = json.loads(bruto.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        log.error("FACEBOOK_STATE_B64 não é um storage_state válido (%s) — "
                  "a sessão do volume foi mantida como está", exc)
        return

    if not isinstance(dados, dict) or not dados.get("cookies"):
        log.error("FACEBOOK_STATE_B64 decodificou, mas sem cookies dentro — "
                  "a sessão do volume foi mantida como está")
        return

    try:
        FACEBOOK_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = FACEBOOK_STATE_FILE.with_suffix(".json.tmp")
        tmp.write_bytes(bruto)
        tmp.replace(FACEBOOK_STATE_FILE)
        marca.write_text(impressao, encoding="utf-8")
    except OSError as exc:
        log.error("Falha escrevendo a sessão do Facebook em %s: %s",
                  FACEBOOK_STATE_FILE, exc)
        return

    log.info("Sessão do Facebook semeada a partir do ambiente em %s (%d cookies)",
             FACEBOOK_STATE_FILE, len(dados.get("cookies") or []))


def main() -> None:
    semear_sessao_facebook()
    validate_env()
    fontes = build_sources()
    log.info("ADV Jobs Bot iniciando (tick=%ds, data_dir=%s)", CHECK_INTERVAL, DATA_DIR)
    log.info("Destino: chat %s%s", CHAT_ID_ATUAL,
             "" if CHAT_ID_ATUAL == TELEGRAM_CHAT_ID else f" (migrado de {TELEGRAM_CHAT_ID})")
    for f in fontes:
        log.info("  fonte %-9s a cada %4dmin", f.name, f.interval_seconds // 60)

    STORE.migrar()

    # Pré-carrega profile e client pra mostrar o status do filtro no startup
    profile = load_profile()
    client = get_genai_client()
    if profile and client:
        log.info("Filter ENABLED (model=%s, profile=%s)", GEMINI_MODEL, PROFILE_FILE)
    else:
        reasons = []
        if not GENAI_AVAILABLE:
            reasons.append("google-genai não instalado")
        elif not GEMINI_API_KEY:
            reasons.append("GEMINI_API_KEY ausente")
        if profile is None:
            reasons.append(f"{PROFILE_FILE} não encontrado")
        log.warning("Filter DISABLED — aprovando TUDO. Motivo(s): %s", "; ".join(reasons))

    STATS.virar_se_preciso(hoje_local())
    seen_uids, seen_keys, inicializadas = load_seen()
    pendentes = {f.name for f in fontes} - inicializadas
    if pendentes:
        log.info("Fontes ainda não inicializadas: %s", ", ".join(sorted(pendentes)))

    cfg = config_atual()
    limite = _limite_diario(cfg)
    log.info(
        "Volume: %s, enviando entre %dh e %dh, validade da fila %d dias",
        "SEM TETO diário" if limite <= 0 else f"até {limite} demanda(s)/dia",
        int(cfg.get("window_start") if cfg.get("window_start") is not None
            else SEND_WINDOW_START),
        int(cfg.get("window_end") if cfg.get("window_end") is not None
            else SEND_WINDOW_END),
        QUEUE_TTL_DAYS,
    )
    ufs = tuple(cfg.get("ufs_atendidas") or ())
    log.info(
        "Regras: divulgação=%s · inglês=%s · UFs=%s · sem local declarado=%s",
        "recusa" if cfg.get("rejeitar_divulgacao") else "aceita",
        "recusa" if cfg.get("reject_english") else "aceita",
        ", ".join(ufs) if ufs else "todas",
        "aceita" if cfg.get("aceitar_sem_local") else "recusa",
    )

    # O listener existe só para responder /start e /suporte a quem abrir o bot.
    # Não há mais nenhum comando que mexa no funcionamento — ver bot_control.py.
    CommandListener(
        token=TELEGRAM_TOKEN,
        site_url=SITE_URL,
        instagram_url=INSTAGRAM_URL,
        suporte_telegram=SUPORTE_TELEGRAM,
        chats_legados=CHATS_MENU_LEGADO,
    ).start()

    log.info("Relatório diário às %dh (%s), destino: %s",
             REPORT_HOUR, TIMEZONE_NAME, REPORT_TO)
    if ACAO_VAGA_ENCERRADA == "nada":
        log.info("Revisão de posts removidos: desligada")
    else:
        log.info("Post removido na origem: ação=%s · rechecagem a cada %dh · "
                 "%d por ciclo · acompanhando por %d dias",
                 ACAO_VAGA_ENCERRADA, RECHECK_HORAS, RECHECK_POR_CICLO,
                 RECHECK_DIAS)

    while True:
        cfg = config_atual()

        try:
            seen_uids, seen_keys, inicializadas = check_new_jobs(
                fontes, seen_uids, seen_keys, inicializadas, cfg
            )
        except requests.RequestException as exc:
            log.error("Network error: %s — will retry next cycle", exc)
        except Exception as exc:  # noqa: BLE001
            log.exception("Unexpected error: %s — will retry next cycle", exc)

        # Publica da fila, se for hora.
        try:
            despachar(cfg)
        except Exception as exc:  # noqa: BLE001
            log.exception("Erro no despacho: %s", exc)

        # Revê algumas vagas já publicadas: ainda estão abertas?
        try:
            revisar(fontes, cfg)
        except Exception as exc:  # noqa: BLE001
            log.exception("Erro na revisão de vagas: %s", exc)

        # Relatório do dia. O dia é o LOCAL, não o UTC — era essa confusão que
        # fazia o relatório sair com números de uma hora só.
        try:
            agora = agora_local()
            dia = hoje_local()
            if agora.hour >= REPORT_HOUR and STATS.relatorio_pendente(dia):
                if enviar_relatorio(fontes, cfg):
                    STATS.marcar_relatorio_enviado(dia)
        except Exception as exc:  # noqa: BLE001
            log.exception("Erro no relatório diário: %s", exc)

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
