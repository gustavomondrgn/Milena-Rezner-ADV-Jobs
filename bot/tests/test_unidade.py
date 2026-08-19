"""Testes das partes que este projeto tem de novo e que podem quebrar calado.

O foco não é cobertura: é cobrir o que, se estiver errado, **não dá sintoma**.
Três coisas se encaixam nisso:

1. `parece_divulgacao` — se apertar demais, mata demanda boa e ninguém fica
   sabendo, porque não existe log de "oportunidade que eu deixei passar".
2. A limpeza do texto do Facebook — se sobrar rodapé, o classificador julga
   pelo lixo; se cortar demais, ele julga por meio post.
3. `local_aceito` — a regra de UF é a única que descarta por um dado
   *inferido* (a comarca), então é a que mais pode errar em silêncio.

Rodar: `python bot/tests/test_unidade.py`
"""
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Precisa vir ANTES de importar main: no import ele já instancia os arquivos de
# estado a partir de DATA_DIR, e sem isto o teste sujaria a pasta do projeto.
_TMP = tempfile.mkdtemp(prefix="advjobs-teste-")
os.environ["DATA_DIR"] = _TMP

import facebook  # noqa: E402
import filters  # noqa: E402
import main  # noqa: E402
from dispatch import SendQueue  # noqa: E402

BRT = timezone(timedelta(hours=-3))
falhas = []


def check(nome, got, esperado):
    ok = got == esperado
    print(f"{'  OK ' if ok else 'FALHA'} | {nome}: got={got!r} esperado={esperado!r}")
    if not ok:
        falhas.append(nome)


def secao(titulo):
    print()
    print("=" * 72)
    print(titulo)
    print("=" * 72)


# ---------------------------------------------------------------------------
secao("DIVULGAÇÃO — o filtro que sustenta o produto")
# ---------------------------------------------------------------------------

ANUNCIO_1 = """Bom dia, colegas! Sou advogado atuante em Direito Imobiliario ha
12 anos, atuo em todo o Brasil e aceito parcerias. Tabela de honorarios no
privado. OAB/PR 45.678"""

ANUNCIO_2 = """ESCRITORIO ESPECIALIZADO em usucapiao e regularizacao de imoveis.
Faco peticoes, contestacoes e recursos com agilidade. Orcamento sem compromisso,
agende sua consulta."""

ANUNCIO_3 = """Somos um escritorio com 15 anos de experiencia em direito
empresarial. Atendimento humanizado e personalizado para sua empresa."""

check("anúncio com 'atuo em todo o Brasil'", filters.parece_divulgacao(ANUNCIO_1), True)
check("anúncio com 'faço petições'", filters.parece_divulgacao(ANUNCIO_2), True)
check("anúncio por acúmulo de sinais fracos", filters.parece_divulgacao(ANUNCIO_3), True)

DEMANDA_1 = """Pessoal, preciso de um advogado pra entrar com usucapiao de um
terreno aqui em Santos. Meu pai mora la ha 22 anos e nunca regularizou nada.
Alguem indica?"""

DEMANDA_2 = """Procuro colega para audiencia na 2a Vara Civel de Londrina dia
12/09 as 14h. Pago R$ 400 pelo ato."""

DEMANDA_3 = """Meu inquilino esta ha 5 meses sem pagar o aluguel e nao quer sair.
Alguem atua com despejo aqui na regiao?"""

check("demanda de cliente", filters.parece_divulgacao(DEMANDA_1), False)
check("demanda de correspondente", filters.parece_divulgacao(DEMANDA_2), False)
check("demanda de despejo", filters.parece_divulgacao(DEMANDA_3), False)

# O caso híbrido é o que mais importa: advogado que TAMBÉM está pedindo. Se o
# pré-filtro derrubar isso, some a fonte inteira de parceria — que é metade do
# valor do produto.
HIBRIDO = """Sou advogada em Curitiba e peguei uma demanda de adjudicacao
compulsoria que nao vou conseguir tocar por conflito de agenda. Alguem tem
interesse? Divido honorarios."""

