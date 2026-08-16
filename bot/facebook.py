"""Fonte Facebook — lê posts de grupos e devolve `Job` normalizado.

Esta é a única fonte do projeto que **não** tem API. O Facebook desligou o acesso
de terceiros a grupos em 2018 e nunca devolveu; o que existe hoje é a página,
renderizada por JavaScript, atrás de login. Então o caminho é um navegador de
verdade com uma sessão de verdade.

Três consequências que moldam este arquivo inteiro:

1. **Sessão em arquivo, nunca senha no código.** O bot não faz login: ele carrega
   um `storage_state` salvo por `tools/facebook_login.py`, que abre um navegador
   com janela para a pessoa entrar na mão — inclusive com 2FA e checkpoint. O bot
   nunca vê e-mail nem senha, e trocar de conta é trocar um arquivo.

2. **Sessão morre, e isso precisa ser barulhento.** Um scraper logado que perde a
   sessão não devolve erro: devolve a página de login, que tem zero posts. Sem
   detecção explícita o sintoma seria "o grupo parou de receber vaga" e ninguém
   saberia por quê. Por isso a página de login levanta `AuthError` na hora.

3. **Volume baixo de propósito.** Uma leitura aqui não é como uma requisição de
   API: é um navegador subindo, uma página pesada carregando e um scroll. O
   intervalo padrão é de 30 minutos e há teto de posts por grupo. Ler mais rápido
   não faz aparecer mais trabalho — o que faz é o grupo publicar mais.

O que este módulo **não** faz, por decisão: não entra em grupo, não curte, não
comenta, não responde e não manda mensagem. Ele lê o que a conta já pode ler,
como um membro rolando o feed. Entrar nos grupos é passo manual, feito uma vez
por quem é dono da conta — automatizar isso é o caminho mais curto para o
bloqueio, e não economiza nada que valha o risco.
"""

from __future__ import annotations

import json
import logging
import random
import re
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sources import AuthError, BaseSource, Job, SourceError

log = logging.getLogger("adv-jobs-bot.facebook")


# ---------------------------------------------------------------------------
# Lista de grupos
# ---------------------------------------------------------------------------

@dataclass
class Grupo:
    """Um grupo do Facebook a ser lido.

    O `uf` é o detalhe que faz este projeto funcionar. Post de grupo raramente
    diz a cidade — o grupo *é* a cidade ("Advogados de Campinas e região"). Sem
    essa dica o classificador teria que adivinhar a comarca a cada post, e
    adivinhar comarca é exatamente o erro que manda a Milena para uma audiência
    a 400 km de distância.
    """

    slug: str                 # id numérico ou nome curto na URL
    nome: str = ""            # rótulo legível, só para log e mensagem
    uf: str = ""              # UF presumida deste grupo, quando o post não disser

    @property
    def url(self) -> str:
        # `sorting_setting=CHRONOLOGICAL` é obrigatório: o padrão do Facebook é
        # "mais relevantes", que mistura post de três semanas atrás no topo e faz
        # o bot reprocessar antiguidade a cada ciclo sem ver o que é novo.
        return f"https://www.facebook.com/groups/{self.slug}?sorting_setting=CHRONOLOGICAL"

    @property
    def rotulo(self) -> str:
        return self.nome or self.slug


_URL_GRUPO_RE = re.compile(r"facebook\.com/groups/([^/?#\s]+)", re.I)


