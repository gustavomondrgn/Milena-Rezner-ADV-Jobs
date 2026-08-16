"""Roda o classificador de verdade, contra a API, em posts realistas.

O teste de unidade prova que o código faz o que eu escrevi. Este prova que o
**filtro** faz o que o cliente quer — que é outra coisa, e a única que importa
no fim. O `profile.md` é um prompt: ele não tem sintaxe para errar, ele tem
julgamento para errar, e julgamento só se verifica medindo.

    python bot/tests/test_classificador.py

Gasta ~14 chamadas de IA. Não manda nada para o Telegram e não escreve no banco.

Cada caso declara o que se espera; no fim sai o placar. Um caso que falha não é
necessariamente um bug de código — na maior parte das vezes é uma frase que
falta no `profile.md`, e é exatamente para isso que este arquivo existe.
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

# (título curto, texto do post, UF do grupo, esperado)
#   esperado: "passa" | "corta"
#   e, quando faz sentido, o tipo_demanda e a categoria esperados
CASOS = [
    # ---------------------------------------------------------------- passa --
    ("cliente usucapião", """
Pessoal, preciso muito de um advogado para entrar com usucapiao de um terreno
aqui em Santos. Meu pai mora no imovel ha 22 anos, nunca teve escritura, e agora
apareceu um herdeiro do antigo dono querendo tomar. Tenho conta de luz antiga e
testemunhas dos vizinhos. Alguem pode me ajudar? Meu whats e 13 99999-0000
""", "SP", "passa", "lead_cliente", "imobiliario"),

    ("cliente despejo", """
Boa tarde. Meu inquilino esta ha 5 meses sem pagar o aluguel e se recusa a
desocupar. Ja mandei notificacao pelo cartorio e nada. Preciso entrar com acao
de despejo. Alguem indica advogado?
""", "PR", "passa", "lead_cliente", "imobiliario"),

    ("parceria correspondente", """
Colegas, procuro advogado para fazer audiencia de instrucao na 2a Vara Civel de
Londrina no dia 12/09 as 14h. Processo simples, cobranca de aluguel. Pago R$ 400
pelo ato, com contrato de parceria.
""", "PR", "passa", "parceria_advogado", None),

    ("parceria repasse de caso", """
Sou advogada aqui de Curitiba e peguei uma demanda de adjudicacao compulsoria
que nao vou conseguir tocar por conflito de agenda. Cliente ja tem contrato de
compra e venda quitado. Alguem tem interesse? Divido honorarios 50/50.
""", "PR", "passa", "parceria_advogado", "imobiliario"),

    ("empresa contrato social", """
Tenho uma pequena empresa de TI e vou entrar com um socio novo. Preciso de
alguem para redigir o acordo de socios e alterar o contrato social. Alguem
trabalha com isso? Atendimento pode ser online.
""", "", "passa", "lead_cliente", "empresarial"),

    ("condomínio inadimplência", """
Sou sindico de um predio com 48 unidades e estamos com 11 unidades inadimplentes
ha mais de um ano. Precisamos de assessoria juridica para cobranca das cotas
condominiais e revisao da convencao. Aceito indicacoes.
""", "SC", "passa", None, None),

    ("defesa trabalhista da empresa", """
Recebi uma reclamacao trabalhista de um ex-funcionario da minha loja. Audiencia
marcada para o mes que vem. Preciso de um advogado para fazer a defesa da
empresa. Urgente.
""", "RS", "passa", None, "trabalhista_empresa"),

    # ---------------------------------------------------------------- corta --
    ("divulgação clássica", """
Bom dia, colegas! Sou advogado atuante em Direito Imobiliario ha 12 anos, atuo
em todo o Brasil e aceito parcerias. Tabela de honorarios no privado.
OAB/PR 45.678
""", "PR", "corta", "divulgacao", None),

    ("divulgação de escritório", """
ESCRITORIO ESPECIALIZADO em usucapiao e regularizacao de imoveis. Fazemos
peticoes, contestacoes e recursos com agilidade e preco justo. Orcamento sem
compromisso, agende sua consulta pelo link da bio.
""", "SP", "corta", "divulgacao", None),

    ("área errada — criminal", """
Meu filho foi preso em flagrante ontem a noite e a audiencia de custodia e
amanha. Preciso urgentemente de um advogado criminalista aqui em Recife.
""", "PE", "corta", None, None),

    ("área errada — previdenciário", """
Boa noite. Tive meu auxilio doenca negado pelo INSS mesmo com laudo medico.
Alguem trabalha com direito previdenciario para entrar com recurso?
""", "MG", "corta", None, None),

    ("empregado, não empresa", """
Trabalhei 3 anos numa empresa sem registro em carteira e fui mandado embora sem
receber nada. Quero processar. Alguem pega causa trabalhista?
""", "SP", "corta", None, None),

    ("ruído de grupo", """
Bom dia a todos! Que Deus abencoe o dia de cada um de voces. Alguem mais esta
com problema para acessar o PJe hoje? Aqui nao carrega de jeito nenhum.
""", "SP", "corta", None, None),

    ("presença fora das UFs", """
Preciso de um advogado para fazer uma diligencia presencial no cartorio de
registro de imoveis de Manaus, protocolar uns documentos de usucapiao. Pago
R$ 300 pelo servico.
""", "AM", "corta", None, None),
]


def main_() -> int:
    if not main.GEMINI_API_KEY:
        print("GEMINI_API_KEY ausente — sem isso este teste não tem o que medir.")
        return 1
    if main.load_profile() is None:
        print(f"profile.md não encontrado em {main.PROFILE_FILE}")
        return 1

    cfg = main.config_atual()
    print("=" * 78)
    print("CLASSIFICADOR — posts reais contra a API")
    print(f"modelo={main.GEMINI_MODEL}  UFs={','.join(cfg.get('ufs_atendidas') or [])}")
    print("=" * 78)

    falhas: list[str] = []
    for i, (nome, texto, uf_grupo, esperado, tipo_esp, cat_esp) in enumerate(CASOS, 1):
        job = Job(
            source="facebook",
            source_id=f"teste{i}",
            title=texto.strip().split("\n")[0][:80],
            url="https://facebook.com/groups/x/posts/1",
            description=texto.strip(),
            company="Fulano de Tal",
            location=uf_grupo,
            published_at="2026-08-15T10:00:00",
            job_type="Post de grupo",
        )
        a = main.analyze_job(job, cfg)

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

        marca = " OK " if ok else "FALHA"
        print(f"\n[{i:2}] {marca} | {nome}")
        print(f"     esperado : {esperado}"
              + (f" · {tipo_esp}" if tipo_esp else "")
              + (f" · {cat_esp}" if cat_esp else ""))
        print(f"     obtido   : {obtido} · {a.get('tipo_demanda')} · "
              f"{a.get('categoria')} · nota {a.get('score')}")
        print(f"     local    : uf={a.get('uf')!r} comarca={a.get('comarca')!r} "
              f"presença={a.get('exige_presenca')} contato={a.get('tem_contato')}")
        print(f"     motivo   : {a.get('reason')}")
        if a.get("motivo_corte"):
            print(f"     corte    : {a['motivo_corte']}")
        if not ok:
            falhas.append(nome)

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
