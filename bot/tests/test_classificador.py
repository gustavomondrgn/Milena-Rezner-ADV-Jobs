"""Roda o classificador de verdade, contra a API, em posts REAIS dos grupos.

O teste de unidade prova que o código faz o que eu escrevi. Este prova que o
**filtro** faz o que o cliente quer — que é outra coisa, e a única que importa
no fim. O `profile.md` é um prompt: ele não tem sintaxe para errar, ele tem
julgamento para errar, e julgamento só se verifica medindo.

    python bot/tests/test_classificador.py

Gasta ~20 chamadas de IA. Não manda nada para o Telegram e não escreve no banco.

## De onde vieram estes casos

A primeira versão deste arquivo tinha 14 posts que **eu escrevi imaginando** como
seria um post de grupo. Ele passava 14/14 e não provava quase nada: post real é
mais curto, mais bagunçado, sem pontuação e quase nunca usa o nome jurídico da
coisa.

Os casos marcados `[real]` foram colhidos em **16/08/2026** dos nove grupos
configurados, com `FacebookSource.fetch()` — texto exatamente como saiu do
Facebook, com erro de digitação e tudo. Os poucos marcados `[sintético]` cobrem
situações que não apareceram na colheita (parceria entre advogados, empresa
contratando defesa) e continuam valendo como rede de segurança.

Um caso que falha quase nunca é bug de código: é uma frase que falta no
`profile.md`. É exatamente para isso que este arquivo existe.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="advjobs-clf-")

import main  # noqa: E402
from sources import Job  # noqa: E402

# (nome, texto do post, UF do grupo, esperado, tipo_demanda, categoria)
#   esperado: "passa" | "corta"   ·   tipo/categoria: None = não checa
CASOS = [
    # ------------------------------------------------------------ passa ------
    ("[real] aluguel sem vistoria, caução retida", """
Eu aluguei um imóvel cujo esse não foi feito vistoria alguma até pq tinha gente
morando nele a pessoa saiu e no outro dia de manhã já entrei um condomínio
perigoso q vivia tendo tiros jacks lugar super violento mais eu não sabia eu quiz
sair do AP pois tenho filhos pequenos a dona do imóvel me cobrou 9 mil por quebra
de contrato e ainda não quiz devolver o calção até aí ok , eu saí do AP pois ela
tinha bloqueado minha saído com meus móveis e eu não ia paga 9 mil pra ela pq não
estava esse valor no contrato ela veio no Facebook postou fotos das minhas filhas
e minha mãe chingando me ameaçando , nesse caso oq posso fazer ? Preciso de
orientação!
""", "", "passa", "lead_cliente", "imobiliario"),

    ("[real] vizinho abriu janelas na divisa", """
Gostaria de umas orientações se possível, Tenho uma casa alugada e não para
ninguém na casa, pois meus vizinhos ao lado abriram três jánelas na divisa do
muro, no sobrado dele em cima, e está tirando toda a privacidade dos meus
inquilinos, já tem um tempo que ele abriu, más agora estou querendo subir um
paredão em cima do muro de divisa e fechar as janelas dele, pois todos os
inquilinos que moram na casa saí e todos reclamam de falta de privacidade.
O que vocês acham que devo fazer numa situação dessa?
""", "", "passa", "lead_cliente", "imobiliario"),

    ("[real] imóvel financiado na planta (post curtíssimo)", """
Busco algum advogado especialista em imóvel financiado na planta.
Favor chamar no Direct
""", "PR", "passa", "lead_cliente", "imobiliario"),

    ("[real] locador aumentou o aluguel três vezes", """
Preciso de um advogado imobiliária estou com problema de aluguel no caso eu
aluguei uma casa no caso o dono da casa está me oprimindo já aumentou o aluguel 3
vezes neste ano
""", "", "passa", "lead_cliente", "imobiliario"),

    ("[real] invasão de casa no terreno da família", """
