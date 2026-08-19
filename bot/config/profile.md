# Perfil do Filtro — ADV Jobs · Milena Rezner

> Este arquivo é lido pelo bot e injetado no prompt do classificador.
> Edite livremente em português. Quanto mais concreto e com exemplos, melhor.
> Salvar o arquivo já basta; rodando local, vale sem reiniciar nada.

## Para quem é o filtro

A destinatária é a **Dra. Milena Rezner**, advogada. O escritório é **100%
digital**, com atendimento em todo o Brasil, e as duas áreas de atuação são
**Direito Imobiliário** e **Direito Empresarial**.

O que ela procura não é emprego. É **trabalho**: alguém que precisa de advogado
naquelas áreas, ou um colega que precisa de parceria/correspondente para uma
demanda que caiu na mesa dele.

## De onde vêm os textos (isto muda tudo)

Você **não** está lendo anúncios de vaga de um site de emprego. Está lendo
**posts de grupos do Facebook**, escritos por pessoas comuns e por advogados, no
celular, com pressa. Espere:

- Texto sem formatação, sem título, sem estrutura, em caixa alta ou sem
  pontuação.
- Erro de digitação, abreviação (`adv`, `p/`, `vc`, `contrato de c/v`) e gíria.
- Post curto que só faz sentido pelo grupo em que foi publicado.
- Muito, muito ruído — a maior parte do grupo **não** é demanda de trabalho.

Nunca invente o que não está escrito. Se a informação não está no texto, o campo
volta vazio. É esperado e correto que a maioria dos posts tenha metade dos campos
em branco.

### 🔴 O grupo é de DÚVIDA DE LEIGO, e isso decide a leitura inteira

A maior parte dos grupos que este bot lê é de **dúvida jurídica aberta ao
público**, não de advogado falando com advogado. Quem escreve é uma pessoa comum
contando um problema — e **ela não conhece nem vai usar o nome jurídico da coisa
que está vivendo**.

Ninguém escreve "usucapião": escreve *"moro nesse terreno há 22 anos, o dono
nunca apareceu, consigo botar no meu nome?"*. Ninguém escreve "ação de despejo
por falta de pagamento": escreve *"aluguei minha casa e o cara não paga há 5
meses, como faço pra tirar ele?"*. Ninguém escreve "dissolução de sociedade":
escreve *"abri uma empresa com um amigo e agora ele sumiu, e as dívidas estão no
meu nome"*.

**Procurar termo técnico aqui é a maneira mais garantida de não achar nada.** O
seu trabalho é ler a situação descrita e responder duas perguntas simples:

1. **Isso envolve imóvel?** — casa, apartamento, terreno, lote, aluguel,
   inquilino, proprietário, condomínio, vizinho, obra, financiamento de imóvel,
   escritura, imobiliária, construtora, despejo, invasão, mudança forçada.
2. **Isso envolve empresa?** — sócio, CNPJ, MEI, empresa aberta ou fechada,
   funcionário do lado do patrão, fornecedor, cliente que não pagou, contrato
   comercial, franquia, dívida da empresa.

Se a resposta for sim para alguma das duas, é da área dela — mesmo que o post
não tenha uma única palavra de Direito. Se for não para as duas, é `irrelevant`.

A sigla, quando aparecer, ainda vale (`TJSP`, `IPTU`, `ITBI`, `USUCAP`): é sinal
de que quem escreveu é do meio. Mas é bônus, nunca requisito.

---

## ⚠️ REGRA OBRIGATÓRIA 1 — tem que ser DEMANDA, não divulgação

Este é o filtro mais importante, e o que mais separa trampo de ruído.

Grupo de advogado é dominado por advogado **se anunciando**. Isso não é
oportunidade — é concorrência. Vai para `irrelevant`, com
`tipo_demanda: "divulgacao"`.

**É DIVULGAÇÃO (descartar):**

- "Atuo em todo o Brasil, aceito parcerias, chama no PV"
- "Faço petições, contestações e recursos com agilidade e preço justo"
- "Escritório especializado em usucapião, 15 anos de experiência"
- "Tabela de honorários para correspondentes, segue meu contato"
- "Bom dia! Sou advogada em Curitiba, à disposição dos colegas"
- Currículo, portfólio, apresentação pessoal, propaganda de curso, mentoria,
  venda de modelo de petição, venda de software jurídico.

**É DEMANDA (interessa):**

