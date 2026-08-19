# ADV Jobs — Milena Rezner

Bot que lê **posts de grupos do Facebook**, separa demanda jurídica de ruído
para o perfil da Dra. Milena Rezner (Direito Imobiliário e Empresarial) e envia
o que sobra para um grupo do Telegram. Acompanha um **painel web** para ver os
números e mudar as regras sem redeploy.

## O problema que ele resolve

Grupo de advogado no Facebook é um mercado real de trabalho: cliente procurando
advogado, colega passando caso, correspondente para uma audiência. Também é
90% ruído — e o ruído dominante é **advogado se anunciando**, que parece
oportunidade e é concorrência.

O filtro central deste projeto não é de área nem de lugar. É este:

> Quem escreveu está **procurando** um advogado, ou **se oferecendo** como um?

Só o primeiro caso passa.

## Como funciona

```text
ler os grupos → deduplicar → pré-filtros de graça → classificar → fila → enviar
```

1. **Ler** — a cada 30 min, um navegador logado abre cada grupo em ordem
   cronológica, rola algumas telas, expande os "Ver mais" e extrai os posts.
2. **Deduplicar** — o mesmo post visto em dois grupos vira um só.
3. **Pré-filtrar** — divulgação óbvia e post em inglês caem aqui, por texto,
   sem gastar chamada de IA.
4. **Classificar** — o que sobrou vai ao Gemini com o [`profile.md`](bot/config/profile.md),
   que devolve tipo de demanda, área, UF, comarca, se exige presença, se tem
   contato e uma nota de 0 a 100.
5. **Regras duras** — aplicadas **depois** do modelo e por cima dele. O modelo é
   bom lendo e ruim obedecendo regra absoluta: quem lê é ele, quem decide é o
   código.
6. **Fila e envio** — a demanda aprovada entra numa fila ordenada por nota e vai
   ao grupo. Sem teto diário por padrão.

### As regras duras

| Regra | O que faz |
| --- | --- |
| **Divulgação** | Advogado se anunciando é descartado. É o filtro que sustenta o produto. |
| **Área** | Só Imobiliário e Empresarial, nas 8 famílias do `profile.md`. |
| **Localização** | Só morde quando a demanda **exige presença física**. Trabalho que se faz de qualquer lugar passa em qualquer UF — o escritório é 100% digital. |
| **Idioma** | Post escrito em inglês é descartado. |

A regra de localização é a que mais gente entende ao contrário. Ela **não**
pergunta "onde é a demanda?"; pergunta "alguém precisa estar fisicamente lá?".
Elaborar contrato para uma empresa de Manaus passa; audiência em Manaus não.

> **Decisão em aberto.** O site dela diz atender 12 estados *"e demais estados
> sob demanda, com rede de correspondentes"*. Se essa rede vale na prática, o
> certo é **esvaziar `UFS_ATENDIDAS`** — desmarcar todos os estados no painel —
> e deixar passar demanda presencial de qualquer lugar. Quem decide se compensa
> acionar um correspondente é ela, não o bot. A lista de 12 é o alcance próprio,
> e é o padrão conservador até alguém decidir o contrário.

## A fonte Facebook

