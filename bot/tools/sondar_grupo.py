"""Lê UM grupo do Facebook e mostra o que o extrator viu. Não publica nada.

É a ferramenta de ajuste do projeto. O DOM do Facebook muda sem aviso, e a
diferença entre "o bot está funcionando" e "o bot está lendo lixo" não aparece
no log de produção — aparece aqui, olhando o texto que saiu de cada card.

    python bot/tools/sondar_grupo.py <url-ou-id-do-grupo> [--visivel] [--rolagens N]

O que ela responde, em ordem de importância:

1. A sessão está viva? (o modo de falha silencioso deste bot)
2. Quantos cards viraram post aproveitável, e quantos foram descartados por quê
3. O texto ficou limpo? Sobrou rodapé de reações, comentário de terceiro, nome
   do autor grudado no começo?
4. O que o pré-filtro de divulgação faria com cada post

Não chama a API do Gemini e não manda nada para o Telegram — dá para rodar à
vontade sem gastar cota nem arriscar publicar num grupo de cliente.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import facebook  # noqa: E402
import filters  # noqa: E402
from sources import AuthError  # noqa: E402

ESTADO_PADRAO = Path("fb_state.json")
LARGURA = 78


def barra(texto: str = "") -> None:
    print("=" * LARGURA)
    if texto:
        print(texto)
        print("=" * LARGURA)


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    visivel = "--visivel" in sys.argv
    rolagens = 4
    for i, a in enumerate(sys.argv):
        if a == "--rolagens" and i + 1 < len(sys.argv):
            rolagens = int(sys.argv[i + 1])

    if not args:
        print(__doc__)
        return 1

    estado = ESTADO_PADRAO
    if not estado.exists():
        print(f"ERRO: sessão não encontrada em {estado.resolve()}.")
        print("Rode primeiro: python bot/tools/facebook_login.py")
        return 1

    grupo = facebook.Grupo(slug=args[0], nome="sonda")
    # Reaproveita a fonte de verdade em vez de reimplementar a leitura: sonda que
    # usa outro caminho de código mede a sonda, não o bot.
    fonte = facebook.FacebookSource(
        groups_file=Path("nao-usado.txt"),
        state_file=estado,
        max_posts_por_grupo=999,
        scrolls=rolagens,
        headless=not visivel,
    )

    barra(f"SONDA — grupo {grupo.slug}")
    print(f"sessão   : {estado.resolve()}")
    print(f"navegador: {'com janela' if visivel else 'headless'}")
    print(f"rolagens : {rolagens}")
    print()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("ERRO: playwright não instalado.")
        return 1

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

        try:
            jobs = fonte._ler_grupo(pagina, grupo)
        except AuthError as exc:
            print("SESSÃO MORTA — é este o modo de falha que não dá sintoma em")
            print("produção: o Facebook devolve a página de login, o bot lê zero")
            print("posts e o grupo do Telegram apenas emudece.")
            print()
            print(f"  {exc}")
            navegador.close()
            return 1
        except Exception as exc:  # noqa: BLE001
            print(f"FALHOU: {type(exc).__name__}: {exc}")
            navegador.close()
            return 1

        # Roda a extração crua de novo, só para comparar antes/depois da limpeza.
        brutos = pagina.evaluate(facebook._EXTRAIR_JS) or []
        navegador.close()

    barra("RESUMO")
    print(f"cards com permalink encontrados : {len(brutos)}")
    print(f"posts aproveitáveis             : {len(jobs)}")
    curtos = sum(
        1 for b in brutos
        if len(facebook.limpar_texto(b.get('texto') or '', b.get('autor') or '')) < 40
    )
    print(f"descartados por texto < 40 chars: {curtos}")
    print()

    if not jobs:
        print("NENHUM POST APROVEITÁVEL. Causas prováveis, em ordem:")
        print("  1. a conta não é membro deste grupo (o mais comum)")
        print("  2. o grupo não teve publicação recente")
        print("  3. o seletor de extração quebrou — rode com --visivel e olhe")
        return 1

    divulgacao = 0
    for i, job in enumerate(jobs, 1):
        eh_divulgacao = filters.parece_divulgacao(job.description)
        if eh_divulgacao:
            divulgacao += 1
        barra()
        print(f"[{i}] {job.title}")
        print(f"    id={job.source_id}  quando={job.published_at}")
        print(f"    autor={job.company!r}  uf_do_grupo={job.location!r}")
        print(f"    pré-filtro divulgação: {'CORTA' if eh_divulgacao else 'passa'}")
        print()
        for linha in job.description.split("\n"):
            print(f"    | {linha}")
        print()
        print(f"    {job.url}")

    barra("VEREDITO")
    print(f"{len(jobs)} post(s) · {divulgacao} seriam cortados como divulgação · "
          f"{len(jobs) - divulgacao} chegariam ao classificador")
    print()
    print("O que conferir no texto acima:")
    print("  • começa direto no que a pessoa escreveu (sem nome nem horário)?")
    print("  • termina antes de 'Curtir / Comentar' e sem comentário de terceiro?")
    print("  • posts longos vieram INTEIROS? (é no fim que estão comarca,")
    print("    valor e contato — se estiverem truncados, o 'Ver mais' falhou)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
