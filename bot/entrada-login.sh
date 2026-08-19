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

# O login roda em laco: se a pessoa fechar a janela ou o Facebook derrubar o
# navegador no meio, ela reabre sozinha em vez de deixar uma tela preta.
while true; do
  echo "[estacao] abrindo a janela de login do Facebook"
  python tools/facebook_login.py "${DATA_DIR}/fb_state.json" --espera 1800 || true
  echo "[estacao] a janela fechou. Reabrindo em 10s (Ctrl-C do lado de fora para parar)."
  sleep 10
done