check("HÍBRIDO: advogado que está pedindo", filters.parece_divulgacao(HIBRIDO), False)
check("texto vazio", filters.parece_divulgacao(""), False)

# Anúncio real colhido em 16/08/2026 no grupo "Advogados - Tire suas dúvidas".
# Passou batido porque o padrão exigia "atendemos EM todo o Brasil", e ele está
# escrito sem o "em" — o tipo de detalhe que só um post de verdade mostra.
ANUNCIO_REAL = """ESTA GRAVIDA E DESEMPREGADA? Voce sabia que pode ter direito ao
salario-maternidade? Nos analisamos o seu caso e orientamos sobre as
contribuicoes necessarias. Atendemos todo o Brasil. Chame no WhatsApp."""
check("anúncio com 'Atendemos todo o Brasil' (sem o 'em')",
      filters.parece_divulgacao(ANUNCIO_REAL), True)

# ---------------------------------------------------------------------------
secao("FACEBOOK — limpeza do texto do card")
# ---------------------------------------------------------------------------

CARD = """Maria Silva
2 h
Preciso de um advogado para entrar com usucapiao de um terreno em Santos.
Meu pai mora la ha 22 anos e nunca regularizou nada.
Todas as reações:
14
Curtir
Comentar
Compartilhar
João Pereira
Tenho interesse, chamei no pv"""

limpo = facebook.limpar_texto(CARD, "Maria Silva")
check("tira o nome do autor", limpo.startswith("Preciso de um advogado"), True)
check("tira o rodapé de reações", "Todas as reações" in limpo, False)
check("não vaza comentário de terceiro", "João Pereira" in limpo, False)
check("mantém as duas linhas do post", limpo.count("\n"), 1)

CARD_SEM_AUTOR = """Advogados de Campinas e Região
Ontem às 14:32
···
Alguem atua com acao renovatoria de locacao comercial?
Curtir
Comentar"""
limpo2 = facebook.limpar_texto(CARD_SEM_AUTOR, "Advogados de Campinas e Região")
check("tira cabeçalho com data e '···'",
      limpo2, "Alguem atua com acao renovatoria de locacao comercial?")

# Os três casos abaixo saíram de posts REAIS, colhidos em 16/08/2026 no grupo
# "Preciso de um Advogado". Cada um passava despercebido: nenhum quebra nada,
# todos entregam texto errado ao classificador.

# 1) Depois que o bot clica em "Ver mais", o botão vira "Ver menos" — e o
#    Facebook o deixa DENTRO do bloco de texto do post.
check("tira 'Ver menos' colado no fim",
      facebook.limpar_sobra_de_botao("Entre em contato para mais informações. Ver menos"),
      "Entre em contato para mais informações.")
check("tira '… Ver mais' de post truncado",
      facebook.limpar_sobra_de_botao("Meu inquilino não paga há 5 meses e… Ver mais"),
      "Meu inquilino não paga há 5 meses e")
check("não come texto que só TERMINA parecido",
      facebook.limpar_sobra_de_botao("O contrato dizia que eu poderia ver mais tarde"),
      "O contrato dizia que eu poderia ver mais tarde")

# 2) Card com imagem vem salpicado de "Facebook", que é o nome acessível dos
#    links de mídia. Num post só de foto isso passava dos 40 caracteres mínimos
#    e um post sem texto nenhum chegava ao classificador parecendo ter conteúdo.
CARD_SO_FOTO = """Facebook
Facebook
Facebook
Mari Beatriz
·
Facebook
Comente como Gustavo
Facebook"""
check("post só de foto não vira texto de mentira",
      len(facebook.limpar_texto(CARD_SO_FOTO, "Mari Beatriz")) < 40, True)

CARD_COM_FOTO_E_TEXTO = """Facebook
Facebook
Carlos Goes
2 h
Facebook
Meu vizinho construiu em cima da minha divisa e nao quer desfazer.
Facebook
Comente como Gustavo"""
check("mantém o texto do post que tem foto E texto",
      facebook.limpar_texto(CARD_COM_FOTO_E_TEXTO, "Carlos Goes"),
      "Meu vizinho construiu em cima da minha divisa e nao quer desfazer.")