def carregar_grupos(caminho: Path) -> list[Grupo]:
    """Lê `facebook_groups.txt`.

    Formato por linha, com `|` separando os campos opcionais:

        https://www.facebook.com/groups/123456 | Advogados SP | SP
        advogadoscorrespondentes | Correspondentes Brasil |
        123456789

    `#` comenta a linha inteira. Linha em branco é ignorada.
    """
    if not caminho.exists():
        log.error("Lista de grupos não encontrada em %s — a fonte Facebook não "
                  "tem o que ler", caminho)
        return []

    grupos: list[Grupo] = []
    vistos: set[str] = set()
    for numero, linha in enumerate(caminho.read_text(encoding="utf-8").splitlines(), 1):
        linha = linha.strip()
        if not linha or linha.startswith("#"):
            continue

        partes = [p.strip() for p in linha.split("|")]
        bruto = partes[0]
        nome = partes[1] if len(partes) > 1 else ""
        uf = (partes[2] if len(partes) > 2 else "").upper()[:2]

        achado = _URL_GRUPO_RE.search(bruto)
        slug = achado.group(1) if achado else bruto.strip("/ ")
        if not slug:
            log.warning("Linha %d de %s não tem grupo reconhecível: %r",
                        numero, caminho.name, linha)
            continue
        if slug in vistos:
            continue
        vistos.add(slug)
        grupos.append(Grupo(slug=slug, nome=nome, uf=uf))

    log.info("Grupos do Facebook carregados: %d (%s)", len(grupos),
             ", ".join(g.rotulo for g in grupos) or "nenhum")
    return grupos


# ---------------------------------------------------------------------------
# Limpeza do texto do post
# ---------------------------------------------------------------------------

# O `innerText` de um post traz muito além do post: cabeçalho, tradução,
# contadores de reação, barra de ações e o começo dos comentários. Tudo que vem
# a partir de uma destas linhas é descarte.
_FIM_DO_POST = re.compile(
    r"^(?:todas as rea[cç][oõ]es|curtir|gostei|like|comentar|coment[aá]rios?|"
    r"compartilhar|share|ver tradu[cç][aã]o|ver mais coment[aá]rios|"
    r"\d+\s*coment[aá]rios?|\d+\s*compartilhamentos?)\b",
    re.I,
)

# Linhas de enfeite do cabeçalho, que aparecem entre o autor e o texto.
_RUIDO_CABECALHO = re.compile(
    r"^(?:·+|\.{3}|membro do grupo|autor[a]?|administrador[a]?|moderador[a]?|"
    r"seguir|participar|entrar no grupo|compartilhado com o grupo|"
    r"principais contribuintes?|novo membro|editado|patrocinado|sugerido para voc[eê])$",
    re.I,
)

# "2 h", "35 min", "Ontem às 14:32", "12 de agosto às 09:12", "3 d"
_TEMPO_RE = re.compile(
    r"^(?:agora(?:\s+mesmo)?|h[aá]\s+)?\s*"
    r"(?:(\d+)\s*(min(?:uto)?s?|h(?:ora)?s?|d(?:ia)?s?|sem(?:ana)?s?)"
    r"|ontem|hoje|\d{1,2}\s+de\s+\w+)"
    r"(?:\s*(?:[aà]s|,)\s*\d{1,2}[:h]\d{2})?\.?$",
    re.I,
)


def _sem_acento(texto: str) -> str:
    return (unicodedata.normalize("NFKD", texto or "")
            .encode("ascii", "ignore").decode("ascii"))


def limpar_texto(bruto: str, autor: str = "") -> str:
    """Reduz o `innerText` do card ao texto que a pessoa realmente escreveu.

    Erra para o lado de **manter**: cortar demais faria o classificador julgar um
    post pela metade, e um post pela metade é pior que um post com sobra — a
    sobra o modelo ignora, a falta ele não tem como inventar.
    """
    linhas = [ln.strip() for ln in (bruto or "").replace("\r", "").split("\n")]

    # 1) Corta do rodapé em diante (reações, ações, comentários).
    corpo: list[str] = []
    for ln in linhas:
        if _FIM_DO_POST.match(_sem_acento(ln)):
            break
        corpo.append(ln)

    # 2) Tira o cabeçalho: nome do autor, horário e enfeites. Só no começo — as
    #    mesmas palavras no meio do texto são conteúdo legítimo.
    autor_norm = _sem_acento(autor).lower().strip()
    while corpo:
        primeira = corpo[0]
        if not primeira:
            corpo.pop(0)
            continue
        norm = _sem_acento(primeira).lower().strip()
        # Linha que não sobrou NADA depois de tirar os acentos era só
        # pontuação ou emoji — o "···" do menu do post, uma seta, um separador.
        # (`_sem_acento` descarta tudo que não é ASCII, então `···` vira "".)
        if not norm:
            corpo.pop(0)
            continue
        if (autor_norm and norm == autor_norm) or _RUIDO_CABECALHO.match(norm) \
                or _TEMPO_RE.match(norm):
            corpo.pop(0)
            continue
        break

    texto = "\n".join(corpo).strip()
    return re.sub(r"\n{3,}", "\n\n", texto)


