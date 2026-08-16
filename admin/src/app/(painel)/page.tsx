import Link from 'next/link';
import { kpis, serieDiaria, porArea, porTipo, motivosRecusa, listarVagas,
         bancoTemDados, linkTelegram } from '@/lib/metricas';
import { lerConfig, rotuloCategoria, rotuloTipoDemanda, rotuloFonte } from '@/lib/config';
import { BarrasHorizontais } from '@/components/Graficos';
import { GraficoDiario } from '@/components/GraficoDiario';
import { Cartao, Metrica, Variacao, Nota, Vazio, Bolinha, ROTULOS_STATUS }
  from '@/components/ui';

// O painel mostra o estado de agora; cache de página aqui só serviria para
// mostrar número velho.
export const dynamic = 'force-dynamic';

export default async function VisaoGeral() {
  const [temDados, k, serie, areas, tipos, motivos, cfg, ultimas] = await Promise.all([
    bancoTemDados(), kpis(), serieDiaria(30), porArea(30), porTipo(30),
    motivosRecusa(30), lerConfig(), listarVagas({ status: 'sent', porPagina: 8 }),
  ]);

  if (!temDados) {
    return (
      <>
        <Cabecalho cfg={cfg} />
        <div className="cartao mt-6">
          <Vazio
            titulo="Nenhuma demanda registrada ainda"
            texto="O painel se enche sozinho conforme o robô trabalha. Se o bot já está no ar, confirme duas coisas: que a variável DATABASE_URL está configurada nele, e que há grupos preenchidos em bot/config/facebook_groups.txt."
          />
        </div>
      </>
    );
  }

  // 0 = sem teto, que é o padrão deste projeto. Toda a interface de cota some
  // nesse caso: barra em 0%, "3 / 0" e "faltam -3" seriam ruído afirmando um
  // limite que não existe.
  const temTeto = cfg.daily_limit > 0;
  const usoDaCota = temTeto
    ? Math.min(100, Math.round((k.publicadasHoje / cfg.daily_limit) * 100))
    : 0;

  return (
    <>
      <Cabecalho cfg={cfg} />

      {/* Duas colunas já no celular: quatro cartões empilhados dariam uma tela
          inteira de rolagem antes do primeiro gráfico. */}
      <div className="grid gap-3 mt-5 grid-cols-2 sm:gap-4 sm:mt-6 lg:grid-cols-4">
        <Metrica
          rotulo="Enviadas hoje"
          valor={temTeto ? `${k.publicadasHoje} / ${cfg.daily_limit}` : k.publicadasHoje}
          destaque
          detalhe={<Variacao atual={k.publicadasHoje} anterior={k.publicadasOntem} />}
        />
        <Metrica
          rotulo="No mês"
          valor={k.publicadasMes}
          detalhe={k.mediaDiaria30 != null ? `${k.mediaDiaria30}/dia nos últimos 30 dias` : undefined}
        />
        <Metrica
          rotulo="Esperando na fila"
          valor={k.naFila}
          detalhe={temTeto ? 'aprovadas, aguardando a vez' : 'aprovadas, saindo no próximo ciclo'}
        />
        <Metrica
          rotulo="Recusadas hoje"
          valor={k.recusadasHoje}
          detalhe={`de ${k.analisadasHoje} analisada(s) hoje`}
        />
      </div>

      {/* A cota só existe na tela quando existe de verdade. */}
      {temTeto ? (
        <div className="cartao mt-4 p-4 sm:p-5">
          <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 mb-3">
            <h2 className="text-[13px] font-semibold tracking-tight">Cota de hoje</h2>
            <span className="text-[12.5px]" style={{ color: 'var(--texto-suave)' }}>
              enviando entre {cfg.window_start}h e {cfg.window_end}h
            </span>
          </div>
          <div className="h-2.5 rounded-full overflow-hidden" style={{ background: 'var(--superficie-2)' }}>
            <div className="h-full rounded-full transition-all"
                 style={{ width: `${usoDaCota}%`, background: 'var(--acento)' }} />
          </div>
          <p className="mt-2.5 text-[12.5px]" style={{ color: 'var(--texto-suave)' }}>
            {k.publicadasHoje >= cfg.daily_limit
              ? 'Cota do dia atingida — o resto da fila entra amanhã.'
              : `Faltam ${cfg.daily_limit - k.publicadasHoje} demanda(s) para fechar a cota do dia.`}
          </p>
        </div>
      ) : (
        <p className="mt-4 text-[12.5px]" style={{ color: 'var(--texto-suave)' }}>
          Sem teto diário: toda demanda aprovada vai ao grupo, entre{' '}
          {cfg.window_start}h e {cfg.window_end}h.
        </p>
      )}

      <div className="mt-4">
        <Cartao
          titulo="Demandas enviadas por dia"
          acao={<span className="text-[12px]" style={{ color: 'var(--texto-fraco)' }}>últimos 30 dias</span>}
        >
          <GraficoDiario dados={serie} />
        </Cartao>
      </div>

      <div className="grid gap-4 mt-4 lg:grid-cols-2">
        <Cartao titulo="Por área do direito" acao={<Periodo />}>
          <BarrasHorizontais
            dados={areas.map((a) => ({
              rotulo: rotuloCategoria(a.categoria),
              valor: a.enviadas,
            }))}
          />
        </Cartao>

        <Cartao titulo="Tipo de demanda" acao={<Periodo />}>
          <BarrasHorizontais
            dados={tipos.map((t) => ({
              rotulo: rotuloTipoDemanda(t.tipo_demanda),
              valor: t.enviadas,
            }))}
          />
        </Cartao>
      </div>

      <div className="mt-4">
        <Cartao titulo="Por que foram recusadas" acao={<Periodo />}>
          <BarrasHorizontais
            dados={motivos.map((m) => ({
              rotulo: ROTULOS_STATUS[m.motivo] ?? m.motivo,
              valor: m.total,
            }))}
          />
        </Cartao>
      </div>

      <div className="mt-4">
        <Cartao
          titulo="Últimas enviadas"
          acao={
            <Link href="/demandas"
                  className="text-[12.5px] font-medium pointer-coarse:py-2 pointer-coarse:-my-2"
                  style={{ color: 'var(--acento)' }}>
              Ver todas →
            </Link>
          }
        >
          {ultimas.linhas.length === 0 ? (
            <p className="text-sm py-4" style={{ color: 'var(--texto-fraco)' }}>
              Nenhuma demanda enviada ainda.
            </p>
          ) : (
            <ul className="flex flex-col divide-y" style={{ borderColor: 'var(--borda)' }}>
              {ultimas.linhas.map((v) => (
                <li key={`${v.uid}-${v.status}`}
                    className="py-2.5 first:pt-0 last:pb-0 flex items-center gap-2.5 sm:gap-3">
                  <Bolinha fechadaEm={v.closed_at} />
                  <Nota valor={v.score} />
                  <div className="min-w-0 flex-1">
                    <TituloVaga
                      titulo={v.resumo_demanda || v.title}
                      link={linkTelegram(v.telegram_message_id)}
                    />
                    <p className="text-[12px] truncate" style={{ color: 'var(--texto-fraco)' }}>
                      {[
                        v.tipo_demanda ? rotuloTipoDemanda(v.tipo_demanda) : '',
                        v.categoria ? rotuloCategoria(v.categoria) : '',
                        [v.comarca, v.uf].filter(Boolean).join('/'),
                      ].filter(Boolean).join(' · ')}
                    </p>
                  </div>
                  {/* No celular, só a data: a hora custaria ~40px do título, que
                      é a única coluna que interessa numa tela estreita. */}
                  <span className="text-[12px] tabular shrink-0" style={{ color: 'var(--texto-fraco)' }}>
                    <span className="sm:hidden">{v.created_at.split(' ')[0]}</span>
                    <span className="hidden sm:inline">{v.created_at}</span>
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Cartao>
      </div>
    </>
  );
}

/**
 * Título da vaga. Linka para a MENSAGEM no grupo quando ela existe, e para
 * lugar nenhum quando não existe — nunca para a plataforma de origem.
 */
function TituloVaga({ titulo, link }: { titulo: string; link: string | null }) {
  if (!link) {
    return <span className="block text-[13.5px] font-medium truncate">{titulo}</span>;
  }
  return (
    // `py-2 -my-2` no toque: a linha de texto tem 20px de altura, e 20px é alvo
    // pequeno para o dedo. O par padding/margem negativa engorda a área
    // clicável para 36px sem mover um pixel do layout.
    <a
      href={link} target="_blank" rel="noopener noreferrer"
      title="Abrir a mensagem no grupo do Telegram"
      className="group flex items-center gap-1.5 text-[13.5px] font-medium min-w-0 hover:underline pointer-coarse:py-2 pointer-coarse:-my-2"
    >
      <span className="truncate">{titulo}</span>
      <IconeTelegram />
    </a>
  );
}

function IconeTelegram() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor"
         aria-hidden="true" className="shrink-0 opacity-45 group-hover:opacity-100"
         style={{ color: 'var(--acento)' }}>
      <path d="M21.9 4.3 18.8 19c-.2 1-.9 1.3-1.8.8l-4.9-3.6-2.4 2.3c-.3.3-.5.5-1 .5l.3-4.9 9-8.1c.4-.3-.1-.5-.6-.2L6.3 12.8 1.5 11.3c-1-.3-1.1-1 .2-1.5L20.5 2.6c.9-.3 1.6.2 1.4 1.7z" />
    </svg>
  );
}

function Periodo() {
  return <span className="text-[12px]" style={{ color: 'var(--texto-fraco)' }}>30 dias</span>;
}

async function Cabecalho({ cfg }: { cfg: Awaited<ReturnType<typeof lerConfig>> }) {
  const desligadas = Object.entries(cfg.sources)
    .filter(([, r]) => !r.enabled)
    .map(([nome]) => rotuloFonte(nome));

  return (
    <header className="flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-[22px] font-semibold tracking-tight">Visão geral</h1>
        <p className="mt-0.5 text-[13px]" style={{ color: 'var(--texto-suave)' }}>
          {new Date().toLocaleDateString('pt-BR', {
            weekday: 'long', day: '2-digit', month: 'long',
            timeZone: 'America/Sao_Paulo',
          })}
        </p>
      </div>
      {desligadas.length > 0 && (
        <p
          className="text-[12.5px] rounded-lg px-3 py-1.5"
          style={{
            color: 'var(--atencao)',
            background: 'color-mix(in srgb, var(--atencao) 12%, transparent)',
          }}
        >
          Fonte desligada: {desligadas.join(', ')}
        </p>
      )}
    </header>
  );
}
