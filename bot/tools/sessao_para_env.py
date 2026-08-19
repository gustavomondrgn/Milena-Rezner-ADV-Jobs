"""Transforma o `fb_state.json` na variável de ambiente que o servidor entende.

O bot em produção precisa da sessão do Facebook dentro do volume. Copiar o
arquivo na mão (`docker cp`) depois de cada primeiro deploy é um passo fácil de
esquecer — e esquecer não dá erro: o bot sobe, roda, e o grupo simplesmente
emudece. Por isso a sessão viaja pelo ambiente, e o bot a escreve no volume no
boot (`main.semear_sessao_facebook`).

    python bot/tools/sessao_para_env.py

Escreve `fb_state.b64.txt` ao lado do arquivo de sessão e imprime só o resumo.
O conteúdo NÃO vai para a tela de propósito: ele vale tanto quanto a senha da
conta, e tela vira print, print vira mensagem de WhatsApp.

Depois: cole o conteúdo do arquivo em `FACEBOOK_STATE_B64`, no Coolify, e
redeploy. Trocar a sessão mais tarde é repetir isso — o bot só sobrescreve a
sessão do volume quando o valor da variável muda.
"""
from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")  # o console do Windows é cp1252

RAIZ = Path(__file__).resolve().parents[2]


def main() -> int:
    bruto = os.getenv("FACEBOOK_STATE_FILE", "").strip()
    origem = Path(bruto) if bruto else RAIZ / "fb_state.json"
    if len(sys.argv) > 1:
        origem = Path(sys.argv[1])

    if not origem.exists():
        print(f"Sessão não encontrada em {origem}.")
        print("Gere com: python bot/tools/facebook_login.py")
        return 1

    dados = json.loads(origem.read_text(encoding="utf-8"))
    cookies = dados.get("cookies") or []
    if not cookies:
        print(f"{origem} não tem cookies dentro — essa sessão não serve.")
        return 1

    b64 = base64.b64encode(origem.read_bytes()).decode()
    destino = origem.with_suffix(".b64.txt")
    destino.write_text(b64, encoding="utf-8")

    print(f"Sessão lida de {origem} ({len(cookies)} cookies)")
    print(f"Base64 escrito em {destino} ({len(b64)} caracteres)")
    print()
    print("Cole o conteúdo desse arquivo na variável FACEBOOK_STATE_B64 do")
    print("Coolify e redeploy. Depois apague o .b64.txt: ele vale tanto quanto")
    print("a senha da conta.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