Bom dia pessoal, tem um assunto meio delicado e queria tirar uma dúvida, meu pai
tem uma casa onde não moramos, e deixamos uma certa parte deste terreno para
minha irma por parte de mãe morar com os sobrinhos. Porem minha irma abandonou
eles, o pai foi morar com eles, porem se desviou e abandonou as crianças tambem,
virou morador de rua, usuário. Porem depois de um ano o pai voltou invadiu uma
casa que tem no terreno e ainda por cima pegou a geladeira do filho de 17 e
fogão, a parte do terreno tinha ficado com os filhos para morarem, tem como fazer
algo pra tirar ele de la sem usar a força, algo judicialmente?
""", "", "passa", None, None),

    ("[real] posse da casa do pai contra a madrasta", """
moro em casa a mais de 10 anos ela era do meu pai nós morávamos nela junto com
minha mãe e meu irmãos, sendo que só eu e meu irmão caçula é filho dele me
ausentei pra reforma a casa minha madrasta tentou invadir a casa,e chegou a jogar
algumas coisas minhas fora e disse que se eu sair ela entra pq disse que tem
documento eles pagam aluguel a mais de 15 anos meu pai e ela eles tem direito na
nossa casa sendo que o documento é compra e venda?
""", "", "passa", None, None),

    ("[real] herança em que o bem é uma casa (borderline)", """
Uma dúvida. Sou casada com separação total de bens há 8 anos, meu esposo faleceu.
Não possui filhos, nem pais vivos, apenas 2 irmãos. Ele possui uma casa, os
irmãos tem direto a casa? Ou só eu? Mesmo casada com separação total de bens? Eu
tenho direito ou preciso ir na justiça pra brigar por ela?
""", "", "passa", None, None),

    ("[sintético] parceria: correspondente para audiência", """
Colegas, procuro advogado para fazer audiencia de instrucao na 2a Vara Civel de
Londrina no dia 12/09 as 14h. Processo simples, cobranca de aluguel. Pago R$ 400
pelo ato, com contrato de parceria.
""", "PR", "passa", "parceria_advogado", None),

    ("[sintético] parceria: repasse de caso imobiliário", """
Sou advogada aqui de Curitiba e peguei uma demanda de adjudicacao compulsoria que
nao vou conseguir tocar por conflito de agenda. Cliente ja tem contrato de compra
e venda quitado. Alguem tem interesse? Divido honorarios 50/50.
""", "PR", "passa", "parceria_advogado", "imobiliario"),

    ("[sintético] empresa: entrada de sócio", """
Tenho uma pequena empresa de TI e vou entrar com um socio novo. Preciso de alguem
para redigir o acordo de socios e alterar o contrato social. Alguem trabalha com
isso? Atendimento pode ser online.
""", "", "passa", "lead_cliente", "empresarial"),

    ("[sintético] empresa se defendendo de reclamatória", """
Recebi uma reclamacao trabalhista de um ex-funcionario da minha loja. Audiencia
marcada para o mes que vem. Preciso de um advogado para fazer a defesa da
empresa. Urgente.
""", "RS", "passa", None, "trabalhista_empresa"),

    # A regra de UF foi desligada em 16/08/2026 (os grupos são nacionais e o
    # atendimento é 100% digital). Este caso existe para PROVAR isso: antes ele
    # era cortado por ser em Manaus, e agora tem que passar.
    ("[sintético] diligência presencial em UF distante", """
Preciso de um advogado para fazer uma diligencia presencial no cartorio de
registro de imoveis de Manaus, protocolar uns documentos de usucapiao. Pago
R$ 300 pelo servico.
""", "AM", "passa", None, None),

    # ------------------------------------------------------------ corta ------
    ("[real] INSS / salário-maternidade", """
Quem recebeu auxílio maternidade pela empresa, pode receber pelo INSS também?
Não trabalho mais
""", "", "corta", None, None),

    ("[real] empregado querendo processar o patrão", """