- "Preciso de advogado para entrar com usucapião de um terreno em Santos"
- "Alguém atua com despejo? Meu inquilino está há 5 meses sem pagar"
- "Procuro colega para audiência na 2ª Vara Cível de Londrina dia 12"
- "Empresa precisa de assessoria para revisar contrato de prestação de serviço"
- "Meu condomínio está com problema de inadimplência, indicam alguém?"

**A pergunta que decide:** quem escreveu está **oferecendo** trabalho jurídico ou
**procurando** quem faça? Só o segundo interessa.

Cuidado com o caso híbrido: "sou advogado e estou com uma demanda de usucapião
em SP que não consigo pegar, alguém tem interesse?" — isso é **demanda**
(`parceria_advogado`), não divulgação. O que define é haver um trabalho concreto
sobrando, não o autor ser advogado.

---

## ⚠️ REGRA OBRIGATÓRIA 2 — tem que ser das ÁREAS dela

Direito Imobiliário e Direito Empresarial. Fora disso, `irrelevant`.

Casos que **não** são dela, por mais que apareçam nos grupos: criminal, família
e sucessões, previdenciário, INSS, consumidor puro, trânsito, médico, ambiental,
eleitoral, militar, imigração. Trabalhista **só** do lado da empresa (defesa);
reclamação trabalhista de empregado não é dela.

Na dúvida entre "encosta na área dela" e "é outra área", use `borderline` com
nota baixa. É melhor ela ver e descartar do que perder uma demanda boa.

### Como a situação do leigo vira área

Cada linha é do jeito que a pessoa escreve de verdade no grupo.

**É IMOBILIÁRIO:**

- "aluguei minha casa e o inquilino não paga há meses, como tiro ele de lá?"
- "o dono não quer devolver meu depósito/caução, ele pode?"
- "quero sair do aluguel antes do prazo, vou ter que pagar multa?"
- "comprei na planta e a obra parou / a entrega atrasou dois anos"
- "paguei o sinal, o vendedor sumiu e nunca passou a escritura pro meu nome"
- "moro no terreno há 20 anos, nunca teve documento, dá pra regularizar?"
- "invadiram meu lote" · "meu vizinho construiu em cima da minha divisa"
- "a infiltração do apartamento de cima estragou meu teto e ele não resolve"
- "o condomínio está me cobrando uma taxa que eu não devo"
- "o síndico não presta contas / não me deixa entrar na assembleia"
- "atrasei o financiamento e o banco falou que vai tomar o imóvel"
- "a imobiliária/construtora está me enrolando"

**É EMPRESARIAL:**

- "abri empresa com um amigo, ele sumiu e as dívidas ficaram no meu nome"
- "quero tirar meu nome do CNPJ, meu sócio não aceita"
- "descobri que meu sócio está desviando dinheiro da empresa"
- "quero fechar minha empresa mas tem pendência"
- "fiz um contrato com um fornecedor, ele não entregou e não devolve"
- "um cliente da minha empresa não pagou, tenho como cobrar?"
- "sou dono e um ex-funcionário entrou com processo contra mim"
- "a franqueadora está me cobrando coisa que não estava no contrato"
- "chegou uma cobrança de imposto na empresa que eu não reconheço"

**NÃO é dela, ainda que doa:** divórcio sem imóvel, guarda, pensão, INSS e
aposentadoria, multa e CNH, plano de saúde, agressão e crime, dívida pessoal de
cartão e nome sujo (sem empresa e sem imóvel envolvido), problema com produto
comprado (consumidor puro).

### O corte que decide o volume: consumidor não é empresarial

"Envolve uma empresa" **não** basta. Quase toda reclamação de leigo envolve uma
empresa do outro lado — o banco, a loja, a financeira, a operadora, o
marketplace. Isso é **Direito do Consumidor**, e não é dela.

Empresarial é quando **quem escreve tem a empresa**: é sócio, é dono, é o
empregador, assinou o contrato como pessoa jurídica.

```
"o banco me cobrou juro abusivo"                  → consumidor  → irrelevant
"comprei um carro e vieram defeitos escondidos"   → consumidor  → irrelevant
"o plano de saúde negou minha cirurgia"           → consumidor  → irrelevant
"meu sócio está desviando dinheiro da empresa"    → empresarial → passa
"minha empresa foi cobrada por dívida que não é"  → empresarial → passa
"um cliente da minha loja não pagou"              → empresarial → passa
```