def parse_tempo(rotulo: str, agora: datetime) -> str:
    """Converte o rótulo de tempo do Facebook em ISO 8601.

    A fila desempata por `published_at` como texto; um "2 h" cru ordenaria
    alfabeticamente e colocaria "9 min" depois de "10 h". Quando não dá para
    entender, devolve o instante da coleta — que é uma aproximação honesta, já
    que o bot lê em ordem cronológica e só vê post novo.
    """
    rot = _sem_acento(rotulo or "").lower().strip()
    achado = _TEMPO_RE.match(rot)
    if achado and achado.group(1):
        n = int(achado.group(1))
        unidade = achado.group(2)[0]  # m, h, d, s
        delta = {
            "m": timedelta(minutes=n),
            "h": timedelta(hours=n),
            "d": timedelta(days=n),
            "s": timedelta(weeks=n),
        }.get(unidade, timedelta())
        return (agora - delta).isoformat(timespec="seconds")
    if rot.startswith("ontem"):
        return (agora - timedelta(days=1)).isoformat(timespec="seconds")
    return agora.isoformat(timespec="seconds")


def titulo_do_post(texto: str, limite: int = 90) -> str:
    """Primeira frase útil do post, para servir de título.

    Post de grupo não tem título — tem um bloco de texto. O título existe aqui
    para o log, para a deduplicação e para a linha em negrito da mensagem.
    """
    limpo = re.sub(r"\s+", " ", (texto or "").strip())
    if not limpo:
        return "(post sem texto)"
    # Corta na primeira quebra forte de frase, se ela vier cedo.
    corte = re.split(r"(?<=[.!?])\s+|\n", limpo, maxsplit=1)[0]
    if len(corte) > limite:
        corte = corte[:limite]
        espaco = corte.rfind(" ")
        if espaco > 40:
            corte = corte[:espaco]
        corte += "…"
    return corte


# ---------------------------------------------------------------------------
# Extração no navegador
# ---------------------------------------------------------------------------

# Roda dentro da página. Devolve uma lista crua de posts; toda a limpeza fica no
# Python, que é onde dá para testar sem navegador.
#
# Por que `div[role="article"]`: as classes do Facebook são geradas e mudam sem
# aviso, mas os papéis ARIA são o que faz o site funcionar com leitor de tela —
# eles são estáveis porque quebrá-los quebraria a acessibilidade do produto.
_EXTRAIR_JS = r"""
() => {
  const saida = [];
  const artigos = document.querySelectorAll('div[role="article"]');

  for (const art of artigos) {
    // Comentário também é role=article, aninhado dentro do post. Só o de fora
    // é o post; os de dentro são conversa alheia.
    if (art.parentElement && art.parentElement.closest('div[role="article"]')) continue;

    let pid = '', url = '', tempo = '';
    for (const a of art.querySelectorAll('a[href]')) {
      const h = a.getAttribute('href') || '';
      let m = h.match(/\/groups\/([^/?#]+)\/(?:posts|permalink)\/(\d+)/);
      if (m) {
        pid = m[2];
        url = 'https://www.facebook.com/groups/' + m[1] + '/posts/' + m[2] + '/';
        tempo = (a.innerText || '').trim();
        break;
      }
      m = h.match(/[?&]story_fbid=(\d+)/);
      if (m) {
        pid = m[1];
        url = h.startsWith('http') ? h : 'https://www.facebook.com' + h;
        tempo = (a.innerText || '').trim();
        break;
      }
    }
    if (!pid) continue;

    let autor = '';
    const cab = art.querySelector('h2 a, h3 a, h4 a, h2 span, h3 span, h4 span');
    if (cab) autor = (cab.innerText || '').trim().split('\n')[0];

    // Perfil do autor: em post de pedido de serviço é por onde se responde.
    let perfil = '';
    const pa = art.querySelector('h2 a[href], h3 a[href], h4 a[href]');
    if (pa) {
      const h = pa.getAttribute('href') || '';
      perfil = h.startsWith('http') ? h : 'https://www.facebook.com' + h;
      perfil = perfil.split('?')[0];
    }

    saida.push({
      pid, url, tempo, autor, perfil,
      texto: (art.innerText || '').trim(),
    });
  }
  return saida;
}
"""

