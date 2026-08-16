"""Cria a sessão do Facebook que o bot usa para ler os grupos.

Abre um navegador **com janela**, você loga na mão, o script salva os cookies
num arquivo e fecha. O bot depois carrega esse arquivo e nunca precisa de senha.

    python bot/tools/facebook_login.py                    # salva em ./fb_state.json
    python bot/tools/facebook_login.py caminho/estado.json

Por que na mão e não automatizado: login automatizado do Facebook cai em
checkpoint com muito mais frequência, e resolver checkpoint exige SMS, e-mail ou
foto — nada disso um script faz. Fazer uma vez a cada muitos meses, no navegador,
é mais barato do que manter um robô de login que quebra na hora errada. E tem o
efeito colateral que interessa: **a senha nunca passa por este repositório**.

O que você precisa fazer, uma vez:

1. Rodar este script.
2. Logar na janela que abrir.
3. **Entrar em todos os grupos** que o bot vai ler, com essa mesma conta. O bot
   só enxerga grupo do qual a conta já é membro — ele não pede para entrar.
4. Voltar aqui e apertar ENTER.

Para trocar a conta depois (por exemplo: sair da sua e passar para uma
descartável), é só rodar de novo com a conta nova. O arquivo é sobrescrito.

Em produção o arquivo vive no volume, em `/app/data/fb_state.json`. Para
atualizar, copie o novo por cima — via painel do Coolify ou `docker cp`.
"""

from __future__ import annotations

import sys
from pathlib import Path

DESTINO_PADRAO = Path("fb_state.json")

# Sem headless aqui, obviamente: a janela é o produto deste script.
URL_INICIAL = "https://www.facebook.com/"
URL_GRUPOS = "https://www.facebook.com/groups/feed/"


def main() -> int:
    destino = Path(sys.argv[1]) if len(sys.argv) > 1 else DESTINO_PADRAO

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("ERRO: playwright não instalado.\n"
              "  pip install playwright\n"
              "  playwright install chromium")
        return 1

    print("=" * 70)
    print("LOGIN DO FACEBOOK — ADV Jobs · Milena Rezner")
    print("=" * 70)
    print(f"A sessão será salva em: {destino.resolve()}")
    print()

    with sync_playwright() as pw:
        navegador = pw.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
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
        pagina.goto(URL_INICIAL, wait_until="domcontentloaded")

        print("1) Faça login na janela que abriu.")
        print("2) Entre em TODOS os grupos que o bot vai ler.")
        print("   (o bot só lê grupo em que esta conta já é membro)")
        print("3) Volte aqui e aperte ENTER.")
        print()
        input(">>> ENTER quando estiver logado e dentro dos grupos... ")

        # Confere de verdade em vez de confiar no ENTER. Salvar uma sessão que
        # não está logada produziria exatamente o modo de falha que o bot inteiro
        # foi desenhado para evitar: parecer que funciona e devolver zero post.
        pagina.goto(URL_GRUPOS, wait_until="domcontentloaded")
        pagina.wait_for_timeout(3000)
        url = (pagina.url or "").lower()
        if "login" in url or "checkpoint" in url:
            print()
            print(f"ERRO: ainda não está logado (a página foi para {pagina.url}).")
            print("Nada foi salvo. Rode de novo e conclua o login.")
            navegador.close()
            return 1

        cookies = contexto.cookies()
        tem_sessao = any(c.get("name") == "c_user" for c in cookies)
        if not tem_sessao:
            print()
            print("ERRO: o cookie de sessão (c_user) não apareceu. Nada foi salvo.")
            navegador.close()
            return 1

        destino.parent.mkdir(parents=True, exist_ok=True)
        contexto.storage_state(path=str(destino))
        navegador.close()

    print()
    print("=" * 70)
    print(f"OK — sessão salva em {destino.resolve()}")
    print()
    print("Próximos passos:")
    print("  • local:     é só rodar o bot; ele lê DATA_DIR/fb_state.json")
    print("  • produção:  copiar este arquivo para o volume, em")
    print("               /app/data/fb_state.json")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
