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
    r"comente como|write a comment|"
    r"\d+\s*coment[aá]rios?|\d+\s*compartilhamentos?)\b",
    re.I,
)

# O `innerText` de um card com imagem vem salpicado de linhas "Facebook" — é o
# nome acessível que o próprio site dá aos links de mídia. Num post só de foto
# isso sozinho passava dos 40 caracteres mínimos e fazia um post sem texto nenhum
# chegar ao classificador parecendo ter conteúdo.
_LINHA_VAZIA_DE_SENTIDO = re.compile(r"^(?:facebook|\W*)$", re.I)

# Sobra do botão de expandir. Depois que o bot clica em "Ver mais", o botão vira
# "Ver menos" — e o Facebook o mantém DENTRO do bloco de texto do post, então ele
# entra no `story_message` como se fosse a última palavra de quem escreveu.
_BOTAO_EXPANDIR = re.compile(r"\s*(?:\.{3}|…)?\s*(?:ver menos|ver mais|see less|see more)\s*$",
                             re.I)

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

    # 1) Corta do rodapé em diante (reações, ações, comentários) e joga fora as
    #    linhas que não são texto de ninguém.
    corpo: list[str] = []
    for ln in linhas:
        if _FIM_DO_POST.match(_sem_acento(ln)):
            break
        if _LINHA_VAZIA_DE_SENTIDO.match(ln):
            continue
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
    texto = re.sub(r"\n{3,}", "\n\n", texto)
    return limpar_sobra_de_botao(texto)