# Sinais de que a sessão morreu. Cobre português e inglês porque a página de
# login vem no idioma do IP, não no da conta.
_SINAIS_LOGIN = (
    "login", "checkpoint", "/recover", "confirmemail",
)

# O que o Facebook escreve quando o post não existe mais — apagado pelo autor,
# removido pelo moderador, ou grupo fechado. Comparado SEM acento, porque o
# texto é normalizado antes.
#
# A lista é curta e literal de propósito: é ela que autoriza apagar uma mensagem
# do grupo do cliente. Uma expressão frouxa aqui (um "indisponivel" solto, por
# exemplo) casaria com aviso de vídeo indisponível dentro de um post que está
# perfeitamente no ar.
_POST_SUMIU = re.compile(
    r"este conteudo nao esta disponivel no momento|"
    r"esse conteudo nao esta disponivel no momento|"
    r"this content isn'?t available (?:right now|at the moment)|"
    r"conteudo nao encontrado|"
    r"esta pagina nao esta disponivel|"
    r"this page isn'?t available|"
    r"o link que voce seguiu pode estar quebrado|"
    r"the link you followed may be broken",
    re.I,
)


class FacebookSource(BaseSource):
    """Lê os grupos configurados com um navegador logado."""

    name = "facebook"
    label = "Facebook"
    # Não existe "remoto" aqui: o trabalho jurídico é presencial numa comarca ou
    # remoto por natureza (parecer, contrato). Quem filtra lugar é a regra de UF.
    prefilter_remote = False
    # 30 min. Subir navegador não é barato, e grupo de advogado não publica de
    # dez em dez minutos.
    default_interval = 1800

    def __init__(self, *, groups_file: Path, state_file: Path,
                 interval_seconds: int | None = None,
                 max_posts_por_grupo: int = 25,
                 scrolls: int = 4,
                 headless: bool = True) -> None:
        super().__init__(interval_seconds)
        self.groups_file = groups_file
        self.state_file = state_file
        self.max_posts_por_grupo = max_posts_por_grupo
        self.scrolls = scrolls
        self.headless = headless
        self._grupos_mtime: float | None = None
        self._grupos: list[Grupo] = []

    # -- configuração -------------------------------------------------------

    def grupos(self) -> list[Grupo]:
        """Lista de grupos, relida quando o arquivo muda (sem restart)."""
        try:
            mtime = self.groups_file.stat().st_mtime
        except OSError:
            mtime = None
        if mtime != self._grupos_mtime or not self._grupos:
            self._grupos = carregar_grupos(self.groups_file)
            self._grupos_mtime = mtime
        return self._grupos

    # -- coleta -------------------------------------------------------------

    def fetch(self) -> list[Job]:
        grupos = self.grupos()
        if not grupos:
            raise SourceError(
                f"Nenhum grupo configurado em {self.groups_file}. "
                "Preencha o arquivo com as URLs dos grupos."
            )
        if not self.state_file.exists():
            raise AuthError(
                f"Sessão do Facebook não encontrada em {self.state_file}. "
                "Rode `python bot/tools/facebook_login.py` para criar."
            )

        try:
            from playwright.sync_api import sync_playwright  # noqa: PLC0415
        except ImportError as exc:
            raise SourceError(
                "playwright não instalado — `pip install playwright` e "
                "`playwright install chromium`"
            ) from exc

        jobs: list[Job] = []
        with sync_playwright() as pw:
            navegador = pw.chromium.launch(
                headless=self.headless,
                args=[
                    # Sem isto o `navigator.webdriver` entrega o navegador de cara.
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                ],
            )
            contexto = navegador.new_context(
                storage_state=str(self.state_file),
                locale="pt-BR",
                timezone_id="America/Sao_Paulo",
                viewport={"width": 1366, "height": 900},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
                ),
            )
            contexto.add_init_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
            )
            pagina = contexto.new_page()

            try:
                for i, grupo in enumerate(grupos):
                    if i:
                        # Intervalo irregular entre grupos. Ritmo de metrônomo é
                        # o padrão mais fácil de detectar que existe.
                        time.sleep(random.uniform(3.0, 7.0))
                    try:
                        jobs.extend(self._ler_grupo(pagina, grupo))
                    except AuthError:
                        raise
                    except Exception as exc:  # noqa: BLE001
                        log.warning("Grupo %s falhou: %s — seguindo para o próximo",
                                    grupo.rotulo, exc)
            finally:
                # A sessão se renova a cada visita; salvar de volta estica muito a
                # vida do login. Sem isto o cookie envelhece e morre antes da hora.
                try:
                    contexto.storage_state(path=str(self.state_file))
                except Exception as exc:  # noqa: BLE001
                    log.debug("Não consegui regravar a sessão: %s", exc)
                contexto.close()
                navegador.close()

        return jobs

    def _ler_grupo(self, pagina: Any, grupo: Grupo) -> list[Job]:
        log.info("Facebook: lendo grupo %s", grupo.rotulo)
        pagina.goto(grupo.url, wait_until="domcontentloaded", timeout=60_000)

        url_atual = (pagina.url or "").lower()
        if any(s in url_atual for s in _SINAIS_LOGIN):
            raise AuthError(
                f"Facebook redirecionou para {pagina.url} — a sessão expirou ou "
                "caiu em checkpoint. Rode `python bot/tools/facebook_login.py` "
                "de novo."
            )

        # O feed monta em etapas; esperar o primeiro artigo é mais confiável que
        # esperar `networkidle`, que num feed infinito nunca acontece.
        try:
            pagina.wait_for_selector('div[role="article"]', timeout=30_000)
        except Exception as exc:  # noqa: BLE001
            log.warning("Grupo %s não renderizou nenhum post: %s", grupo.rotulo, exc)
            return []

        for _ in range(self.scrolls):
            pagina.mouse.wheel(0, 2200)
            pagina.wait_for_timeout(random.randint(900, 1800))

        self._expandir(pagina)

        brutos = pagina.evaluate(_EXTRAIR_JS) or []
        log.info("Facebook: %s devolveu %d card(s)", grupo.rotulo, len(brutos))

        agora = datetime.now().astimezone()
        jobs: list[Job] = []
        vistos: set[str] = set()
        for bruto in brutos:
            if len(jobs) >= self.max_posts_por_grupo:
                break
            pid = str(bruto.get("pid") or "")
            if not pid or pid in vistos:
                continue
            vistos.add(pid)

            autor = (bruto.get("autor") or "").strip()
            texto = limpar_texto(bruto.get("texto") or "", autor)
            # Post curto demais não é pedido de trabalho — é "up", "interesse",
            # "chamei no pv". Gastar chamada de IA nisso é queimar cota à toa.
            if len(texto) < 40:
                continue

            jobs.append(Job(
                source=self.name,
                source_id=pid,
                title=titulo_do_post(texto),
                url=bruto.get("url") or grupo.url,
                description=texto,
                company=autor,
                # O local que o bot conhece de graça é o do grupo. O
                # classificador ainda pode achar comarca no texto e sobrepor.
                location=grupo.uf,
                published_at=parse_tempo(bruto.get("tempo") or "", agora),
                job_type="Post de grupo",
                category=grupo.rotulo,
            ))

        log.info("Facebook: %s rendeu %d post(s) aproveitável(is)",
                 grupo.rotulo, len(jobs))
        return jobs

    # -- o post ainda existe? -----------------------------------------------

    def verificar_posts(self, urls: dict[str, str]) -> dict[str, str]:
        """Recebe {uid: url} e devolve {uid: "aberta"|"fechada"|"desconhecida"}.

        **Regra de ouro, igual à do resto do projeto: na dúvida, está aberta.**
        Só a frase explícita de conteúdo indisponível conta como fechada. Erro
        de rede, timeout, sessão morta, layout novo — tudo devolve
        "desconhecida", porque a diferença entre "o autor apagou o post" e "o
        Facebook não carregou agora" é a diferença entre apagar a mensagem certa
        e apagar uma demanda boa do grupo do cliente.

        Some a isso a trava de `publicadas.py`: são precisas DUAS confirmações
        em checagens distintas. Um soluço não derruba nada.

        Custo: uma página por post. É caro comparado a uma API — por isso o
        `RECHECK_POR_CICLO` é baixo e cada post é revisto no máximo uma vez por
        `RECHECK_HORAS`.
        """
        if not urls:
            return {}
        if not self.state_file.exists():
            log.warning("Sem sessão do Facebook — revisão de posts adiada")
            return {uid: "desconhecida" for uid in urls}

        try:
            from playwright.sync_api import sync_playwright  # noqa: PLC0415
        except ImportError:
            return {uid: "desconhecida" for uid in urls}

        resultado: dict[str, str] = {}
        with sync_playwright() as pw:
            navegador = pw.chromium.launch(
                headless=self.headless,
                args=["--disable-blink-features=AutomationControlled",
                      "--no-sandbox", "--disable-dev-shm-usage"],
            )
            contexto = navegador.new_context(
                storage_state=str(self.state_file),
                locale="pt-BR",
                timezone_id="America/Sao_Paulo",
                viewport={"width": 1366, "height": 900},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
                ),
            )
            contexto.add_init_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
            )
            pagina = contexto.new_page()

            for i, (uid, url) in enumerate(urls.items()):
                if i:
                    time.sleep(random.uniform(1.5, 3.5))
                resultado[uid] = self._estado_do_post(pagina, url)

            try:
                contexto.storage_state(path=str(self.state_file))
            except Exception:  # noqa: BLE001
                pass
            contexto.close()
            navegador.close()

        fechadas = sum(1 for e in resultado.values() if e == "fechada")
        log.info("Revisão de posts: %d checado(s), %d sem conteúdo, %d inconclusivo(s)",
                 len(resultado), fechadas,
                 sum(1 for e in resultado.values() if e == "desconhecida"))
        return resultado

    def _estado_do_post(self, pagina: Any, url: str) -> str:
        if not url:
            return "desconhecida"
        try:
            pagina.goto(url, wait_until="domcontentloaded", timeout=45_000)
            pagina.wait_for_timeout(1500)
        except Exception as exc:  # noqa: BLE001
            log.debug("Falha abrindo %s: %s", url, exc)
            return "desconhecida"

        # Sessão morta devolve a página de login, que obviamente não tem o post.
        # Tratar isso como "post apagado" apagaria o grupo inteiro em um dia.
        if any(s in (pagina.url or "").lower() for s in _SINAIS_LOGIN):
            log.warning("Revisão interrompida: a sessão do Facebook expirou")
            return "desconhecida"

        try:
            texto = (pagina.inner_text("body") or "")[:4000]
        except Exception:  # noqa: BLE001
            return "desconhecida"

        if _POST_SUMIU.search(_sem_acento(texto)):
            return "fechada"

        # O post continua lá se o card ainda renderiza. Se nem card nem frase de
        # erro apareceram, é caso indeterminado — e indeterminado é "aberta".
        try:
            if pagina.locator('div[role="article"]').count() > 0:
                return "aberta"
        except Exception:  # noqa: BLE001
            pass
        return "desconhecida"

    @staticmethod
    def _expandir(pagina: Any) -> None:
        """Clica nos "Ver mais" para o post vir inteiro.

        Sem isto o Facebook entrega os primeiros ~250 caracteres, e é justamente
        no fim do post que costumam estar a comarca, o valor e o contato — os
        três dados que decidem se o trabalho serve.
        """
        try:
            botoes = pagina.get_by_role(
                "button", name=re.compile(r"^(ver mais|see more)$", re.I)
            )
            total = min(botoes.count(), 30)
        except Exception:  # noqa: BLE001
            return
        for i in range(total):
            try:
                botoes.nth(i).click(timeout=1500)
                pagina.wait_for_timeout(120)
            except Exception:  # noqa: BLE001
                continue
