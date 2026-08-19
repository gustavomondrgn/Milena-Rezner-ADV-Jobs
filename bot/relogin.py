"""Peças para o bot refazer o login do Facebook sozinho, dentro do servidor.

## Por que isto existe

A sessão criada na máquina de casa é recusada quando aparece num datacenter:
para o Facebook é aparelho novo em lugar novo, e aparelho novo cai no
checkpoint. O sintoma é cruel — a sessão continua **válida** na máquina de
origem, então tudo parece certo, e mesmo assim o servidor recebe a página de
login e o grupo emudece.

A sessão que **nasce no servidor** não tem esse problema: o IP que fez o login
é o mesmo IP que vai navegar depois. Este módulo dá ao bot as duas coisas que
faltavam para conseguir isso sem ninguém na frente da tela:

1. **o código de verificação**, que pode vir de um segredo TOTP (automático,
   sem humano) ou de uma mensagem no Telegram (humano, mas de qualquer lugar);
2. **um lugar seguro para esperar por ele** — uma caixa com trava, que o laço
   do bot preenche e a thread do login consome.

## O que este módulo NÃO resolve

Checkpoint que pede foto de documento, confirmação por e-mail ou "reconheça
seus amigos" continua exigindo gente. Nesses casos o bot avisa e para de tentar
— tentar de novo em cima de um checkpoint aberto é o caminho mais rápido para
perder a conta de vez.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import re
import struct
import threading
import time

log = logging.getLogger("advjobs.relogin")

# Um código de verificação do Facebook. Aceita com ou sem espaço no meio, que é
# como o autenticador mostra e como a pessoa copia.
_PADRAO_CODIGO = re.compile(r"\b(\d{6}|\d{3}\s\d{3})\b")


def extrair_codigo(texto: str) -> str | None:
    """Tira o código de uma mensagem escrita por gente.

    Aceita `/codigo 123456`, `123456`, `123 456` e `o codigo e 123456`. O que
    não pode é aceitar QUALQUER número: `/codigo` sem nada, ou uma frase com um
    ano dentro, viraria uma tentativa de login com código errado — e código
    errado repetido é o que endurece o checkpoint.
    """
    if not texto:
        return None
    achado = _PADRAO_CODIGO.search(texto)
    if not achado:
        return None
    return achado.group(1).replace(" ", "")


def codigo_totp(segredo: str, agora: float | None = None) -> str:
    """Código de 6 dígitos do autenticador, a partir do segredo em base32.

    Implementado aqui em vez de trazer uma biblioteca: são doze linhas de
    RFC 6238 e o bot já carrega um navegador inteiro de dependência.
    """
    limpo = re.sub(r"\s+", "", segredo).upper()
    faltando = (-len(limpo)) % 8
    chave = base64.b32decode(limpo + "=" * faltando, casefold=True)
    intervalo = int((agora if agora is not None else time.time()) // 30)
    digest = hmac.new(chave, struct.pack(">Q", intervalo), hashlib.sha1).digest()
    deslocamento = digest[-1] & 0x0F
    numero = struct.unpack(">I", digest[deslocamento:deslocamento + 4])[0] & 0x7FFFFFFF
    return f"{numero % 1_000_000:06d}"


class CodigoPendente:
    """Caixa com trava onde o código de verificação é depositado.

    A thread que está fazendo o login pede e **espera**; o laço que ouve o
    Telegram entrega. Sem isto, o login teria de ficar lendo updates do
    Telegram por conta própria — dois consumidores de `getUpdates` no mesmo
    bot, que é receita de mensagem perdida.
    """

    def __init__(self) -> None:
        self._trava = threading.Lock()
        self._chegou = threading.Event()
        self._codigo: str | None = None
        self._aguardando_desde: float | None = None
        self._usados: set[str] = set()

    @property
    def aguardando(self) -> bool:
        with self._trava:
            return self._aguardando_desde is not None

    def pedir(self) -> None:
        with self._trava:
            self._codigo = None
            self._aguardando_desde = time.time()
        self._chegou.clear()

    def entregar(self, codigo: str) -> bool:
        """True se o código foi aceito por alguém que estava esperando.

        Código repetido é recusado: o do Facebook é de uso único, e reenviar o
        mesmo só gasta tentativa.
        """
        with self._trava:
            if self._aguardando_desde is None:
                return False
            if codigo in self._usados:
                log.info("Código repetido ignorado.")
                return False
            self._codigo = codigo
            self._usados.add(codigo)
        self._chegou.set()
        return True

    def esperar(self, timeout_s: float) -> str | None:
        if not self._chegou.wait(timeout_s):
            with self._trava:
                self._aguardando_desde = None
            return None
        with self._trava:
            codigo, self._codigo = self._codigo, None
            self._aguardando_desde = None
        self._chegou.clear()
        return codigo

    def desistir(self) -> None:
        with self._trava:
            self._aguardando_desde = None
            self._codigo = None
        self._chegou.clear()
