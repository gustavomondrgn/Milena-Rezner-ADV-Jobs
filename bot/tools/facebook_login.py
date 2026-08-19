"""Cria a sessão do Facebook que o bot usa para ler os grupos.

Abre um navegador **com janela**, a sessão é criada ali e salva num arquivo de
cookies. O bot depois carrega esse arquivo e nunca precisa de senha.

    python bot/tools/facebook_login.py                    # salva em ./fb_state.json
    python bot/tools/facebook_login.py caminho/estado.json

Dois modos, e o navegador é com janela nos dois:

* **manual** (padrão quando não há credencial): você loga na mão e aperta ENTER.
* **assistido**: se `FB_EMAIL` e `FB_PASSWORD` estiverem no `.env` (ou vierem em
  `--email` / `--senha`), o script preenche o formulário e **espera** o cookie de
  sessão aparecer. Se o Facebook pedir código, foto ou "foi você?", a janela está
  aberta na sua frente — resolva ali que o script continua sozinho.

Por que a parte final nunca é automatizada: checkpoint exige SMS, e-mail ou foto,
e nada disso um script faz. O modo assistido só poupa a digitação; quem passa
pelo checkpoint é você.

O que você precisa fazer, uma vez:

1. Rodar este script.
2. Concluir o login na janela que abrir (ou só olhar, no modo assistido).
3. **Entrar em todos os grupos** que o bot vai ler, com essa mesma conta. O bot
   só enxerga grupo do qual a conta já é membro — ele não pede para entrar.

QUANDO O FACEBOOK PEDE CAPTCHA NO SERVIDOR
------------------------------------------
Login novo vindo de datacenter dispara o desafio da Arkose ("verifique se você
é humano"), que script nenhum resolve. A saída é fazer o login aqui, na mão,
mas **saindo pelo IP do servidor**:

    ssh -D 1080 -N usuario@ip-da-vps           # noutra janela, deixa aberto
    python bot/tools/facebook_login.py --proxy socks5://127.0.0.1:1080

Você resolve o CAPTCHA e o 2FA na janela; a sessão nasce com o IP da VPS, que é
o mesmo IP de onde o bot vai navegar depois. Aí `sessao_para_env.py` e colar em
FACEBOOK_STATE_B64.

Para trocar a conta depois (por exemplo: sair da sua e passar para uma
descartável), é só rodar de novo com a conta nova. O arquivo é sobrescrito.

Em produção o arquivo vive no volume, em `/app/data/fb_state.json`. Para
atualizar, copie o novo por cima — via painel do Coolify ou `docker cp`.

A senha continua fora do repositório: o `.env` é `.gitignore`, e o que o bot lê
é o `fb_state.json`, nunca a credencial.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
DESTINO_PADRAO = Path("fb_state.json")

# Sem headless aqui, obviamente: a janela é o produto deste script.
URL_INICIAL = "https://www.facebook.com/"
URL_LOGIN = "https://www.facebook.com/login.php"
URL_GRUPOS = "https://www.facebook.com/groups/feed/"

# Quanto tempo esperar a sessão aparecer no modo assistido. Generoso de
# propósito: o relógio corre enquanto você procura o código no celular.
ESPERA_PADRAO_S = 420

TEXTOS_COOKIE = (
    "Permitir todos os cookies",
    "Allow all cookies",
    "Aceitar tudo",
    "Only allow essential cookies",
    "Permitir cookies essenciais",
)


OPCOES_COM_VALOR = ("--email", "--senha", "--espera", "--proxy")


def _console_utf8() -> None:
    """O console do Windows abre em cp1252 e engasga em '→' e em acento.

    Sem isto o script morre com UnicodeEncodeError no meio da espera — perdendo
    a janela já logada junto.
    """
    for fluxo in (sys.stdout, sys.stderr):
        try:
            fluxo.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass


def _parse_argv(argv: list[str]) -> tuple[list[str], dict[str, str]]:
    """Separa posicionais de opções, sem confundir o valor de `--espera 420`
    com o caminho do arquivo de sessão."""
    posicionais: list[str] = []
    opcoes: dict[str, str] = {}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a.startswith("--"):
            if "=" in a:
                nome, valor = a.split("=", 1)
                opcoes[nome] = valor
            elif a in OPCOES_COM_VALOR and i + 1 < len(argv):
                opcoes[a] = argv[i + 1]
                i += 1
            else:
                opcoes[a] = "1"
        else:
            posicionais.append(a)
        i += 1
    return posicionais, opcoes


def _carregar_env() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(RAIZ / ".env")


def _fechar_banner_cookies(pagina) -> None:
    """O banner de cookies cobre o formulário e o clique no login não pega."""
    for texto in TEXTOS_COOKIE:
        try:
            botao = pagina.get_by_role("button", name=texto).first
            if botao.is_visible(timeout=1500):
                botao.click()
                pagina.wait_for_timeout(1000)
                return
        except Exception:  # noqa: BLE001  — banner ausente é o caso comum
            continue


def _preencher_login(pagina, email: str, senha: str) -> bool:
    try:
        pagina.goto(URL_LOGIN, wait_until="domcontentloaded")
        pagina.wait_for_timeout(2000)
        _fechar_banner_cookies(pagina)

        campo_email = pagina.locator("input[name='email']").first
        campo_senha = pagina.locator("input[name='pass']").first
        campo_email.wait_for(state="visible", timeout=15000)

        campo_email.click()
        campo_email.fill(email)
        pagina.wait_for_timeout(300)
        campo_senha.click()
        campo_senha.fill(senha)
        pagina.wait_for_timeout(300)

        botao = pagina.locator("button[name='login']").first
        if botao.count() and botao.is_visible():
            botao.click()
        else:
            campo_senha.press("Enter")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"  (não consegui preencher o formulário: {type(exc).__name__}: {exc})")
        print("  Sem problema — a janela está aberta, faça o login na mão.")
        return False


def _tem_sessao(contexto) -> bool:
    return any(c.get("name") == "c_user" for c in contexto.cookies())


def _esperar_sessao(pagina, contexto, limite_s: int) -> bool:
    """Espera o cookie `c_user` aparecer, avisando o que a página está pedindo.

    É aqui que o checkpoint acontece. O script não tenta resolver: só descreve o
    que vê e continua esperando, porque quem resolve é a pessoa na frente da tela.
    """
    inicio = time.monotonic()
    ultimo_aviso = ""
    while time.monotonic() - inicio < limite_s:
        if _tem_sessao(contexto):
            return True

        url = (pagina.url or "").lower()
        aviso = ""
        if "checkpoint" in url:
            aviso = "CHECKPOINT: o Facebook quer confirmar que é você — resolva na janela."
        elif "two_step" in url or "two_factor" in url or "authentication" in url:
            aviso = "DOIS FATORES: digite o código na janela."
        elif "login" in url and "login.php" not in url:
            aviso = "ainda na tela de login."
        if aviso and aviso != ultimo_aviso:
            restante = int(limite_s - (time.monotonic() - inicio))
            print(f"  → {aviso} (esperando até {restante}s)")
            ultimo_aviso = aviso

        try:
            pagina.wait_for_timeout(3000)
        except Exception:  # noqa: BLE001  — janela fechada na mão
            return _tem_sessao(contexto)
    return _tem_sessao(contexto)


def main() -> int:
    _console_utf8()
    _carregar_env()

    posicionais, opcoes = _parse_argv(sys.argv[1:])

    destino = Path(posicionais[0]) if posicionais else DESTINO_PADRAO
    email = opcoes.get("--email") or os.getenv("FB_EMAIL", "").strip()
    senha = opcoes.get("--senha") or os.getenv("FB_PASSWORD", "").strip()
    espera = int(opcoes.get("--espera") or ESPERA_PADRAO_S)
    proxy = (opcoes.get("--proxy") or os.getenv("FACEBOOK_PROXY", "")).strip()
    assistido = bool(email and senha)

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
    print(f"Modo: {'assistido (credencial do .env)' if assistido else 'manual'}")
    if assistido:
        print(f"Conta: {email}")
    if proxy:
        print(f"Saindo por: {proxy}")
    print()

    with sync_playwright() as pw:
        # `--proxy socks5://127.0.0.1:1080` faz esta janela sair pela VPS (via
        # `ssh -D 1080`). Serve para o caso especifico em que o Facebook
        # responde com CAPTCHA a login vindo de datacenter: aqui quem resolve o
        # CAPTCHA e uma pessoa, e mesmo assim a sessao nasce com o IP do
        # servidor — que e o IP que vai navegar depois.
        navegador = pw.chromium.launch(
            headless=False,
            proxy={"server": proxy} if proxy else None,
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

        if assistido:
            print("Preenchendo o formulário de login...")
            _preencher_login(pagina, email, senha)
            print("Esperando a sessão. Se aparecer código, foto ou 'foi você?',")
            print("resolva na janela — o script segue sozinho depois.")
            ok = _esperar_sessao(pagina, contexto, espera)
        else:
            pagina.goto(URL_INICIAL, wait_until="domcontentloaded")
            print("1) Faça login na janela que abriu.")
            print("2) Entre em TODOS os grupos que o bot vai ler.")
            print("   (o bot só lê grupo em que esta conta já é membro)")
            print()
            if sys.stdin and sys.stdin.isatty():
                print("3) Volte aqui e aperte ENTER.")
                input(">>> ENTER quando estiver logado e dentro dos grupos... ")
                ok = True
            else:
                # Sem terminal interativo (rodando por agente/CI): esperar o
                # cookie é o único jeito de saber que o login terminou.
                print(f"3) Terminado o login, é só deixar a janela — espero até {espera}s.")
                ok = _esperar_sessao(pagina, contexto, espera)

        # Confere de verdade em vez de confiar no ENTER. Salvar uma sessão que
        # não está logada produziria exatamente o modo de falha que o bot inteiro
        # foi desenhado para evitar: parecer que funciona e devolver zero post.
        if ok:
            try:
                pagina.goto(URL_GRUPOS, wait_until="domcontentloaded")
                pagina.wait_for_timeout(3000)
                url = (pagina.url or "").lower()
            except Exception:  # noqa: BLE001
                url = ""
            if "login" in url or "checkpoint" in url:
                print()
                print(f"ERRO: ainda não está logado (a página foi para {pagina.url}).")
                print("Nada foi salvo. Rode de novo e conclua o login.")
                navegador.close()
                return 1

        if not _tem_sessao(contexto):
            print()
            print("ERRO: o cookie de sessão (c_user) não apareceu. Nada foi salvo.")
            print("Se o Facebook pediu confirmação e o tempo acabou, rode de novo")
            print("com mais folga:  --espera 900")
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