# ---------------------------------------------------------------------------
secao("FACEBOOK — data relativa vira ISO ordenável")
# ---------------------------------------------------------------------------

AGORA = datetime(2026, 8, 15, 12, 0, 0, tzinfo=BRT)

check("'35 min'", facebook.parse_tempo("35 min", AGORA)[:16], "2026-08-15T11:25")
check("'2 h'", facebook.parse_tempo("2 h", AGORA)[:16], "2026-08-15T10:00")
check("'3 d'", facebook.parse_tempo("3 d", AGORA)[:16], "2026-08-12T12:00")
check("'Ontem às 14:32'", facebook.parse_tempo("Ontem às 14:32", AGORA)[:10], "2026-08-14")
check("rótulo ininteligível cai no agora",
      facebook.parse_tempo("sei lá quando", AGORA)[:16], "2026-08-15T12:00")

# O ponto de existir esta função: ordenação por texto tem que bater com o tempo.
mais_novo = facebook.parse_tempo("9 min", AGORA)
mais_velho = facebook.parse_tempo("10 h", AGORA)
check("ISO ordena certo (9 min > 10 h)", mais_novo > mais_velho, True)

# ---------------------------------------------------------------------------
secao("FACEBOOK — título derivado do post")
# ---------------------------------------------------------------------------

check("corta na primeira frase",
      facebook.titulo_do_post("Preciso de advogado. Meu caso e o seguinte..."),
      "Preciso de advogado.")
check("post vazio", facebook.titulo_do_post(""), "(post sem texto)")
longo = facebook.titulo_do_post("palavra " * 40)
check("título longo é truncado", len(longo) <= 91, True)

# ---------------------------------------------------------------------------
secao("FACEBOOK — lista de grupos")
# ---------------------------------------------------------------------------

arq = Path(_TMP) / "grupos.txt"
arq.write_text("""# comentário
https://www.facebook.com/groups/123456789 | Advogados SP | SP
advogadoscascavel | Advogados Cascavel | pr

https://www.facebook.com/groups/123456789 | duplicado, deve sumir | SP
987654321
""", encoding="utf-8")

grupos = facebook.carregar_grupos(arq)
check("quantidade (duplicado removido)", len(grupos), 3)
check("extrai slug da URL", grupos[0].slug, "123456789")
check("UF normalizada para maiúscula", grupos[1].uf, "PR")
check("aceita slug puro", grupos[1].slug, "advogadoscascavel")
check("aceita id sem rótulo", grupos[2].slug, "987654321")
check("URL é cronológica",
      "sorting_setting=CHRONOLOGICAL" in grupos[0].url, True)
check("arquivo inexistente devolve vazio",
      facebook.carregar_grupos(Path(_TMP) / "nao-existe.txt"), [])

# ---------------------------------------------------------------------------
secao("POST REMOVIDO — o detector que autoriza apagar mensagem do grupo")
# ---------------------------------------------------------------------------

# Falso positivo aqui não custa uma vaga: custa APAGAR uma demanda boa do grupo
# do cliente. Por isso o padrão é literal e curto, e tudo que não casar
# explicitamente vira "desconhecida" lá em `_estado_do_post`.


def sumiu(texto):
    return bool(facebook._POST_SUMIU.search(facebook._sem_acento(texto)))


check("PT: conteúdo indisponível",
      sumiu("Este conteúdo não está disponível no momento"), True)
check("PT: página indisponível",
      sumiu("Esta página não está disponível"), True)
check("PT: link quebrado",
      sumiu("O link que você seguiu pode estar quebrado"), True)
check("EN: content isn't available",
      sumiu("This content isn't available right now"), True)
check("EN: content isnt available (sem apóstrofo)",
      sumiu("This content isnt available right now"), True)

# Os casos que NÃO podem disparar. Cada um destes é uma mensagem que seria
# apagada por engano do grupo da cliente.
check("post normal não dispara",
      sumiu("Preciso de advogado para usucapiao em Santos, alguem indica?"), False)
