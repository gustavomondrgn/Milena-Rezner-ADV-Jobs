"""Lista os grupos de que a conta logada é membro, no formato do `facebook_groups.txt`.

    python bot/tools/listar_grupos.py [--visivel] [--rolagens N]
                                      [--rapido] [--escrever]

Existe porque a lista de grupos é o que define o alcance do bot inteiro, e
montá-la na mão é copiar URL a URL do navegador. O bot **não entra** em grupo:
ele lê o que a conta já pode ler. Então esta ferramenta responde à pergunta que
vem antes de qualquer coleta — *o que essa conta enxerga hoje?*

A saída sai pronta para colar no `bot/config/facebook_groups.txt`, ou vai direto
para lá com `--escrever` (o arquivo antigo vira `.bak`).

Por padrão a ferramenta **abre cada grupo** depois de listar, para pegar o nome
de verdade e o id numérico. Isso não é capricho: a lista lateral do Facebook
mistura texto de notificação com nome de grupo ("Não lidaAgora você pode postar
e comentar no grupo..."), e o mesmo grupo aparece duas vezes quando tem apelido
na URL — uma pelo apelido, outra pelo id. Sem resolver, o bot leria o grupo duas
vezes por ciclo e o painel mostraria um nome que não existe. `--rapido` pula essa
parte quando você só quer olhar.

Não publica nada, não entra em grupo, não segue ninguém — só lê a própria lista.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ESTADO_PADRAO = Path("fb_state.json")

# "Seus grupos". A aba `/joins/` é a que lista participação; o feed `/feed/` só
# mostra quem publicou recentemente e esconderia grupo quieto — que é justamente
# o grupo que interessa descobrir aqui.
URLS = (
    "https://www.facebook.com/groups/joins/",
    "https://www.facebook.com/groups/feed/",
)

ARQUIVO_GRUPOS = Path("bot/config/facebook_groups.txt")
MARCADOR_PENDENTES = "# --- AGUARDANDO APROVAÇÃO DO ADMIN"

# Lixo que a lista lateral cola no nome: notificação, convite, contador.
_NOME_SUJO = re.compile(
    r"n[ãa]o lida|agora voc[êe] pode|aproveite sua participa[çc][ãa]o|"
    r"\bconvidou\b|\bconvite\b|novos? posts?|\d+\s*(?:min|h|d)\b",
    re.I,
)

# O id numérico do grupo aparece no próprio HTML da página, mesmo quando a URL
# usa apelido. É o que permite reconhecer que o apelido e o número são o mesmo
# grupo — sem isso o bot leria os dois.
_ID_NA_PAGINA = re.compile(r'"group(?:_i|I)d"\s*:\s*"?(\d{8,})"?')

# Estado de acesso ao grupo. A pergunta que importa não é "sou membro?", é "eu
# consigo LER?" — e a resposta é o feed existir. Grupo com pedido pendente mostra
# o nome, os membros e a descrição, e esconde os posts: pelo lado do bot é
# idêntico a um grupo vazio, e sem esta checagem o diagnóstico do extrator ficaria
# sendo feito contra uma página que nunca teria post nenhum.
_ACESSO_JS = r"""
() => {
  const t = document.body.innerText || '';
  return {
    feed: !!document.querySelector('div[role="feed"]'),
    posts: document.querySelectorAll('[data-ad-rendering-role="story_message"]').length,
    pendente: /solicita[çc][ãa]o de participa[çc][ãa]o est[áa] pendente|cancelar solicita[çc][ãa]o|your request to join/i.test(t),
    privado: /grupo privado|private group/i.test(t),
  };
}
"""

_EXTRAIR_JS = r"""
() => {
  const vistos = new Map();
  for (const a of document.querySelectorAll('a[href*="/groups/"]')) {
    const h = a.getAttribute('href') || '';
    const m = h.match(/\/groups\/([^/?#]+)\/?(?:[?#]|$)/);
    if (!m) continue;
    const slug = m[1];
    // Abas da própria interface, não grupos.
    if (['feed','joins','discover','create','search','your_groups','notifications']
        .includes(slug)) continue;
    const nome = (a.innerText || '').trim().split('\n')[0];
    if (!nome) continue;
    // Um mesmo grupo aparece várias vezes (avatar sem texto, nome, atalho).
    // Fica com a ocorrência que trouxe nome legível.
    if (!vistos.has(slug) || vistos.get(slug).length < nome.length) {
      vistos.set(slug, nome);
    }
  }
  return Array.from(vistos, ([slug, nome]) => ({slug, nome}));
}
"""


def main() -> int:
    for fluxo in (sys.stdout, sys.stderr):
        try:
            fluxo.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass

    visivel = "--visivel" in sys.argv
    rapido = "--rapido" in sys.argv
    escrever = "--escrever" in sys.argv
    rolagens = 6
    for i, a in enumerate(sys.argv):
        if a == "--rolagens" and i + 1 < len(sys.argv):
            rolagens = int(sys.argv[i + 1])

    estado = ESTADO_PADRAO
    if not estado.exists():
        print(f"ERRO: sessão não encontrada em {estado.resolve()}.")
        print("Rode primeiro: python bot/tools/facebook_login.py")
        return 1

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("ERRO: playwright não instalado.")
        return 1

    achados: dict[str, str] = {}
    detalhe: dict[str, dict] = {}
    with sync_playwright() as pw:
        navegador = pw.chromium.launch(
            headless=not visivel,
            args=["--disable-blink-features=AutomationControlled",
                  "--no-sandbox", "--disable-dev-shm-usage"],
        )
        contexto = navegador.new_context(
            storage_state=str(estado),
            locale="pt-BR",
            timezone_id="America/Sao_Paulo",
            viewport={"width": 1366, "height": 900},
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/131.0.0.0 Safari/537.36"),
        )
        contexto.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
        )
        pagina = contexto.new_page()

        for url in URLS:
            try:
                pagina.goto(url, wait_until="domcontentloaded", timeout=60_000)
                pagina.wait_for_timeout(4000)
            except Exception as exc:  # noqa: BLE001
                print(f"(falhou abrindo {url}: {type(exc).__name__})")
                continue

            atual = (pagina.url or "").lower()
            if "login" in atual or "checkpoint" in atual:
                print(f"SESSÃO MORTA — a página foi para {pagina.url}")
                navegador.close()
                return 1

            for _ in range(rolagens):
                pagina.mouse.wheel(0, 2000)
                pagina.wait_for_timeout(1200)

            for item in pagina.evaluate(_EXTRAIR_JS) or []:
                slug, nome = item["slug"], item["nome"]
                if _NOME_SUJO.search(nome):
                    nome = ""
                atual = achados.get(slug, "")
                if slug not in achados or len(atual) < len(nome):
                    achados[slug] = nome

        # Abre cada grupo para pegar o nome real e o id numérico. É o passo que
        # transforma uma lista aproximada numa lista confiável.
        if not rapido and achados:
            print(f"Abrindo os {len(achados)} grupos para confirmar nome e id...")
            resolvidos: dict[str, dict] = {}
            for slug in list(achados):
                nome, ident, acesso = achados[slug], slug, {}
                try:
                    pagina.goto(f"https://www.facebook.com/groups/{slug}/",
                                wait_until="domcontentloaded", timeout=60_000)
                    pagina.wait_for_timeout(4000)
                    titulo = (pagina.title() or "").strip()
                    if titulo.lower().endswith("| facebook"):
                        titulo = titulo[: -len("| facebook")].strip(" |")
                    # "(3) Nome do grupo" — o (3) é o contador de notificações
                    # não lidas da aba, não parte do nome.
                    titulo = re.sub(r"^\(\d+\)\s*", "", titulo)
                    if titulo and not _NOME_SUJO.search(titulo):
                        nome = titulo
                    achado = _ID_NA_PAGINA.search(pagina.content() or "")
                    if achado:
                        ident = achado.group(1)
                    acesso = pagina.evaluate(_ACESSO_JS) or {}
                except Exception as exc:  # noqa: BLE001
                    print(f"  ({slug}: não consegui abrir — {type(exc).__name__})")

                registro = {"slug": slug, "nome": nome,
                            "legivel": bool(acesso.get("feed")),
                            "posts": int(acesso.get("posts") or 0),
                            "pendente": bool(acesso.get("pendente")),
                            "privado": bool(acesso.get("privado"))}

                # Chave é o id numérico quando ele existe: é o que faz apelido e
                # número colapsarem no mesmo grupo.
                anterior = resolvidos.get(ident)
                if anterior is None:
                    resolvidos[ident] = registro
                else:
                    print(f"  (duplicado: {slug} é o mesmo grupo que "
                          f"{anterior['slug']} — mantido uma vez só)")
                    # Prefere o apelido, que é estável e legível na URL.
                    if not slug.isdigit():
                        registro["nome"] = nome or anterior["nome"]
                        resolvidos[ident] = registro
                    elif not anterior["nome"]:
                        anterior["nome"] = nome
            detalhe = {v["slug"]: v for v in resolvidos.values()}
            achados = {s: v["nome"] for s, v in detalhe.items()}

        navegador.close()

    print("=" * 78)
    print(f"GRUPOS VISÍVEIS PARA ESTA CONTA: {len(achados)}")
    print("=" * 78)
    if not achados:
        print("Nenhum. Ou a conta não participa de grupo nenhum, ou a página não")
        print("carregou — rode com --visivel para olhar.")
        return 1

    largura = max(len(s) for s in achados) + 44
    ordenados = sorted(achados.items(), key=lambda kv: (kv[1] or kv[0]).lower())

    def _linha(slug: str, nome: str) -> str:
        return f"{'https://www.facebook.com/groups/' + slug:<{largura}} | {nome} |"

    # Sem o passo de detalhe não há como saber quem é legível; aí todos entram.
    legiveis = [(s, n) for s, n in ordenados
                if not detalhe or detalhe.get(s, {}).get("legivel")]
    bloqueados = [(s, n) for s, n in ordenados if (s, n) not in legiveis]

    print()
    for slug, nome in ordenados:
        d = detalhe.get(slug, {})
        if not detalhe:
            marca = ""
        elif d.get("legivel"):
            marca = f"  ✔ lê ({d.get('posts', 0)} post(s) na primeira tela)"
        elif d.get("pendente"):
            marca = "  ✖ PEDIDO PENDENTE — o admin ainda não aprovou"
        elif d.get("privado"):
            marca = "  ✖ privado e sem acesso"
        else:
            marca = "  ✖ sem feed (não carregou ou exige entrar)"
        print(_linha(slug, nome) + marca)
    print()

    if detalhe and bloqueados:
        print(f"{len(legiveis)} de {len(ordenados)} dão para ler agora. Os outros "
              f"{len(bloqueados)} dependem de aprovação do admin do grupo —")
        print("é normal levar de horas a dias, e alguns pedem resposta a pergunta.")
        print("Quando aprovarem, rode este comando de novo: eles entram sozinhos.")
        print()

    linhas = [_linha(s, n) for s, n in legiveis]

    if not escrever:
        print("Cole no bot/config/facebook_groups.txt (ou rode com --escrever).")
        print("A UF fica em branco: grupo nacional não tem UF, e presumir uma")
        print("seria pior que não presumir nada.")
        return 0

    if not ARQUIVO_GRUPOS.exists():
        print(f"ERRO: {ARQUIVO_GRUPOS} não existe. Rode a partir da raiz do projeto.")
        return 1

    atual = ARQUIVO_GRUPOS.read_text(encoding="utf-8")
    ARQUIVO_GRUPOS.with_suffix(".txt.bak").write_text(atual, encoding="utf-8")
    # Preserva o cabeçalho comentado — ele é a documentação do formato — e troca
    # só a parte de baixo, que é a lista de verdade.
    # O cabeçalho comentado é documentação do formato e fica. Mas o bloco de
    # pendentes que ESTA ferramenta escreveu na rodada anterior também é
    # comentário: sem cortar aqui, ele seria lido como cabeçalho e reaparecia
    # duplicado a cada execução.
    corte = atual.find(MARCADOR_PENDENTES)
    base = atual[:corte] if corte >= 0 else atual
    cabecalho = [ln for ln in base.splitlines()
                 if ln.startswith("#") or not ln.strip()]
    corpo = "\n".join(linhas) + "\n"
    if bloqueados:
        # Ficam comentados, não somem. Grupo pendente carregaria uma página por
        # ciclo para devolver zero post — e some da vista de quem for editar o
        # arquivo na mão, que é como se perde um grupo aprovado semanas depois.
        corpo += ("\n" + MARCADOR_PENDENTES + " " + "-" * 41 + "\n"
                  "# Descomente quando o pedido for aprovado (ou rode o "
                  "listar_grupos.py de novo).\n")
        corpo += "".join(f"# {_linha(s, n)}\n" for s, n in bloqueados)
    novo = "\n".join(cabecalho).rstrip() + "\n\n" + corpo
    ARQUIVO_GRUPOS.write_text(novo, encoding="utf-8")
    print(f"Escrito em {ARQUIVO_GRUPOS}: {len(linhas)} ativo(s), "
          f"{len(bloqueados)} comentado(s) aguardando aprovação. "
          f"O anterior virou {ARQUIVO_GRUPOS.name}.bak")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