Preciso de um advogado, estou saindo da empresa saint gobain de cumbica e
gostaria de processar por falta de pagamento de insalubridade
""", "SP", "corta", None, None),

    ("[real] criminal / Maria da Penha", """
eu caí na Maria da Penha sai na custódia mas a vítima não relatou oq tá no
processo como faz pra tirar
""", "", "corta", None, None),

    # O corte que mais decide volume: do outro lado tem uma empresa, mas quem
    # escreve é consumidor. Se isto passar, o grupo da Milena vira um balcão de
    # reclamação de banco e de loja.
    ("[real] consumidor: carro comprado com defeito", """
No dia 02 de março comprei um veículo e o paguei a vista, via pix e espécie, na
agência de um amigo. Por se tratar de um amigo, tudo foi tratado verbalmente! Não
houve um contrato se quer ou nota fiscal. Me foi prometido q ele me entregaria o
carro 10 dias depois pois havia um probleminha nos freios para fazer e desde
então quase 6 meses se passaram entre MUITAS MENTIRAS. No dia seguinte o levei a
um mecânico de minha confiança e descobri q o carro tinha outros problemas.
""", "", "corta", None, None),

    ("[real] divulgação: captação de salário-maternidade", """
ESTÁ GRÁVIDA E DESEMPREGADA? Você sabia que pode ter direito ao
salário-maternidade, mesmo estando desempregada? Nós analisamos o seu caso e
orientamos sobre as contribuições necessárias para buscar o benefício.
Atendimento rápido e fácil. Atendemos todo o Brasil. Possibilidade de receber 4
parcelas de R$ 1.621,00. Indicou alguém? Você pode ganhar R$ 100!
Chame no WhatsApp: (65) 99355-2898
""", "", "corta", "divulgacao", None),

    ("[real] divulgação: cartão de visita de advogada", """
Advogada à disposição! Atuação nas áreas de: Direito Civil, Divórcio (judicial e
extrajudicial), Pensão alimentícia, Guarda e regulamentação de visitas,
Inventário, Direito do Consumidor, Indenizações, Contratos, Cobranças e demais
demandas cíveis. Direito Criminal – atendimento 24 horas. Atendimento on-line e
presencial. Entre em contato para analisar seu caso. (11) 94889-7493.
""", "", "corta", "divulgacao", None),

    ("[real] compra de processo trabalhista", """
Compramos processos trabalhistas. Adiantamos valor para o trabalhador e
honorários do advogado. O Processo precisa estar em segunda instância. Entre em
contato para mais informações. Link para o Whatsapp. Para mais pessoas terem
acesso a esse benefício, por favor, CURTA, COMENTE, COMPARTILHE.
""", "", "corta", None, None),

    ("[real] ruído: boas-vindas aos novos membros", """
Vamos dar as boas-vindas aos novos membros. Nadia Lázaro, Mikael Silva, Melo R
Arnon, Luisa Neves - Advogada, Simone Knauth Lopes, Castro Cass, Rivaldo Junior,
Isabela Faria, Jean Eduardo Lima - Adv, Senara Cruz, João Neto.
""", "RJ", "corta", None, None),
]

# Casos reais em que **eu não sei** qual é a resposta certa — a decisão é de
# negócio, não de código. Rodam e imprimem o veredito sem cobrar nada, para o
# Gustavo e a Dra. Milena olharem e decidirem se querem esse tipo de post ou não.
AMBIGUOS = [
    ("moto financiada que a empresa não quitou", """
Eu tenho a seguinte dúvida, em setembro do ano passado eu passei uma moto que era
nova financiada para uma empresa quitar e tirar do nome da minha amiga, e então
fomos no cartório colocamos a moto na responsabilidade desta empresa sob
cartório. Porém até hoje eles não quitaram a moto ainda, não respondem mais no
WhatsApp, e desativaram a conta no Instagram. No contrato do cartório diz que
eles tem que quitar em até 24 meses, porém até agora nada, estou com medo de ter
caído em golpe, como devo proceder?
"""),
    ("cobrança pessoal sem nenhum detalhe", "posso processar alguém que está me devendo ??"),
    ("colega perguntando procedimento de despejo comercial", """