check("vídeo indisponível DENTRO de post no ar",
      sumiu("Olha esse caso absurdo\nVídeo indisponível\nPreciso de um parecer"),
      False)
check("a palavra 'indisponível' sozinha não basta",
      sumiu("O imóvel está indisponível para visita até segunda"), False)
check("'não encontrado' fora de contexto",
      sumiu("O processo não encontrado no PJe, alguém sabe o que houve?"), False)
check("texto vazio", sumiu(""), False)

# -- Cadência da confirmação ------------------------------------------------
# A detecção estava certa e mesmo assim a mensagem ficava ~24h no ar: quem
# segurava era a SEGUNDA checagem, que usava o intervalo normal. O post suspeito
# tem que voltar para a fila em uma hora; o saudável, não.

import publicadas  # noqa: E402


def _registro_com(faltas, checada_ha_horas, agora):
    reg = publicadas.RegistroPublicadas(Path(_TMP) / f"pub-{faltas}-{checada_ha_horas}.json")
    reg.registrar(uid="facebook:1", source="facebook", source_id="1",
                  title="t", message_id=9, agora=agora, url="u")
    reg._itens["facebook:1"]["faltas"] = faltas
    reg._itens["facebook:1"]["checada_em"] = (
        agora - timedelta(hours=checada_ha_horas)).isoformat()
    return reg


AGORA_P = datetime(2026, 8, 16, 20, 0, 0, tzinfo=BRT)

check("suspeito volta à fila depois de 1h",
      len(_registro_com(1, 2, AGORA_P).a_checar(
          agora=AGORA_P, intervalo_horas=24, limite=8)), 1)
check("suspeito NÃO volta antes de 1h",
      len(_registro_com(1, 0.5, AGORA_P).a_checar(
          agora=AGORA_P, intervalo_horas=24, limite=8)), 0)
check("saudável continua esperando as 24h",
      len(_registro_com(0, 2, AGORA_P).a_checar(
          agora=AGORA_P, intervalo_horas=24, limite=8)), 0)
check("saudável volta depois das 24h",
      len(_registro_com(0, 25, AGORA_P).a_checar(
          agora=AGORA_P, intervalo_horas=24, limite=8)), 1)

# O teto por ciclo é baixo; se o suspeito ficar atrás de posts saudáveis na
# ordenação, ele não é checado e a mensagem errada continua no grupo.
_reg = publicadas.RegistroPublicadas(Path(_TMP) / "pub-ordem.json")
for i in range(5):
    _reg.registrar(uid=f"facebook:{i}", source="facebook", source_id=str(i),
                   title="t", message_id=i, agora=AGORA_P, url="u")
    _reg._itens[f"facebook:{i}"]["checada_em"] = (
        AGORA_P - timedelta(hours=48)).isoformat()
_reg._itens["facebook:4"]["faltas"] = 1
check("suspeito passa na frente quando o teto é 1",
      _reg.a_checar(agora=AGORA_P, intervalo_horas=24, limite=1)[0]["uid"],
      "facebook:4")

# ---------------------------------------------------------------------------
secao("REGRA DE LOCAL — só morde quando exige presença")
# ---------------------------------------------------------------------------

CFG = {
    "ufs_atendidas": ["SP", "PR", "SC"],
    "aceitar_sem_local": True,
    "sources": {},
}

check("trabalho digital em UF não atendida passa",
      main.local_aceito(CFG, "facebook", "AM", False)[0], True)
check("presença em UF atendida passa",
      main.local_aceito(CFG, "facebook", "SP", True)[0], True)
check("presença em UF NÃO atendida cai",
      main.local_aceito(CFG, "facebook", "AM", True)[0], False)
check("presença sem local declarado passa (padrão)",
      main.local_aceito(CFG, "facebook", "", True)[0], True)

CFG_ESTRITO = dict(CFG, aceitar_sem_local=False)
check("presença sem local cai quando configurado assim",
      main.local_aceito(CFG_ESTRITO, "facebook", "", True)[0], False)

