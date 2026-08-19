# Estação de login: um navegador COM TELA rodando dentro do servidor, que se
# alcança pelo browser (noVNC).
#
# Por que isto existe, e por que não é gambiarra:
#
# A sessão do Facebook não viaja de IP. Criada na máquina de casa e apresentada
# de um datacenter, ela vira "aparelho novo em lugar novo": o Facebook mostra a
# tela "Continuar como Fulano" e, se alguém insistir com login novo dali, o
# CAPTCHA da Arkose — que script nenhum resolve, por desenho.
#
# Medido em 19/08/2026: a mesma sessão lia o grupo da máquina local no mesmo
# minuto em que o servidor recebia a tela de confirmação. O clique em
# "Continuar" foi tentado por JavaScript e por clique real no `role=button`;
# nenhum dos dois navega em navegador sem tela.
#
# A saída é a sessão NASCER aqui. Como não há tela num servidor, esta imagem
# cria uma: Xvfb desenha, x11vnc publica, noVNC entrega no browser. A pessoa
# entra pelo endereço, loga na mão — resolvendo CAPTCHA e dois fatores com o
# mouse — e o `facebook_login.py` grava a sessão no MESMO volume que o bot lê.
#
# Sobe só quando precisa. Ficar no ar de graça é um navegador logado exposto na
# internet atrás de uma senha, o que não se deixa aceso sem motivo.

FROM python:3.12-slim

WORKDIR /app

COPY bot/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

ENV PLAYWRIGHT_BROWSERS_PATH=0
RUN playwright install --with-deps chromium && rm -rf /var/lib/apt/lists/*

# Xvfb = a tela que não existe no servidor. x11vnc publica essa tela; novoVNC e
# websockify a entregam por HTTP, para o acesso ser um endereço no navegador em
# vez de um cliente de VNC instalado.
RUN apt-get update && apt-get install -y --no-install-recommends \
        xvfb x11vnc novnc websockify \
    && rm -rf /var/lib/apt/lists/*

COPY bot/*.py ./
COPY bot/tools/ ./tools/
COPY bot/config/ ./config/

ENV DATA_DIR=/app/data
ENV DISPLAY=:99

COPY bot/entrada-login.sh /entrada-login.sh
RUN chmod +x /entrada-login.sh

EXPOSE 8080
CMD ["/entrada-login.sh"]