Não existe API. O Facebook fechou o acesso de terceiros a grupos em 2018, e o
conteúdo hoje é montado por JavaScript atrás de login. Então é um navegador de
verdade ([Playwright](https://playwright.dev/)) com uma sessão de verdade.

**O bot nunca vê e-mail nem senha.** A sessão é criada uma vez, na mão:

```bash
python bot/tools/facebook_login.py
```

Abre uma janela, você loga, entra nos grupos, aperta ENTER. Os cookies vão para
`fb_state.json`. Em produção esse arquivo vive no volume, em
`/app/data/fb_state.json`.

Três coisas que o bot **não** faz, por decisão: não entra em grupo, não curte,
não comenta. Ele lê o que a conta já pode ler, como um membro rolando o feed.

### Quando a sessão morre

É a única falha deste bot que não dá sintoma: sem sessão, o Facebook devolve a
página de login, que tem zero posts — o bot segue rodando e o grupo apenas
emudece. Por isso a página de login levanta `AuthError` explicitamente, e isso
vira um **alerta no privado de quem mantém** (`REPORT_CHAT_IDS`), uma vez por
dia. Nunca no grupo do cliente.

### Os grupos

Ficam em [`bot/config/facebook_groups.txt`](bot/config/facebook_groups.txt), um
por linha:

```text
https://www.facebook.com/groups/123456789 | Advogados de Curitiba | PR
```

A **UF** é o campo que faz o projeto funcionar. Post de grupo quase nunca diz a
cidade — o grupo *é* a cidade. Essa UF é usada só quando o texto não disser
nada; o que está escrito sempre ganha da presunção.

> A conta usada no login precisa **já ser membro** do grupo. Grupo em que ela
> não está devolve zero post, sem erro.

## Quando o post some do Facebook

A mensagem correspondente sai do grupo. O bot revisita os posts já publicados —
alguns por ciclo, cada um no máximo uma vez por `RECHECK_HORAS` — e, quando a
página responde que o conteúdo não existe mais, apaga a mensagem
(`ACAO_VAGA_ENCERRADA=apagar`) ou a risca com um aviso (`marcar`).

**Duas travas contra apagar mensagem boa**, e elas importam mais aqui do que
importariam numa API:

1. **Só a frase explícita conta.** Erro de rede, timeout, layout novo, sessão
   morta — tudo devolve `desconhecida`. A diferença entre "o autor apagou o
   post" e "o Facebook não carregou agora" é a diferença entre acertar e
   destruir o feed do cliente.
2. **São necessárias duas confirmações seguidas**, em checagens distintas. Se o
   post voltar a responder no meio, o contador zera.

> Ajuste de expectativa: isso rende menos no Facebook do que rendia num portal
> de vagas. Portal tira o anúncio do ar quando a vaga é preenchida; quem posta
> num grupo raramente volta para apagar depois de resolver. O que a verificação
> pega de verdade é post apagado pelo autor, removido pelo moderador ou grupo
> que fechou — não "a demanda já foi atendida".

No painel isso é a bolinha ao lado de cada demanda enviada: verde = post no ar,
vermelha = removido. Vermelho é raro, e é essa raridade que o torna informativo.

## Quando o classificador cai

Aqui há uma inversão em relação a bots de vaga, e ela é deliberada.

O padrão da indústria é "filtro fora do ar → aprova tudo", para nunca perder uma
oportunidade. Isso funciona quando existe um teto diário e uma fila por nota
segurando o estrago. **Aqui não há teto**: aprovar tudo com o classificador fora
do ar despejaria no grupo todo advogado que se anunciou naquela hora — exatamente
o ruído que o produto existe para remover.

Então o post **não é marcado como visto** e volta a ser avaliado no ciclo
seguinte. Post de Facebook não desaparece em dez minutos. Não se perde nada e
não se publica lixo; o custo é o atraso de um ciclo.

## Painel

`admin/` — Next.js 16, Tailwind 4, Postgres, zero serviço externo.

- **Visão geral**: enviadas hoje/mês, fila, recusadas, gráfico diário, quebra
  por **área do direito** e por **tipo de demanda**, motivos de recusa.
- **Demandas**: tudo que o robô leu, com filtro por situação, busca por texto,
  autor ou comarca, e o motivo de cada recusa.
- **Configurações**: teto diário (0 = sem teto), janela de horário, nota mínima,
  regras gerais, **UFs atendidas** e as 8 áreas ligáveis por fonte.
- Tema claro/escuro/sistema, na paleta do site dela (osso, espresso, ouro).

O painel escreve em `bot_config`; o bot relê a cada 60s. **Mudança no painel vale
sem redeploy.**

Os links de cada demanda apontam para a **mensagem no grupo do Telegram**
(`t.me/c/<id>/<msg>`), nunca para o Facebook — o produto é o grupo.

> ⚠️ Esse link **só existe em supergrupo**. Grupo básico do Telegram não tem link
> por mensagem. Se todos os links sumirem de uma vez, o `TELEGRAM_CHAT_ID` está
> apontando para um grupo básico.

## Migração de grupo — tratada em código

Um grupo básico do Telegram vira supergrupo sozinho: ao passar de 200 membros,
ao ganhar username público, ao ter o histórico aberto para novos membros. Quando
isso acontece **o chat ID muda** e o antigo morre.

O bot lê o `migrate_to_chat_id` do erro 400, grava o ID novo em `chat_id.json`
(no volume) e reenvia na hora. O ID salvo só é honrado se descender do
`TELEGRAM_CHAT_ID` configurado — assim, trocar o grupo no painel continua
funcionando.

## Uso local

```bash
uv venv --python 3.12 .venv
.venv\Scripts\activate            # Windows
pip install -r bot/requirements.txt
playwright install chromium

cp .env.example .env               # e preencher

python bot/tools/facebook_login.py # uma vez
python bot/main.py
```

Testes:

```bash
python bot/tests/test_unidade.py        # rápido, sem rede
python bot/tests/test_classificador.py  # chama a API de verdade (~14 chamadas)
```

O segundo é o que importa: ele mede se o **filtro** faz o que o cliente quer, e
não se o código faz o que eu escrevi. Um caso que falha ali quase sempre se
conserta com uma frase no `profile.md`, não com uma linha de código.

## Variáveis de ambiente

| Variável | Descrição | Default |
| --- | --- | --- |
| `TELEGRAM_TOKEN` | Token do bot | — |
| `TELEGRAM_CHAT_ID` | Supergrupo de destino | — |
| `REPORT_CHAT_IDS` | Privado de quem mantém. Recebe **alerta de operação**, nunca demanda | — |
| `GEMINI_API_KEY` | Chave do classificador | — |
| `GEMINI_MODEL` | Modelo | `gemini-3.1-flash-lite` |
| `SOURCES` | Fontes ativas | `facebook` |
| `INTERVAL_FACEBOOK` | Intervalo da coleta (s) | `1800` |
| `FACEBOOK_STATE_FILE` | Sessão. Vazio = `DATA_DIR/fb_state.json` | — |
| `FACEBOOK_STATE_B64` | A sessão em base64 — é assim que ela chega no servidor | — |
| `FB_EMAIL` / `FB_PASSWORD` | Credencial para o bot **refazer o login sozinho** no servidor | — |
| `FB_TOTP_SECRET` | Segredo do autenticador. Com ele a renovação é automática de ponta a ponta | — |
| `RELOGIN_AUTOMATICO` | Liga a renovação automática | `true` |
| `RELOGIN_INTERVALO_H` | Piso entre duas tentativas de login | `6` |
| `FACEBOOK_HEADLESS` | Sempre `true` em produção | `true` |
| `FACEBOOK_MAX_POSTS` | Teto de posts por grupo por ciclo | `25` |
| `FACEBOOK_SCROLLS` | Quantas telas rolar por grupo | `4` |
| `DAILY_LIMIT` | **0 = sem teto** | `0` |
| `SEND_WINDOW_START` / `_END` | Janela de envio | `6` / `23` |
| `MIN_SCORE` | Nota mínima para entrar na fila | `0` |
| `REJEITAR_DIVULGACAO` | Recusar advogado se anunciando | `true` |
| `UFS_ATENDIDAS` | UFs. **Vazio = todas**, que é o padrão deste projeto | *(vazio)* |
| `ACEITAR_SEM_LOCAL` | Aceitar demanda presencial sem local declarado | `true` |
| `REJECT_ENGLISH` | Recusar post em inglês | `true` |
| `DATABASE_URL` | Postgres do painel. Vazio = bot roda igual, sem dashboard | — |
| `DATA_DIR` | Estado. **Não cadastrar no Coolify** — é fixo no compose | `/app/data` |
| `TIMEZONE` / `REPORT_HOUR` | Relatório diário | `America/Sao_Paulo` / `22` |
| `ADMIN_EMAIL` / `ADMIN_PASSWORD` | Primeiro usuário do painel, criado no boot | — |

## Deploy

```bash
docker compose up -d --build
```

Via Coolify: New Resource → Docker Compose → conectar ao repo → cadastrar as
variáveis → deploy.

Não há passo manual depois do deploy. As duas coisas que antes exigiam entrar
no container viajam pelo ambiente:

- **a sessão do Facebook**, em `FACEBOOK_STATE_B64` (gerar com
  `python bot/tools/sessao_para_env.py`). O bot escreve o arquivo no volume no
  boot e só sobrescreve quando o valor da variável muda — ele mesmo renova os
  cookies a cada ciclo, e reescrever a cada boot devolveria sessão velha;
- **o primeiro usuário do painel**, em `ADMIN_EMAIL` / `ADMIN_PASSWORD`. Criado
  no boot se ainda não existir; nunca sobrescreve senha de quem já existe.

Duas armadilhas do Coolify que custaram deploy neste projeto:

- **"Connect To Predefined Network" precisa estar ligado**, senão o Traefik não
  alcança o container e responde 503 com tudo no ar;
- **"Escape special characters in labels" precisa estar DESLIGADO** se algum
  label usar variável. Com ele ligado, ``Host(`${ADMIN_DOMAIN}`)`` vira texto
  literal, nenhuma rota casa e o sintoma é o mesmo 503.

Usuários adicionais do painel:

```bash
docker exec adv-jobs-admin node /opt/manut/scripts/criar-usuario.mjs   email@exemplo.com "senha" "Nome"
```

## Stack

- Python 3.12 · `playwright` · `requests` · `google-genai` · `psycopg`
- Next.js 16 · Tailwind 4 · Postgres 16
- Docker / docker-compose · Coolify

Dois arquivos de código no coração: [`facebook.py`](bot/facebook.py) sabe falar
com o Facebook e devolve `Job` normalizado; [`main.py`](bot/main.py) é o pipeline
e não sabe de onde o post veio.