CFG_SEM_UF = dict(CFG, ufs_atendidas=[])
check("lista de UFs vazia aceita tudo",
      main.local_aceito(CFG_SEM_UF, "facebook", "AM", True)[0], True)

# ---------------------------------------------------------------------------
secao("FILA — teto zero significa SEM TETO")
# ---------------------------------------------------------------------------

fila = SendQueue(Path(_TMP) / "fila.json", validade_dias=3)
agora = datetime(2026, 8, 15, 10, 0, 0, tzinfo=BRT)
for i in range(5):
    fila.push(uid=f"facebook:{i}", source="facebook", title=f"post {i}",
              html="<b>x</b>", score=50 + i, category="relevant",
              published_at="2026-08-15T09:00:00", agora=agora)

saiu = []
for _ in range(6):
    item = fila.proxima(agora=agora, hoje="2026-08-15", limite_diario=0,
                        janela_inicio=6, janela_fim=23)
    if item is None:
        break
    saiu.append(item["score"])
    fila.confirmar_envio(agora, "2026-08-15")

check("sem teto: esvazia a fila num tick só", len(saiu), 5)
check("sem teto: maior nota primeiro", saiu, [54, 53, 52, 51, 50])

fila2 = SendQueue(Path(_TMP) / "fila2.json", validade_dias=3)
for i in range(5):
    fila2.push(uid=f"facebook:b{i}", source="facebook", title=f"post {i}",
               html="<b>x</b>", score=50 + i, category="relevant",
               published_at="2026-08-15T09:00:00", agora=agora)
primeiro = fila2.proxima(agora=agora, hoje="2026-08-15", limite_diario=2,
                         janela_inicio=6, janela_fim=23)
fila2.confirmar_envio(agora, "2026-08-15")
segundo = fila2.proxima(agora=agora, hoje="2026-08-15", limite_diario=2,
                        janela_inicio=6, janela_fim=23)
check("com teto: o espaçamento segura o segundo envio", segundo, None)
check("com teto: o primeiro saiu mesmo assim", primeiro is not None, True)

fora = fila.proxima(agora=agora.replace(hour=3), hoje="2026-08-15",
                    limite_diario=0, janela_inicio=6, janela_fim=23)
check("janela de horário vale mesmo sem teto", fora, None)

# ---------------------------------------------------------------------------
secao("IDIOMA — herdado, mas ainda em uso")
# ---------------------------------------------------------------------------

EN = """We are looking for a real estate attorney to join our team. You will be
responsible for reviewing contracts, handling closings and supporting the legal
department with due diligence tasks. The ideal candidate has excellent written
communication skills and is comfortable working under pressure every day."""

check("inglês detectado", filters.parece_ingles("Real Estate Attorney", EN), True)
check("português não é confundido",
      filters.parece_ingles("Advogado", DEMANDA_1 + " " + DEMANDA_3), False)

# ---------------------------------------------------------------------------
secao("RELATÓRIO DIÁRIO — a peça do ciclo que nunca tinha rodado")
# ---------------------------------------------------------------------------
# É a única parte do laço que só acontece uma vez por dia, às 22h: nenhum teste
# de ciclo passava por ela, e um erro aqui não dá sintoma nenhum até a noite —
# quando o cliente simplesmente não recebe nada e ninguém fica sabendo.

import telemetry  # noqa: E402

_ENVIADOS: list[tuple[str, str]] = []
_send_real = main.send_telegram


def _send_fake(text, chat_id=None, **kwargs):
    _ENVIADOS.append((str(chat_id), text))
    return {"result": {"message_id": len(_ENVIADOS)}}


def _send_quebrado(text, chat_id=None, **kwargs):
    raise main.requests.RequestException("timeout simulado")


main.send_telegram = _send_fake
_HOJE = main.hoje_local()
main.STATS.virar_se_preciso(_HOJE)

# --- dia sem nada ----------------------------------------------------------
vazio = main.montar_relatorio([], {"daily_limit": 0})
check("dia sem demanda avisa em vez de mandar zero",
      "Nenhuma demanda nova hoje" in vazio, True)
