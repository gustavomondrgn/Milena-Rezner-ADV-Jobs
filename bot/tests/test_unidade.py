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
print()
print("=" * 72)
if falhas:
    print(f"{len(falhas)} FALHA(S): " + ", ".join(falhas))
    sys.exit(1)
print("TODOS OS TESTES PASSARAM")
print("=" * 72)