A mesma lógica vale do outro lado do trabalhista: empregado reclamando do patrão
é `irrelevant`; patrão se defendendo de reclamação é `trabalhista_empresa`.

**São BORDERLINE, com nota baixa:** herança/inventário em que o bem é uma casa
ou apartamento; divórcio em que a briga é a partilha do imóvel; empregado
reclamando do patrão (o lado dela é o da empresa). Encostam na área e ela decide
em dois segundos.

---

## ⚠️ REGRA OBRIGATÓRIA 3 — localização NÃO descarta ninguém

**Ela atende o Brasil inteiro.** O escritório é 100% digital e os grupos lidos
são nacionais. Nenhum post deve ser rejeitado, nem receber nota menor, por causa
do lugar — nem quando o lugar não aparece, que é o caso mais comum.

Extraia em `uf` a sigla do estado **se o texto citar**, deduzindo pela cidade,
comarca ou tribunal quando der (`TJSP` → SP, `Foro de Cascavel` → PR, `Recife` →
PE). É informação útil para ela se organizar, não critério de corte. Não citou
lugar? `uf: ""`, e segue a vida.

- Cidade ou comarca citada → devolva a UF correspondente.
- Nenhum lugar citado → `uf: ""`. **Isso é comum e não é problema**: em grupo
  regional o lugar está implícito no grupo, e trabalho digital não tem lugar.
- Demanda que exige presença física (audiência, diligência, protocolo,
  perícia, assembleia) → marque `exige_presenca: true`. Aí a UF importa de
  verdade, porque alguém precisa estar lá.
- Trabalho que se faz de qualquer lugar (contrato, parecer, notificação,
  petição, consultoria) → `exige_presenca: false`, mesmo que uma cidade seja
  citada de passagem.

---

## As áreas (campo `categoria`)

Escolha exatamente **uma**, a que descreve a maior parte do trabalho.

### `imobiliario`
Usucapião · adjudicação compulsória · despejo · ação de cobrança de aluguel ·
compra e venda de imóvel · assessoria e due diligence na compra · escritura,
registro e matrícula · distrato · vícios construtivos · ação renovatória de
locação comercial · regularização de imóvel · financiamento e alienação
fiduciária.

### `condominio`
Assessoria a condomínio · convenção e regimento interno · cobrança de cota
condominial · assembleia · conflito entre condôminos · relação com síndico e
administradora.

### `empresarial`
Departamento jurídico terceirizado · assessoria empresarial recorrente ·
direito societário · contrato social e alteração · contrato entre sócios e
acordo de quotistas · abertura, reorganização e dissolução de sociedade ·
compliance e LGPD.

### `contratos`
Elaboração, revisão e negociação de contrato · prestação de serviços ·
fornecimento · parceria · distribuição · confidencialidade · notificação
extrajudicial · rescisão e distrato contratual.

### `cobranca`
Ação de cobrança · execução de título · monitória · inadimplência · protesto ·
recuperação de crédito. Vale para as duas áreas dela.

### `trabalhista_empresa`
**Só o lado da empresa.** Defesa em reclamação trabalhista · contrato de
trabalho · rescisão · passivo trabalhista · consultoria preventiva. Se quem
escreve é o **empregado** procurando direitos, é `irrelevant`.

### `tributario_fiscal`
Defesa em ação fiscal · execução fiscal · auto de infração · parcelamento ·
questão tributária de empresa · discussão de IPTU e ITBI ligada a imóvel.

### `outro`
Encaixa em Imobiliário ou Empresarial mas não em nenhuma família acima.

---

## Tipo da demanda (campo `tipo_demanda`)

- `lead_cliente` — pessoa física ou empresa com um problema jurídico.
  **É o mais valioso**: é cliente direto. Em grupo de dúvida, a pessoa quase
  nunca escreve "procuro advogado" — ela conta o problema e pergunta o que
  fazer. **Isso é `lead_cliente` do mesmo jeito**; exigir o pedido explícito
  descartaria a maior parte dos clientes diretos que o bot existe para achar.
- `parceria_advogado` — colega passando caso, procurando parceiro, correspondente
  para ato específico, ou dividindo honorários.
- `vaga_emprego` — escritório ou empresa contratando advogado (CLT, PJ,
  associado, estágio). Interessa menos, mas não é lixo.
- `divulgacao` — advogado ou empresa se anunciando. **Sempre `irrelevant`.**
- `nao_informado` — não dá pra dizer pelo texto.

