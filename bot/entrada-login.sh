#!/bin/sh
# Sobe a tela virtual, publica por VNC/noVNC e abre a janela de login.
#
# A ordem importa: sem o Xvfb de pé, o Chromium morre em "no display"; sem o
# x11vnc, a tela existe e ninguém vê; sem o websockify, é preciso um cliente de
# VNC instalado, e a ideia toda é a pessoa só abrir um endereço.
set -e

if [ -z "$VNC_PASSWORD" ]; then
  echo "VNC_PASSWORD nao definida — esta estacao NAO sobe sem senha."
  echo "Um navegador logado no Facebook, aberto na internet sem senha, e pior"
  echo "do que nao ter estacao nenhuma."
  exit 1
fi

echo "[estacao] tela virtual em ${DISPLAY}"
Xvfb "${DISPLAY}" -screen 0 1366x900x24 -nolisten tcp &
sleep 2

mkdir -p /root/.vnc
x11vnc -storepasswd "$VNC_PASSWORD" /root/.vnc/passwd >/dev/null 2>&1

echo "[estacao] publicando a tela"
x11vnc -display "${DISPLAY}" -rfbauth /root/.vnc/passwd -forever -shared \
       -rfbport 5900 -noxdamage -quiet &
sleep 1

echo "[estacao] noVNC em :8080"
websockify --web=/usr/share/novnc 8080 localhost:5900 &
sleep 1

# UMA janela, nao um laco.
#
# A primeira versao reabria a janela quando o login terminava — e o efeito, para
# quem estava do outro lado, foi cruel: a pessoa venceu CAPTCHA e dois fatores,
# o Facebook abriu, a tela ficou preta por dois segundos e um login NOVO
# comecou do zero. Parecia que tinha deslogado. Era o oposto: a ferramenta so
# fecha DEPOIS de confirmar a sessao e grava-la.
echo "[estacao] abrindo a janela de login do Facebook"
if python tools/facebook_login.py "${DATA_DIR}/fb_state.json" --espera 1800; then
  echo "[estacao] SESSAO GRAVADA em ${DATA_DIR}/fb_state.json."
  echo "[estacao] Pode fechar esta aba. A estacao fica parada de proposito:"
  echo "[estacao] navegador logado exposto na internet nao se deixa aceso."
else
  echo "[estacao] o login NAO foi concluido — nada foi gravado."
  echo "[estacao] Redeploye a estacao para tentar de novo."
fi

# Segura o processo para a tela continuar acessivel por alguns minutos (dá
# tempo de ler a mensagem acima no terminal da janela), e entao sai.
sleep 600
