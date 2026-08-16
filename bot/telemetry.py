"""Contadores do dia — o que alimenta o relatório das 22h e o /status.

Dois defeitos do desenho anterior nasciam aqui, e os dois faziam o relatório
mentir para baixo:

1. **O "dia" era o dia UTC.** Meia-noite em UTC é 21h em Brasília, então os
   contadores zeravam às 21h e o relatório das 22h contava só a última hora. Era
   isso que fazia "várias vagas" virarem "5". Agora o dia é o dia **local**, o
   mesmo que aparece no cabeçalho do relatório.

2. **Os contadores só existiam em memória.** Todo redeploy — e há um a cada push
   — zerava o dia em silêncio. Agora ficam num arquivo no volume.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

log = logging.getLogger("adv-jobs-bot.telemetry")

CHAVES = (
    "classified",    # passaram pelo classificador de IA
    "rate_limited",  # tentativas barradas pela cota do Gemini
    "adiada",        # classificador falhou: NÃO publicado, volta no próximo ciclo
    "prefiltered",   # cortados por pré-filtro de texto, de graça
    "deduped",       # mesmo post já visto em outro grupo
    "queued",        # aprovados e postos na fila de envio
    "sent",          # efetivamente publicados no grupo
    "skipped",       # reprovados pelo classificador
    "divulgacao",    # advogado se anunciando — o ruído dominante do Facebook
    "local",         # exigem presença numa UF que ela não atende
    "ingles",        # descartados por estarem escritos em inglês
    "categoria",     # descartados: área desligada no painel para a fonte
    "expirada",      # saíram da fila sem serem enviados (validade/lotação)
    "falhou",        # o Telegram recusou a mensagem em definitivo
    "encerrada",     # demanda saiu do ar na fonte depois de publicada
)


class DailyStats:
    """Contadores do dia corrente, persistidos e virando no fuso local."""

    def __init__(self, path: Path, historico: Path | None = None) -> None:
        self.path = path
        self.historico = historico
        self._lock = threading.Lock()
        self._dia: str = ""
        self._totais: dict[str, int] = {}
        self._por_fonte: dict[str, dict[str, int]] = {}
        self._cota_diaria_anunciada = False
        self._relatorio_enviado = False
        # Alertas de operação já disparados hoje. Só em memória de propósito: um
        # redeploy é justamente quando vale reavisar que algo continua quebrado.
        self._alertados: set[str] = set()
        self._load()

    # -- persistência -------------------------------------------------------

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            with self.path.open("r", encoding="utf-8") as f:
                dados = json.load(f)
        except (json.JSONDecodeError, ValueError, OSError) as exc:
            log.warning("Falha lendo %s: %s — começando zerado", self.path, exc)
            return
        self._dia = str(dados.get("dia") or "")
        self._totais = {k: int(v) for k, v in (dados.get("totais") or {}).items()}
        self._por_fonte = {
            fonte: {k: int(v) for k, v in contadores.items()}
            for fonte, contadores in (dados.get("por_fonte") or {}).items()
        }
        self._relatorio_enviado = bool(dados.get("relatorio_enviado"))
        if self._totais:
            log.info("Contadores do dia %s recuperados do disco: %d enviada(s)",
                     self._dia, self._totais.get("sent", 0))

    def _save_locked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        try:
            with tmp.open("w", encoding="utf-8") as f:
                json.dump({
                    "dia": self._dia,
                    "totais": self._totais,
                    "por_fonte": self._por_fonte,
                    "relatorio_enviado": self._relatorio_enviado,
                }, f, ensure_ascii=False)
            tmp.replace(self.path)
        except OSError as exc:
            log.error("Falha salvando %s: %s", self.path, exc)

    def _arquivar_locked(self) -> None:
        """Fecha o dia anterior no histórico antes de zerar."""
        if not self.historico or not self._dia or not self._totais:
            return
        try:
            self.historico.parent.mkdir(parents=True, exist_ok=True)
            with self.historico.open("a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "day": self._dia,
                    **self._totais,
                    "por_fonte": self._por_fonte,
                }, ensure_ascii=False) + "\n")
        except OSError as exc:
            log.error("Falha arquivando o dia %s: %s", self._dia, exc)

    # -- ciclo do dia -------------------------------------------------------

    def _virar_locked(self, hoje: str) -> None:
        if self._dia == hoje:
            return
        if self._dia:
            self._arquivar_locked()
        self._dia = hoje
        self._totais = {k: 0 for k in CHAVES}
        self._por_fonte = {}
        self._cota_diaria_anunciada = False
        self._relatorio_enviado = False
        self._alertados = set()
        self._save_locked()

    def virar_se_preciso(self, hoje: str) -> None:
        with self._lock:
            self._virar_locked(hoje)

    # -- escrita ------------------------------------------------------------

    def bump(self, hoje: str, chave: str, fonte: str | None = None, n: int = 1) -> None:
        with self._lock:
            self._virar_locked(hoje)
            self._totais[chave] = self._totais.get(chave, 0) + n
            if fonte:
                por_fonte = self._por_fonte.setdefault(fonte, {})
                por_fonte[chave] = por_fonte.get(chave, 0) + n
            self._save_locked()

    def relatorio_pendente(self, hoje: str) -> bool:
        """True se o relatório de hoje ainda não saiu.

        Fica junto dos contadores porque tem exatamente o mesmo ciclo de vida —
        e porque precisa sobreviver a restart: sem isso, um redeploy às 22h30
        mandaria o relatório de novo.
        """
        with self._lock:
            self._virar_locked(hoje)
            return not self._relatorio_enviado

    def marcar_relatorio_enviado(self, hoje: str) -> None:
        with self._lock:
            self._virar_locked(hoje)
            self._relatorio_enviado = True
            self._save_locked()

    def marcar_cota_anunciada(self) -> bool:
        """True na primeira vez do dia — para não repetir o alerta a cada vaga."""
        with self._lock:
            if self._cota_diaria_anunciada:
                return False
            self._cota_diaria_anunciada = True
            return True

    def marcar_alerta(self, chave: str, hoje: str) -> bool:
        """True só na primeira vez do dia para esta chave.

        Uma falha que se repete a cada ciclo geraria uma mensagem a cada dez
        minutos no privado de quem mantém. Alerta que satura é alerta que passa
        a ser ignorado — e aí a próxima falha de verdade também passa batida.
        """
        with self._lock:
            self._virar_locked(hoje)
            if chave in self._alertados:
                return False
            self._alertados.add(chave)
            return True

    # -- leitura ------------------------------------------------------------

    @property
    def dia(self) -> str:
        with self._lock:
            return self._dia

    def total(self, chave: str) -> int:
        with self._lock:
            return self._totais.get(chave, 0)

    def totais(self) -> dict[str, int]:
        with self._lock:
            return dict(self._totais)

    def da_fonte(self, fonte: str) -> dict[str, int]:
        with self._lock:
            return dict(self._por_fonte.get(fonte, {}))

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "dia": self._dia,
                "totais": dict(self._totais),
                "por_fonte": {f: dict(c) for f, c in self._por_fonte.items()},
            }