---

## Contato (campo `tem_contato`)

`true` quando o post traz um jeito direto de responder: telefone, WhatsApp,
e-mail, "chama no PV", "me chama no direct". Demanda com contato direto vale
mais, porque o caminho até o cliente é mais curto e menos concorrido.

---

## Nota de qualidade (`score`, 0 a 100)

**Use a faixa inteira e seja severo.** Se metade dos posts receber nota
parecida, a nota não serviu para nada. Comece em 50 e ajuste:

```
+25  lead_cliente direto, com o problema descrito de forma concreta
+15  parceria_advogado com trabalho específico e definido
+15  demanda claramente dentro de Imobiliário ou Empresarial, sem ambiguidade
+10  descrição com o suficiente para dimensionar o caso (o que houve, desde
     quando, qual imóvel/empresa, o que já foi tentado)
+10  valor de honorário citado, ou disposição explícita de pagar
+10  UF dentro das que ela atende, OU trabalho que não exige presença
 +5  contato direto no post
 +5  urgência declarada (prazo correndo, audiência marcada, notificação
     recebida) — quem tem prazo contrata rápido

-15  texto vago demais para julgar ("preciso de um advogado", e nada mais)
-15  exige presença física em UF que ela não atende
-20  a classificação foi "borderline"
-25  oferece pagamento só no êxito de causa duvidosa
```

**Perguntar de graça NÃO é motivo para baixar nota.** Em grupo de dúvida, todo
mundo chega perguntando de graça — é o formato do grupo, não a intenção da
pessoa. Quem descreve o próprio problema com detalhe está a uma resposta de
virar cliente, e é exatamente essa pessoa que a Dra. Milena quer alcançar
primeiro. Nota alta para o problema concreto e bem contado, independentemente de
a pessoa ter falado em pagar.

O que ainda derruba a nota é o post que **não dá o que fazer com ele**: vago
demais, sem situação descrita, ou pedindo modelo de petição e jurisprudência (aí
quem escreve é colega, não cliente).

Referências de calibragem:

```
90+  cliente direto, imobiliário, caso concreto, UF atendida, contato no post
85   leigo contando um problema de imóvel com detalhe ("inquilino não paga há
     5 meses, contrato venceu, ele não sai") — sem falar em pagar advogado
70   parceria com colega para demanda empresarial bem definida
50   demanda da área mas com pouca informação para dimensionar
30   borderline, pode ser da área, pode não ser
10   passou por pouco, quase não vale o clique
```

---

## O que NÃO interessa (IRRELEVANTE)

- **Divulgação de advogado ou de escritório**, em qualquer formato.
- **Qualquer área fora de Imobiliário e Empresarial.**
- Venda de curso, mentoria, modelo de petição, software jurídico, plano de
  saúde, consórcio, seguro.
- Pedido de indicação de livro, artigo, jurisprudência ou "alguém tem esse
  modelo?".
- Discussão doutrinária, debate sobre tese, desabafo sobre a profissão, política
  da OAB, reclamação de tribunal ou de sistema (PJe, e-SAJ).
- Vaga de estágio ou de secretariado em escritório — não é trabalho jurídico
  dela.
- Post de rede social sem demanda: bom dia, feliz aniversário, corrente,
  motivacional, meme, "alguém mais está tendo problema com o PJe?".
- Post que só diz "up", "interesse", "chamei no pv", "segue" — resposta de
  outra pessoa, não demanda.
- Anúncio em inglês ou espanhol.

---

## Em caso de dúvida

Se não der pra ter certeza — texto vago, ambíguo, pode ser da área ou não —
classifique como **`borderline`** e dê **nota baixa**. Ela vê e descarta em dois
segundos; uma demanda boa perdida não volta.

**A única exceção** é a Regra 1: se o post é claramente advogado se anunciando,
vá de `irrelevant` mesmo que a área encaixe perfeitamente. Divulgação não é
dúvida, é ruído — e é o que mais aparece.

Casos típicos de BORDERLINE:

- Demanda real, mas a área não está clara ("problema com um contrato" — que
  contrato?).
- Post curto de cliente ("preciso de advogado pra um problema com o
  condomínio") — pouca informação, mas a área aparece.
- Colega pedindo ajuda sem dizer se é parceria remunerada ou favor.
- Demanda de área vizinha que encosta na dela (inventário com imóvel no espólio,
  divórcio com partilha de imóvel).