def limpar_sobra_de_botao(texto: str) -> str:
    """Tira o "Ver menos"/"Ver mais" grudado no fim do texto do post.

    Vale tanto para o texto limpo à mão quanto para o `story_message`, que vem
    pronto do Facebook e mesmo assim carrega essa sobra. Sem isto, todo post
    longo termina com uma palavra que a pessoa não escreveu — e o classificador
    lê "Ver menos" como se fosse parte do pedido.
    """
    limpo = (texto or "").strip()
    # Duas passagens: "… Ver mais" seguido de "Ver menos" acontece quando o
    # clique expandiu o post no meio da coleta.
    for _ in range(2):
        novo = _BOTAO_EXPANDIR.sub("", limpo).strip()
        if novo == limpo:
            break
        limpo = novo
    return limpo


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
# COMO O FEED DE GRUPO É HOJE (medido em 16/08/2026, contra o facebook.com)
# -------------------------------------------------------------------------
# A primeira versão deste arquivo procurava `div[role="article"]`, apostando que
# papel ARIA é estável porque quebrá-lo quebraria o leitor de tela. A aposta
# estava errada para esta tela: na página de grupo existem hoje **dois**
# `role="article"` na página inteira, ambos vazios, e nenhum é post. O post é
# filho direto de `div[role="feed"]`.
#
# O que substituiu o papel ARIA como âncora estável foi o `data-ad-rendering-role`,
# que o Facebook usa para marcar as partes do post no próprio produto de anúncio:
#
#     story_message  → o texto que a pessoa escreveu, sozinho
#     profile_name   → o autor
#
# Isso é melhor do que existia antes: `story_message` já vem sem cabeçalho, sem
# rodapé de reações e sem comentário de terceiro. A limpeza em `limpar_texto`
# continua no código como reserva — para o layout antigo e para o card em que
# esse marcador não aparecer —, mas deixou de ser o caminho principal.
#
# O id do post aparece em quatro formatos, e é preciso aceitar os quatro: post
# comum traz `/posts/<id>`, post antigo traz `story_fbid`, post com foto esconde
# o id em `set=gm.<id>` no link da imagem, e o link do horário virou
# `/stories/<feed>/<base64>/`, com o id dentro do base64 (`S:_ISC:<id>`).
_EXTRAIR_JS = r"""
() => {
  // O slug da URL atual monta o permalink canônico. Vale mais que o href do
  // card: o link do Facebook vem carregado de `__cft__`, um token de rastreio
  // que muda a cada carregamento e deixaria o mesmo post com URL diferente a
  // cada ciclo.
  const slug = (location.pathname.match(/\/groups\/([^/?#]+)/) || [])[1] || '';

  const canonica = (id) => slug
    ? 'https://www.facebook.com/groups/' + slug + '/posts/' + id + '/'
    : 'https://www.facebook.com/' + id;

  const idDoHref = (h) => {
    if (!h) return null;
    let m = h.match(/\/groups\/[^/?#]+\/(?:posts|permalink)\/(\d+)/);
    if (m) return {id: m[1], url: canonica(m[1])};
    m = h.match(/[?&]story_fbid=(\d+)/);
    if (m) return {id: m[1], url: canonica(m[1])};
    // Post com foto: o link da imagem carrega o id do post do grupo em `gm.`.
    m = h.match(/[?&]set=gm\.(\d+)/);
    if (m) return {id: m[1], url: canonica(m[1])};
    // Link do horário no layout novo. O base64 guarda "S:_ISC:<id do post>".
    m = h.match(/\/stories\/\d+\/([A-Za-z0-9+/=_-]+)/);
    if (m) {
      try {
        const dec = atob(m[1].replace(/-/g, '+').replace(/_/g, '/'));
        const n = dec.match(/(\d{6,})\s*$/);
        if (n) return {id: n[1], url: canonica(n[1])};
      } catch (e) { /* base64 de outra coisa */ }
    }
    // `pfbid...` é o id opaco que o Facebook passou a usar em parte dos posts.
    // Não é número e não monta URL canônica: aqui o permalink.php é o link, e o
    // id de origem é o próprio pfbid — estável entre ciclos, que é o que a
    // deduplicação precisa.
    m = h.match(/[?&]story_fbid=(pfbid[A-Za-z0-9]+)/);
    if (m) {
      const dono = (h.match(/[?&]id=(\d+)/) || [])[1] || '';
      return {
        id: m[1],
        url: 'https://www.facebook.com/permalink.php?story_fbid=' + m[1] +
             (dono ? '&id=' + dono : ''),
      };
    }
    return null;
  };

  const ehTempo = (t) => /^(?:agora|h[áa]\s|\d+\s*(?:min|m|h|d|sem)\b|ontem|hoje|\d{1,2}\s+de\s+\w+)/i
      .test((t || '').trim());

  const cards = [];
  const feed = document.querySelector('div[role="feed"]');
  if (feed) for (const c of feed.children) cards.push(c);
  // Layout antigo, e outras telas que ainda usam article (a página de um post
  // isolado, por exemplo). Comentário também é role=article, aninhado dentro do
  // post: só o de fora é o post.
  for (const art of document.querySelectorAll('div[role="article"]')) {
    if (art.parentElement && art.parentElement.closest('div[role="article"]')) continue;
    if (cards.some(c => c === art || c.contains(art))) continue;
    cards.push(art);
  }

  const saida = [];
  for (const card of cards) {
    let pid = '', url = '', tempo = '';
    for (const a of card.querySelectorAll('a[href]')) {
      const t = (a.innerText || '').trim();
      if (!tempo && ehTempo(t)) tempo = t.split('\n')[0];
      if (!pid) {
        const achado = idDoHref(a.getAttribute('href'));
        if (achado) { pid = achado.id; url = achado.url; }
      }
    }
    if (!pid) continue;

    const msgEl = card.querySelector('[data-ad-rendering-role="story_message"]');
    const nomeEl = card.querySelector('[data-ad-rendering-role="profile_name"]');

    // O nome vem no link; o resto da linha costuma ser enfeite do Facebook
    // ("está em Kohat", "compartilhou uma lembrança").
    let autor = '';
    const nomeLink = nomeEl ? nomeEl.querySelector('a') : null;
    if (nomeLink) autor = (nomeLink.innerText || '').trim().split('\n')[0];
    if (!autor && nomeEl) autor = (nomeEl.innerText || '').trim().split('\n')[0];
    if (!autor) {
      const cab = card.querySelector('h2 a, h3 a, h4 a, h2 span, h3 span, h4 span');
      if (cab) autor = (cab.innerText || '').trim().split('\n')[0];
    }

    // Perfil do autor: em post de pedido de serviço é por onde se responde.
    let perfil = '';
    const pa = (nomeEl && nomeEl.querySelector('a[href]'))
            || card.querySelector('h2 a[href], h3 a[href], h4 a[href]');
    if (pa) {
      const h = pa.getAttribute('href') || '';
      perfil = (h.startsWith('http') ? h : 'https://www.facebook.com' + h).split('?')[0];
    }

    saida.push({
      pid, url, tempo, autor, perfil,
      // `mensagem` é o texto do post já isolado pelo próprio Facebook — quando
      // existe, não há o que limpar. `texto` é o card inteiro, reserva para o
      // card em que o marcador não aparecer.
      mensagem: msgEl ? (msgEl.innerText || '').trim() : '',
      texto: (card.innerText || '').trim(),
    });
  }
  return saida;
}
"""