check("dia sem demanda não inventa quebra por área", "Por área" in vazio, False)
check("dia sem demanda não mostra aviso de adiadas", "⚠️" in vazio, False)

# --- dia com demanda, sem teto (o padrão deste projeto) --------------------
for _ in range(9):
    main.STATS.bump(_HOJE, "sent", fonte="facebook")
for _chave, _n in (("area:imobiliario", 6), ("area:empresarial", 2),
                   ("area:condominio", 1)):
    main.STATS.bump(_HOJE, _chave, n=_n)

sem_teto = main.montar_relatorio([], {"daily_limit": 0})
check("sem teto: conta o enviado e não cita cota",
      "✅ <b>9</b> demanda(s) enviadas ao grupo" in sem_teto, True)
check("sem teto: não escreve 'de 0'", "de 0 demanda" in sem_teto, False)
check("quebra por área aparece", "<b>Por área</b>" in sem_teto, True)
check("só as áreas com demanda entram",
      "Trabalhista (empresa)" in sem_teto, False)
check("área com mais demanda vem primeiro",
      sem_teto.index("Imobiliário") < sem_teto.index("Empresarial")
      < sem_teto.index("Condomínio"), True)
check("o número de cada área bate", "• Imobiliário: 6" in sem_teto, True)

# --- com teto, que é o que acontece se ela ligar cota no painel ------------
com_teto = main.montar_relatorio([], {"daily_limit": 12})
check("com teto: mostra o enviado sobre o total",
      "✅ <b>9</b> de 12 demanda(s) enviadas ao grupo" in com_teto, True)

# --- adiada: o aviso que só pode aparecer quando existe --------------------
main.STATS.bump(_HOJE, "adiada", n=3)
com_adiada = main.montar_relatorio([], {"daily_limit": 0})
check("adiada > 0 dispara o aviso de classificador fora do ar",
      "3 post(s) não puderam ser analisados hoje" in com_adiada, True)

# --- título do cliente passa por escape ------------------------------------
escapado = main.montar_relatorio([], {"daily_limit": 0}, titulo="Fechamento & cia")
check("título vai escapado pro HTML do Telegram",
      "Fechamento &amp; cia" in escapado, True)

# --- entrega -------------------------------------------------------------
_ENVIADOS.clear()
check("relatório entregue devolve True",
      main.enviar_relatorio([], {"daily_limit": 0}), True)
check("foi para um destino só", len(_ENVIADOS), 1)
check("o destino é o grupo", _ENVIADOS[0][0], str(main.CHAT_ID_ATUAL))

# Telegram fora do ar não pode consumir o relatório do dia: o laço só marca
# como enviado quando algum destino confirmou.
main.send_telegram = _send_quebrado
check("falha de rede devolve False em vez de sumir com o relatório",
      main.enviar_relatorio([], {"daily_limit": 0}), False)
main.send_telegram = _send_fake

# --- REPORT_TO=privado ------------------------------------------------------
_report_to, _report_ids = main.REPORT_TO, main.REPORT_CHAT_IDS
main.REPORT_TO = "privado"
main.REPORT_CHAT_IDS = []
_ENVIADOS.clear()
main.enviar_relatorio([], {"daily_limit": 0})
check("privado sem destino cadastrado cai no grupo, não some",
      [destino for destino, _ in _ENVIADOS], [str(main.CHAT_ID_ATUAL)])

main.REPORT_CHAT_IDS = ["6283084782"]
_ENVIADOS.clear()
main.enviar_relatorio([], {"daily_limit": 0})
check("privado manda pro admin, não pro grupo",
      [destino for destino, _ in _ENVIADOS], ["6283084782"])
main.REPORT_TO, main.REPORT_CHAT_IDS = _report_to, _report_ids
main.send_telegram = _send_real

# --- não repetir: a trava tem de sobreviver a redeploy ---------------------
check("hora do relatório é 22h por padrão", main.REPORT_HOUR, 22)
check("21h ainda não é hora de relatório", 21 >= main.REPORT_HOUR, False)
check("22h é", 22 >= main.REPORT_HOUR, True)