Bom dia, doutores! Quando acontece um despejo comercial; as chaves da loja, ficam
com quem... (O Oficial de Justiça; ou o proprietário do imóvel?) Obrigado!
"""),
]


def _classificar(cfg, nome: str, texto: str, uf: str, i: int):
    job = Job(
        source="facebook",
        source_id=f"teste{i}",
        title=texto.strip().split("\n")[0][:80],
        url="https://facebook.com/groups/x/posts/1",
        description=texto.strip(),
        company="Fulano de Tal",
        location=uf,
        published_at="2026-08-16T10:00:00",
        job_type="Post de grupo",
    )
    return main.analyze_job(job, cfg)


def main_() -> int:
    if not main.GEMINI_API_KEY:
        print("GEMINI_API_KEY ausente — sem isso este teste não tem o que medir.")
        return 1
    if main.load_profile() is None:
        print(f"profile.md não encontrado em {main.PROFILE_FILE}")
        return 1

    cfg = main.config_atual()
    ufs = cfg.get("ufs_atendidas") or []
    print("=" * 78)
    print("CLASSIFICADOR — posts reais dos grupos, contra a API")
    print(f"modelo={main.GEMINI_MODEL}  UFs={','.join(ufs) if ufs else 'todas'}")
    print("=" * 78)

    falhas: list[str] = []
    for i, (nome, texto, uf_grupo, esperado, tipo_esp, cat_esp) in enumerate(CASOS, 1):
        a = _classificar(cfg, nome, texto, uf_grupo, i)

        if a.get("falhou"):
            print(f"\n[{i:2}] {nome}\n     ERRO NA API: {a.get('reason')}")
            falhas.append(f"{nome} (api)")
            continue

        passou = a["category"] != "irrelevant"
        obtido = "passa" if passou else "corta"
        ok = obtido == esperado
        if tipo_esp and a.get("tipo_demanda") != tipo_esp:
            ok = False
        if cat_esp and a.get("categoria") != cat_esp:
            ok = False

        print(f"\n[{i:2}] {' OK ' if ok else 'FALHA'} | {nome}")
        print(f"     esperado : {esperado}"
              + (f" · {tipo_esp}" if tipo_esp else "")
              + (f" · {cat_esp}" if cat_esp else ""))
        print(f"     obtido   : {obtido} · {a.get('tipo_demanda')} · "
              f"{a.get('categoria')} · nota {a.get('score')}")
        print(f"     motivo   : {a.get('reason')}")
        if a.get("motivo_corte"):
            print(f"     corte    : {a['motivo_corte']}")
        if not ok:
            falhas.append(nome)

    print()
    print("=" * 78)
    print("AMBÍGUOS — sem veredito, é decisão de negócio")
    print("=" * 78)
    for j, (nome, texto) in enumerate(AMBIGUOS, len(CASOS) + 1):
        a = _classificar(cfg, nome, texto, "", j)
        if a.get("falhou"):
            print(f"\n  {nome}: ERRO NA API")
            continue
        print(f"\n  {nome}")
        print(f"     → {'passa' if a['category'] != 'irrelevant' else 'corta'} · "
              f"{a.get('tipo_demanda')} · {a.get('categoria')} · nota {a.get('score')}")
        print(f"     {a.get('reason')}")

    print()
    print("=" * 78)
    print(f"{len(CASOS) - len(falhas)}/{len(CASOS)} casos como esperado")
    if falhas:
        print("FALHARAM: " + ", ".join(falhas))
        print("Na maioria das vezes o conserto é uma frase no profile.md, "
              "não uma linha de código.")
        return 1
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main_())
