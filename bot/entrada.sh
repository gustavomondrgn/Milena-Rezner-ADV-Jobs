#!/bin/sh
# Entrada do container do bot. Dois modos, uma imagem só.
#
# Por que os dois modos moram na mesma imagem: a estação de login precisa de um
# Chromium, e o bot já tem um. Construir uma segunda imagem só para isso custou
# ~1,5 GB e um Chromium baixado de novo a cada deploy — o suficiente para
# derrubar uma VPS de 4 GB junto com o painel e o Coolify. Aqui a troca de modo
# é uma variável de ambiente e um redeploy sem rebuild.
set -e

if [ "${MODO_LOGIN}" != "1" ]; then
  exec python -u main.py
fi

# --- modo login: um navegador COM TELA, alcançável pelo browser --------------
#
# Existe porque a sessão do Facebook não viaja: ela precisa nascer no servidor,
# no mesmo perfil de navegador que o bot vai usar depois. Como servidor não tem
# tela, criamos uma (Xvfb), publicamos (x11vnc) e entregamos por HTTP (noVNC).
if [ -z "$VNC_PASSWORD" ]; then
  echo "MODO_LOGIN=1 mas VNC_PASSWORD esta vazia."
  echo "Um navegador logado no Facebook, exposto sem senha, e pior do que nao"
  echo "ter estacao nenhuma. Cadastre VNC_PASSWORD e redeploye."
  exit 1
fi

PERFIL="${FACEBOOK_PROFILE_DIR:-${DATA_DIR}/perfil-chrome}"
export DISPLAY=:99

echo "[estacao] tela virtual em ${DISPLAY}"
Xvfb "${DISPLAY}" -screen 0 1366x900x24 -nolisten tcp &
sleep 2

mkdir -p /root/.vnc
x11vnc -storepasswd "$VNC_PASSWORD" /root/.vnc/passwd >/dev/null 2>&1
x11vnc -display "${DISPLAY}" -rfbauth /root/.vnc/passwd -forever -shared \
       -rfbport 5900 -noxdamage -quiet &
sleep 1

echo "[estacao] noVNC em :8080"
websockify --web=/usr/share/novnc 8080 localhost:5900 &
sleep 1

# UMA janela, não um laço. A primeira versão reabria o login assim que ele
# terminava — e para quem estava do outro lado, depois de vencer CAPTCHA e dois
# fatores, a tela ficava preta e um login novo começava do zero. Parecia que
# tinha deslogado; era o oposto.
echo "[estacao] perfil do navegador: ${PERFIL}"
if python tools/facebook_login.py "${DATA_DIR}/fb_state.json" \
     --perfil "${PERFIL}" --espera 1800; then
  echo "[estacao] PRONTO. Perfil e sessao gravados no volume."
  echo "[estacao] Agora e so tirar MODO_LOGIN e a VNC_PASSWORD e redeployar."
else
  echo "[estacao] o login NAO foi concluido — nada foi gravado."
fi

# Segura o container de pé para a mensagem acima poder ser lida na tela.
sleep 900