check("pendente antes de sair", main.STATS.relatorio_pendente(_HOJE), True)
main.STATS.marcar_relatorio_enviado(_HOJE)
check("não sai duas vezes no mesmo dia",
      main.STATS.relatorio_pendente(_HOJE), False)

# Um redeploy às 22h30 recria o DailyStats a partir do disco. Se a marca não
# estivesse persistida, o cliente receberia o relatório de novo a cada deploy.
_apos_redeploy = telemetry.DailyStats(main.STATS.path)
check("redeploy depois das 22h não remanda o relatório",
      _apos_redeploy.relatorio_pendente(_HOJE), False)

_AMANHA = (datetime.strptime(_HOJE, "%Y-%m-%d")
           + timedelta(days=1)).strftime("%Y-%m-%d")
check("dia novo, relatório novo", _apos_redeploy.relatorio_pendente(_AMANHA), True)
check("a virada zera os contadores", _apos_redeploy.totais().get("sent", 0), 0)


# ---------------------------------------------------------------------------
secao("SESSÃO DO FACEBOOK — semeadura pelo ambiente")
# ---------------------------------------------------------------------------
# A sessão chega no servidor por variável de ambiente e o bot a escreve no
# volume no boot. O erro caro aqui é o inverso do óbvio: sobrescrever DEMAIS.
# O Playwright renova os cookies e regrava o arquivo a cada ciclo — se o boot
# devolvesse o valor original toda vez, a conta cairia sozinha em alguns dias,
# e o sintoma seria "o bot parou de achar post", sem erro nenhum.

import base64 as _b64  # noqa: E402

_SESSAO_DIR = Path(tempfile.mkdtemp(prefix="advjobs-sessao-"))
_data_dir_real, _state_real, _b64_real = (
    main.DATA_DIR, main.FACEBOOK_STATE_FILE, main.FACEBOOK_STATE_B64)
main.DATA_DIR = _SESSAO_DIR
main.FACEBOOK_STATE_FILE = _SESSAO_DIR / "fb_state.json"


def _como_env(cookies):
    corpo = json.dumps({"cookies": cookies, "origins": []}).encode()
    return _b64.b64encode(corpo).decode()


ORIGINAL = _como_env([{"name": "c_user", "value": "111"}])
RENOVADA = _como_env([{"name": "c_user", "value": "222"}])

# 1. Sem a variável, nada acontece — é o caso do desenvolvimento local, onde o
#    arquivo já está na pasta.
main.FACEBOOK_STATE_B64 = ""
main.semear_sessao_facebook()
check("sem a variável, não inventa arquivo de sessão",
      main.FACEBOOK_STATE_FILE.exists(), False)

# 2. Primeiro boot no servidor: o volume está vazio e a sessão nasce dele.
main.FACEBOOK_STATE_B64 = ORIGINAL
main.semear_sessao_facebook()
check("primeiro boot escreve a sessão no volume",
      main.FACEBOOK_STATE_FILE.exists(), True)
check("e escreve o conteúdo certo",
      json.loads(main.FACEBOOK_STATE_FILE.read_text(encoding="utf-8"))
      ["cookies"][0]["value"], "111")

# 3. O bot rodou e renovou os cookies. Novo boot, MESMA variável: o arquivo
#    renovado tem de sobreviver. É a regra que este teste existe para proteger.
main.FACEBOOK_STATE_FILE.write_text(
    json.dumps({"cookies": [{"name": "c_user", "value": "renovado"}]}),
    encoding="utf-8")
main.semear_sessao_facebook()
check("redeploy NÃO devolve a sessão velha por cima da renovada",
      json.loads(main.FACEBOOK_STATE_FILE.read_text(encoding="utf-8"))
      ["cookies"][0]["value"], "renovado")

# 4. Alguém colou uma sessão nova na variável: aí sim sobrescreve, porque a
#    troca foi deliberada.
main.FACEBOOK_STATE_B64 = RENOVADA
main.semear_sessao_facebook()
check("variável trocada sobrescreve o volume",
      json.loads(main.FACEBOOK_STATE_FILE.read_text(encoding="utf-8"))
      ["cookies"][0]["value"], "222")