# Marca os links do cabeçalho do post cujo href ainda não é permalink. O
# Facebook entrega esse `<a>` com href de mentira — vazio, `#`, ou só o token de
# rastreio `?__cft__=...` — e só troca pelo link de verdade quando o ponteiro
# passa por cima. Sem passar o mouse, o card fica sem id e o bot o descarta
# inteiro, que foi metade do "zero posts" da primeira medição.
#
# A primeira tentativa mirava o link pelo TEXTO do horário, e não achava nada: o
# Facebook escreve o horário embaralhado, com caractere invisível entre as
# letras (`o͏d͏n͏p͏o͏t͏e͏r͏s͏S͏`), e remonta na ordem certa por CSS. Não há texto de
# horário para casar. O href cru, esse sim, se reconhece.
_MARCAR_LINKS_CRUS_JS = r"""
() => {
  const feed = document.querySelector('div[role="feed"]');
  if (!feed) return 0;
  const cru = (h) => h === null || h === '' || h === '#' || h.startsWith('?');
  let n = 0;
  for (const card of feed.children) {
    // Card sem mensagem é placeholder de post já descarregado da tela.
    if (!card.querySelector('[data-ad-rendering-role="story_message"]')) continue;
    let noCard = 0;
    for (const a of card.querySelectorAll('a[role="link"]')) {
      if (!cru(a.getAttribute('href'))) continue;
      a.setAttribute('data-fbhover', String(n++));
      // Quatro por card chega: o permalink está no cabeçalho, e o resto são
      // "Ver tradução" e afins. Sem esse teto, um card com galeria de fotos
      // sozinho consumiria a passagem inteira.
      if (++noCard >= 4) break;
    }
  }
  return n;
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
                 headless: bool = True,
                 proxy: str = "") -> None:
        super().__init__(interval_seconds)
        # Saida de rede do navegador. Existe por um motivo especifico: o
        # Facebook trata login vindo de datacenter como suspeito e responde com
        # CAPTCHA da Arkose, que script nenhum resolve. Apontando para um proxy
        # residencial (ou para um tunel que sai pelo IP certo), o trafego deixa
        # de vir de onde ele desconfia.
        self.proxy = proxy.strip()
        self.groups_file = groups_file
        self.state_file = state_file
        self.max_posts_por_grupo = max_posts_por_grupo
        self.scrolls = scrolls
        self.headless = headless
        self._grupos_mtime: float | None = None
        self._grupos: list[Grupo] = []
        # Último lote cru do navegador, do jeito que saiu do `_EXTRAIR_JS`. Só a
        # sonda usa: é o que permite olhar o antes/depois da limpeza sem abrir um
        # segundo caminho de código, que mediria a sonda em vez do bot.
        self.ultimos_brutos: list[dict] = []
        # O que a ultima renovacao de sessao viu, fase a fase, e a foto da tela
        # onde ela parou. Sem isto, "nao consegui refazer o login" e uma frase
        # sem informacao nenhuma: o log do container nao sai da maquina, e a
        # pagina que travou e exatamente o dado que resolve.
        self.ultimo_diagnostico: dict[str, Any] = {}

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

    # -- renovacao da sessao ------------------------------------------------

    def renovar_sessao(self, email: str, senha: str,
                       obter_codigo: Any = None,
                       ao_precisar_de_codigo: Any = None) -> bool:
        """Faz login de novo, aqui mesmo, e regrava o arquivo de sessao.

        Roda DENTRO do servidor de proposito. Uma sessao criada noutra maquina
        chega ao Facebook como aparelho novo em lugar novo e cai no checkpoint —
        e o pior e que ela continua valida na maquina de origem, entao parece
        que esta tudo certo. Login feito daqui nao tem esse descompasso: o IP
        que entrou e o IP que vai navegar depois.

        `obter_codigo()` devolve o codigo de dois fatores (do segredo TOTP ou de
        quem responder no Telegram) e pode demorar minutos.
        `ao_precisar_de_codigo` e chamado ANTES da espera, para avisar quem
        precisa manda-lo.

        Devolve True so quando a sessao nova foi gravada e validada.
        """
        try:
            from playwright.sync_api import sync_playwright  # noqa: PLC0415
        except ImportError:
            log.error("playwright nao instalado — sem como renovar a sessao")
            return False

        log.info("Tentando renovar a sessao do Facebook a partir do servidor...")
        with sync_playwright() as pw:
            navegador = pw.chromium.launch(
                headless=self.headless,
                proxy={"server": self.proxy} if self.proxy else None,
                args=["--disable-blink-features=AutomationControlled",
                      "--no-sandbox", "--disable-dev-shm-usage"],
            )
            # Sessao nova, sem carregar a antiga: a antiga e justamente a que o
            # Facebook recusou, e reaproveita-la traria o checkpoint junto.
            contexto = navegador.new_context(
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
                return self._fluxo_de_login(pagina, contexto, email, senha,
                                            obter_codigo, ao_precisar_de_codigo)
            except Exception as exc:  # noqa: BLE001
                log.error("Renovacao de sessao falhou: %s", exc)
                self.ultimo_diagnostico["erro"] = str(exc)[:300]
                self._fotografar(pagina)
                return False
            finally:
                try:
                    contexto.close()
                    navegador.close()
                except Exception:  # noqa: BLE001
                    pass

    def _anotar_fase(self, pagina: Any, nome: str) -> None:
        try:
            titulo = pagina.title()
        except Exception:  # noqa: BLE001
            titulo = "?"
        fase = {"fase": nome, "url": pagina.url, "titulo": titulo}
        self.ultimo_diagnostico.setdefault("fases", []).append(fase)
        log.info("[login:%s] %s | %s", nome, pagina.url, titulo)

    def _fotografar(self, pagina: Any) -> None:
        """Guarda a tela onde o login parou. E o unico jeito de eu ver daqui."""
        try:
            self.ultimo_diagnostico["png"] = pagina.screenshot(full_page=False)
        except Exception as exc:  # noqa: BLE001
            log.warning("Nao consegui fotografar a tela: %s", exc)
        try:
            texto = pagina.inner_text("body")[:600]
            self.ultimo_diagnostico["texto"] = " ".join(texto.split())
        except Exception:  # noqa: BLE001
            pass

    def _fluxo_de_login(self, pagina: Any, contexto: Any, email: str, senha: str,
                        obter_codigo: Any, ao_precisar_de_codigo: Any) -> bool:
        self.ultimo_diagnostico = {"fases": []}
        pagina.goto("https://www.facebook.com/login.php",
                    wait_until="domcontentloaded", timeout=60_000)
        self._aceitar_cookies(pagina)
        self._anotar_fase(pagina, "tela-de-login")

        # Existem DUAS telas de senha, e confundi-las custa a renovacao inteira.
        #
        # A comum tem e-mail e senha. A outra e a "Insira sua senha para
        # continuar", que o Facebook mostra quando ja sabe QUEM e voce (a conta
        # esta logada) mas quer a senha de novo — tipico depois de um acesso de
        # lugar novo. Essa NAO tem campo de e-mail: tentar preencher e-mail nela
        # estoura o tempo de espera e derruba a renovacao com um erro que nao
        # diz nada sobre a causa.
        tem_email = False
        try:
            tem_email = pagina.locator('input[name="email"]').count() > 0
        except Exception:  # noqa: BLE001
            tem_email = False

        if tem_email:
            pagina.fill('input[name="email"]', email, timeout=30_000)
            log.info("Tela de login completa (e-mail + senha).")
        else:
            log.info("Tela de reconfirmacao de senha (a conta ja e conhecida).")

        pagina.fill('input[name="pass"], input[type="password"]', senha, timeout=30_000)
        if not self._submeter(pagina):
            self._fotografar(pagina)
            return False
        pagina.wait_for_timeout(8000)
        self._anotar_fase(pagina, "depois-do-login")

        # A reconfirmacao pode vir DEPOIS do login, na pagina seguinte. Mesma
        # tela, mesmo tratamento.
        if self._pede_so_a_senha(pagina):
            log.info("Reconfirmacao de senha apos o login — respondendo.")
            try:
                pagina.fill('input[name="pass"], input[type="password"]', senha,
                            timeout=20_000)
                self._submeter(pagina)
                pagina.wait_for_timeout(6000)
                log.info("Reconfirmacao enviada — pagina agora: %s", pagina.url)
            except Exception as exc:  # noqa: BLE001
                log.warning("Falha respondendo a reconfirmacao de senha: %s", exc)

        if self._pede_codigo(pagina):
            self._anotar_fase(pagina, "pediu-codigo")
            if obter_codigo is None:
                log.error("O Facebook pediu codigo de dois fatores e nao ha de "
                          "onde tirar um (sem segredo TOTP e sem quem responda).")
                self._fotografar(pagina)
                return False
            if ao_precisar_de_codigo is not None:
                try:
                    ao_precisar_de_codigo()
                except Exception as exc:  # noqa: BLE001
                    log.warning("Falha avisando que o codigo e necessario: %s", exc)
            codigo = obter_codigo()
            if not codigo:
                log.error("Nenhum codigo chegou a tempo — abandonando a renovacao.")
                self._fotografar(pagina)
                return False
            if not self._enviar_codigo(pagina, codigo):
                self._fotografar(pagina)
                return False
            self._anotar_fase(pagina, "depois-do-codigo")

        # Depois do codigo vem uma fila de telas de confirmacao — "Salvar
        # navegador?", "Foi voce?", "Continuar" — e elas nao vem sempre na mesma
        # ordem nem na mesma quantidade. Atravessar UMA so, como antes, deixava
        # o fluxo parado na segunda e a prova final concluia "ainda no
        # checkpoint" com a sessao praticamente pronta.
        for volta in range(4):
            url_atual = (pagina.url or "").lower()
            if "checkpoint" not in url_atual and "two_factor" not in url_atual:
                break
            self._anotar_fase(pagina, f"checkpoint-{volta + 1}")
            if not self._submeter(pagina, campo_para_enter="body"):
                break
            pagina.wait_for_timeout(6000)

        # A prova real: uma pagina logada que nao redireciona para o login.
        pagina.goto("https://www.facebook.com/groups/feed/",
                    wait_until="domcontentloaded", timeout=60_000)
        url = (pagina.url or "").lower()
        self._anotar_fase(pagina, "prova-final")
        cookies = {c["name"] for c in contexto.cookies()}
        if "c_user" not in cookies:
            log.error("Login terminou sem o cookie de sessao (c_user).")
            self._fotografar(pagina)
            return False

        if any(sinal in url for sinal in _SINAIS_LOGIN):
            log.error("Depois do login o Facebook ainda manda para %s — "
                      "checkpoint que exige gente.", pagina.url)
            self._fotografar(pagina)
            return False

        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        contexto.storage_state(path=str(self.state_file))
        log.info("Sessao renovada e gravada em %s", self.state_file)
        return True

    @staticmethod
    def _aceitar_cookies(pagina: Any) -> None:
        for texto in ("Permitir todos os cookies", "Allow all cookies",
                      "Aceitar tudo", "Permitir cookies essenciais"):
            try:
                botao = pagina.get_by_role("button", name=texto)
                if botao.count():
                    botao.first.click(timeout=5000)
                    pagina.wait_for_timeout(1500)
                    return
            except Exception:  # noqa: BLE001
                continue

    @staticmethod
    def _submeter(pagina: Any, campo_para_enter: str = 'input[name="pass"]') -> bool:
        """Envia o formulario sem depender da marcacao do botao.

        O botao "Entrar" do Facebook ja foi `button[name=login]`,
        `#loginbutton` e `[data-testid=royal_login_button]`, e na tela de 2026 e
        outra coisa ainda. Foi exatamente isso que derrubou a primeira
        renovacao em producao: e-mail e senha preenchidos, e o clique estourando
        30 segundos esperando um seletor que nao existe mais.

        Entao: tentativas curtas nos seletores conhecidos e, se nenhum casar,
        **Enter no campo de senha** — que submete o formulario desde sempre e
        nao tem marcacao para mudar.
        """
        seletores = (
            'button[name="login"]',
            '#loginbutton',
            '[data-testid="royal_login_button"]',
            'button[type="submit"]',
            '#checkpointSubmitButton',
            'div[role="button"][aria-label="Entrar"]',
            'div[role="button"]:has-text("Entrar")',
            'button:has-text("Entrar")',
            'button:has-text("Continuar")',
        )
        for seletor in seletores:
            try:
                alvo = pagina.locator(seletor)
                if not alvo.count():
                    continue
                alvo.first.click(timeout=5000)
                log.info("Formulario enviado pelo seletor %s", seletor)
                return True
            except Exception:  # noqa: BLE001
                continue
        try:
            pagina.press(campo_para_enter, "Enter")
            log.info("Formulario enviado com Enter (nenhum botao casou).")
            return True
        except Exception as exc:  # noqa: BLE001
            log.error("Nao consegui enviar o formulario: %s", exc)
            return False

    @staticmethod
    def _tentar_continuar(pagina: Any) -> bool:
        """A tela "Continuar como Fulano" — um clique, e a sessao volta.

        Nao e sessao morta: o Facebook RECONHECE os cookies, mostra a foto e o
        nome da conta e so pede confirmacao. Aparece depois de o acesso mudar de
        lugar, que e exatamente o caso de um bot que roda num servidor.

        Tratar essa tela como "sessao expirou" foi o erro mais caro deste dia:
        levou a refazer login, e refazer login de datacenter e o que dispara o
        CAPTCHA da Arkose — um muro que nao existia no caminho real.
        """
        seletores = (
            'div[role="button"]:has-text("Continuar")',
            'button:has-text("Continuar")',
            'a[role="button"]:has-text("Continuar")',
            'div[role="button"]:has-text("Continue")',
            'button:has-text("Continue")',
        )
        for seletor in seletores:
            try:
                alvo = pagina.locator(seletor)
                if not alvo.count():
                    continue
                alvo.first.click(timeout=8000)
                pagina.wait_for_load_state("domcontentloaded", timeout=30_000)
                pagina.wait_for_timeout(4000)
                log.info("Tela de continuacao resolvida com %s — agora em %s",
                         seletor, pagina.url)
                return True
            except Exception:  # noqa: BLE001
                continue
        return False

    @staticmethod
    def _pede_so_a_senha(pagina: Any) -> bool:
        """A tela "Insira sua senha para continuar": senha sem e-mail."""
        try:
            if pagina.locator('input[name="email"]').count():
                return False
            return pagina.locator('input[name="pass"], input[type="password"]').count() > 0
        except Exception:  # noqa: BLE001
            return False

    @staticmethod
    def _pede_codigo(pagina: Any) -> bool:
        """Reconhece a tela de dois fatores.

        Larga de proposito: se ela nao for reconhecida, o bot segue adiante,
        falha na prova final e devolve "nao consegui" — sem NUNCA pedir o
        codigo a quem poderia responder em dez segundos. O custo de um falso
        positivo aqui e uma pergunta a mais no Telegram; o de um falso negativo
        e a sessao ficar caida.
        """
        url = (pagina.url or "").lower()
        if any(m in url for m in ("two_factor", "two_step", "checkpoint")):
            return True
        for seletor in ('input[name="approvals_code"]',
                        'input[autocomplete="one-time-code"]',
                        'input[name="code"]',
                        'input[id="approvals_code"]'):
            try:
                if pagina.locator(seletor).count():
                    return True
            except Exception:  # noqa: BLE001
                continue
        try:
            texto = pagina.inner_text("body").lower()
        except Exception:  # noqa: BLE001
            return False
        return any(frase in texto for frase in (
            "codigo de login", "código de login",
            "digite o codigo", "digite o código",
            "insira o codigo", "insira o código",
            "authentication code", "login code",
            "aplicativo de autenticacao", "aplicativo de autenticação",
        ))

    @staticmethod
    def _enviar_codigo(pagina: Any, codigo: str) -> bool:
        seletor_usado = ""
        for seletor in ('input[name="approvals_code"]',
                        'input[autocomplete="one-time-code"]',
                        'input[name="code"]',
                        'input[type="text"]'):
            try:
                campo = pagina.locator(seletor)
                if not campo.count():
                    continue
                campo.first.fill(codigo, timeout=15_000)
                seletor_usado = seletor
                break
            except Exception:  # noqa: BLE001
                continue
        if not seletor_usado:
            log.error("Nao achei onde digitar o codigo.")
            return False

        FacebookSource._submeter(pagina, campo_para_enter=seletor_usado)
        pagina.wait_for_timeout(8000)
        log.info("Codigo enviado — pagina agora: %s", pagina.url)
        return True

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
                proxy={"server": self.proxy} if self.proxy else None,
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
            # Antes de declarar a sessao morta: pode ser so a tela de
            # continuacao, que se resolve com um clique e sem credencial
            # nenhuma. Vale a pena tentar — o caminho alternativo (refazer
            # login) e o que esbarra em CAPTCHA.
            if self._tentar_continuar(pagina):
                if grupo.url.split("?")[0] not in (pagina.url or ""):
                    pagina.goto(grupo.url, wait_until="domcontentloaded",
                                timeout=60_000)
                url_atual = (pagina.url or "").lower()

        if any(s in url_atual for s in _SINAIS_LOGIN):
            # Fotografa ANTES de levantar o erro. "A sessao caiu" e uma frase
            # que serve para tres coisas diferentes — pagina de login, pedido de
            # senha de novo, CAPTCHA — e so a tela diz qual delas e.
            self.ultimo_diagnostico = {"fases": [
                {"fase": "coleta", "url": pagina.url, "titulo": grupo.rotulo}]}
            self._fotografar(pagina)
            raise AuthError(
                f"Facebook redirecionou para {pagina.url} — a sessão expirou ou "
                "caiu em checkpoint. Rode `python bot/tools/facebook_login.py` "
                "de novo."
            )

        # O feed monta em etapas; esperar o container do feed é mais confiável
        # que esperar `networkidle`, que num feed infinito nunca acontece.
        try:
            pagina.wait_for_selector('div[role="feed"], div[role="article"]',
                                     timeout=30_000)
        except Exception as exc:  # noqa: BLE001
            log.warning("Grupo %s não renderizou nenhum post: %s", grupo.rotulo, exc)
            return []

        # COLHER A CADA ROLAGEM, NÃO NO FIM.
        #
        # O feed do Facebook é virtualizado: post que sai da tela é desmontado, e
        # o que sobra no DOM é uma div vazia, sem texto e sem link. Rolar quatro
        # vezes e só então extrair — que era o que este método fazia — lia o fim
        # do feed e jogava fora tudo que passou pela tela no caminho. O sintoma
        # era o pior possível: zero post, sem erro nenhum, idêntico a "a conta
        # não é membro deste grupo".
        brutos: dict[str, dict] = {}
        for passo in range(self.scrolls + 1):
            self._expandir(pagina)
            self._hidratar_permalinks(pagina)
            for b in pagina.evaluate(_EXTRAIR_JS) or []:
                pid = str(b.get("pid") or "")
                if not pid:
                    continue
                # Fica com a melhor versão do mesmo post: a passagem em que o
                # "Ver mais" já tinha sido clicado traz o texto inteiro.
                antigo = brutos.get(pid)
                if antigo is None or len(b.get("mensagem") or "") > len(antigo.get("mensagem") or ""):
                    brutos[pid] = b
            if passo < self.scrolls:
                pagina.mouse.wheel(0, 2200)
                pagina.wait_for_timeout(random.randint(900, 1800))

        brutos_lista = list(brutos.values())
        self.ultimos_brutos = brutos_lista  # a sonda lê isto para o diagnóstico
        log.info("Facebook: %s devolveu %d card(s)", grupo.rotulo, len(brutos_lista))

        agora = datetime.now().astimezone()
        jobs: list[Job] = []
        vistos: set[str] = set()
        for bruto in brutos_lista:
            if len(jobs) >= self.max_posts_por_grupo:
                break
            pid = str(bruto.get("pid") or "")
            if not pid or pid in vistos:
                continue
            vistos.add(pid)

            autor = (bruto.get("autor") or "").strip()
            # `mensagem` é o texto que o próprio Facebook marcou como sendo o
            # post. Quando ele existe, limpar seria só arriscar cortar conteúdo.
            mensagem = limpar_sobra_de_botao(bruto.get("mensagem") or "")
            texto = mensagem or limpar_texto(bruto.get("texto") or "", autor)
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
                proxy={"server": self.proxy} if self.proxy else None,
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
            # Mesma tela de continuação da coleta: um clique e a sessão volta.
            if self._tentar_continuar(pagina):
                try:
                    pagina.goto(url, wait_until="domcontentloaded", timeout=45_000)
                except Exception:  # noqa: BLE001
                    return "desconhecida"
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
        #
        # Os três seletores existem porque a página de um post isolado e o feed
        # do grupo não usam a mesma marcação: confiar só em `role="article"` foi
        # o que fez o extrator devolver zero.
        try:
            presente = ('[data-ad-rendering-role="story_message"], '
                        'div[role="article"], div[role="feed"]')
            if pagina.locator(presente).count() > 0:
                return "aberta"
        except Exception:  # noqa: BLE001
            pass
        return "desconhecida"

    @staticmethod
    def _hidratar_permalinks(pagina: Any) -> int:
        """Passa o mouse pelos links do post para o href virar permalink.

        O Facebook entrega esse `<a>` com href de mentira e só o troca pelo link
        real no `mouseover`. Como o bot descarta card sem id, um post inteiro se
        perde por causa de um evento de mouse que nunca aconteceu — falha que
        não dá erro nenhum, só devolve menos post do que o grupo publicou.

        Devolve quantos links foram tocados. Erro aqui nunca interrompe a
        coleta: no pior caso sobram os posts cujo id veio pelo link da foto, e o
        que ficou de fora reaparece na próxima rolagem, quando o card estiver
        no meio da tela.
        """
        try:
            total = int(pagina.evaluate(_MARCAR_LINKS_CRUS_JS) or 0)
        except Exception as exc:  # noqa: BLE001
            log.debug("Não consegui marcar os links do post: %s", exc)
            return 0
        if not total:
            return 0

        tocados = 0
        for i in range(min(total, 40)):
            try:
                pagina.locator(f'[data-fbhover="{i}"]').first.hover(timeout=1000)
                pagina.wait_for_timeout(120)
                tocados += 1
            except Exception:  # noqa: BLE001  — card saiu da tela, menu abriu na frente
                continue
        log.debug("Links tocados para hidratar permalink: %d de %d", tocados, total)
        return tocados

    @staticmethod
    def _expandir(pagina: Any) -> None:
        """Clica nos "Ver mais" **dos posts** para o texto vir inteiro.

        Sem isto o Facebook entrega os primeiros ~250 caracteres, e é justamente
        no fim do post que costumam estar a comarca, o valor e o contato — os
        três dados que decidem se o trabalho serve.

        O `div[role="feed"]` na frente não é decoração. A barra lateral do
        Facebook tem um "Ver mais" próprio (o que abre a lista de atalhos), e a
        versão anterior clicava nele: a página remontava, o feed se redesenhava e
        os posts já carregados sumiam. Medido — cards caíam de 14 para 13 e os
        marcadores de post de 35 para 25 a cada passagem.
        """
        try:
            feed = pagina.locator('div[role="feed"]')
            escopo = feed if feed.count() else pagina.locator("body")
            botoes = escopo.get_by_role(
                "button", name=re.compile(r"^(ver mais|see more)$", re.I)
            )
            total = min(botoes.count(), 30)
        except Exception:  # noqa: BLE001
            return
        for i in range(total):
            try:
                alvo = botoes.nth(i)
                # Botão fora da tela é post que o feed já desmontou: clicar nele
                # rola a página de volta e bagunça a ordem da colheita.
                if not alvo.is_visible():
                    continue
                alvo.click(timeout=1500)
                pagina.wait_for_timeout(120)
            except Exception:  # noqa: BLE001
                continue