# 5. Valor quebrado não pode destruir a sessão que está funcionando.
main.FACEBOOK_STATE_B64 = "isto não é base64 %%%"
main.semear_sessao_facebook()
check("base64 inválido preserva a sessão do volume",
      json.loads(main.FACEBOOK_STATE_FILE.read_text(encoding="utf-8"))
      ["cookies"][0]["value"], "222")

main.FACEBOOK_STATE_B64 = _b64.b64encode(b'{"origins": []}').decode()
main.semear_sessao_facebook()
check("JSON sem cookies preserva a sessão do volume",
      json.loads(main.FACEBOOK_STATE_FILE.read_text(encoding="utf-8"))
      ["cookies"][0]["value"], "222")

main.DATA_DIR, main.FACEBOOK_STATE_FILE, main.FACEBOOK_STATE_B64 = (
    _data_dir_real, _state_real, _b64_real)


# ---------------------------------------------------------------------------
secao("RENOVAÇÃO DA SESSÃO — código de dois fatores")
# ---------------------------------------------------------------------------
# Quando a sessão cai, o bot refaz o login no próprio servidor e pode precisar
# do código de dois fatores. Duas coisas aqui erram calado: aceitar QUALQUER
# número como código (o Facebook endurece o checkpoint a cada código errado) e
# aceitar código fora da janela de espera, quando ninguém vai consumi-lo.

import relogin  # noqa: E402

check("aceita o comando com o código",
      relogin.extrair_codigo("/codigo 123456"), "123456")
check("aceita o código solto", relogin.extrair_codigo("123456"), "123456")
check("aceita o código com espaço, do jeito que o autenticador mostra",
      relogin.extrair_codigo("123 456"), "123456")
check("aceita código no meio da frase",
      relogin.extrair_codigo("o codigo e 654321 ok"), "654321")
check("comando sem número não vira código",
      relogin.extrair_codigo("/codigo"), None)
check("texto sem número não vira código",
      relogin.extrair_codigo("caiu de novo?"), None)
check("número curto demais não passa", relogin.extrair_codigo("1234"), None)

# TOTP: vetores conhecidos do segredo de exemplo (RFC 6238, SHA-1, passo 30s).
check("TOTP no instante 0", relogin.codigo_totp("JBSWY3DPEHPK3PXP", 0), "282760")
check("TOTP ainda no mesmo passo aos 29s",
      relogin.codigo_totp("JBSWY3DPEHPK3PXP", 29), "282760")
check("TOTP muda ao virar o passo",
      relogin.codigo_totp("JBSWY3DPEHPK3PXP", 30) != "282760", True)
check("segredo em minúsculas e com espaço funciona",
      relogin.codigo_totp("jbsw y3dp ehpk 3pxp", 0), "282760")

caixa = relogin.CodigoPendente()
check("fora da janela, ninguém está esperando", caixa.aguardando, False)
check("código chegado fora da janela é recusado", caixa.entregar("111111"), False)

caixa.pedir()
check("com o login esperando, a caixa está aberta", caixa.aguardando, True)
check("o código é aceito", caixa.entregar("222222"), True)
check("e chega para quem espera", caixa.esperar(5), "222222")
check("depois de consumido, a caixa fecha", caixa.aguardando, False)

# Código de uso único: reenviar o mesmo só gasta tentativa no Facebook.
caixa.pedir()
check("o mesmo código não vale duas vezes", caixa.entregar("222222"), False)
check("um código novo vale", caixa.entregar("333333"), True)
caixa.esperar(5)

# Ninguém respondeu: a espera acaba e a caixa não fica aberta para sempre.
caixa.pedir()
check("espera sem resposta devolve nada", caixa.esperar(0.2), None)
check("e fecha a caixa", caixa.aguardando, False)


# ---------------------------------------------------------------------------
print()
print("=" * 72)
if falhas:
    print(f"{len(falhas)} FALHA(S): " + ", ".join(falhas))
    sys.exit(1)
print("TODOS OS TESTES PASSARAM")
print("=" * 72)
