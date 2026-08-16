/**
 * Tipos, constantes e validação da configuração do bot.
 *
 * Este arquivo é deliberadamente **puro**: nenhum import de banco, nada de
 * `server-only`. O formulário de configurações roda no navegador e precisa dos
 * tipos e da lista de fontes; se eles morassem junto com `lerConfig`, o driver
 * do Postgres seria arrastado para o bundle do cliente — o build quebra, e se
 * não quebrasse seria pior ainda.
 *
 * Leitura e escrita ficam em `config.ts`, que importa daqui.
 */

/**
 * Só o Facebook aparece aqui, e isso é uma escolha.
 *
 * O código ainda tem Gupy, Indeed e LinkedIn, testados no projeto anterior, mas
 * eles não estão em `SOURCES` — ou seja, o bot nem os instancia. Listá-los no
 * painel produziria um interruptor que não liga nada, que é pior do que não
 * existir: quem clicasse ficaria esperando vagas que nunca viriam.
 *
 * Para religar um deles: acrescentar em `SOURCES`, descomentar `python-jobspy`
 * no requirements.txt e só então trazê-lo para esta lista.
 */
export const FONTES = [
  { nome: 'facebook', rotulo: 'Facebook' },
] as const;

export type NomeFonte = (typeof FONTES)[number]['nome'];

/**
 * As áreas do direito que o classificador sabe atribuir.
 *
 * Precisa bater EXATAMENTE com `CATEGORIAS` em `bot/main.py` — é uma lista
 * fechada dos dois lados. Se divergir, uma área desligada aqui continua
 * chegando ao grupo, sem erro nenhum aparecer em lugar nenhum.
 */
export const CATEGORIAS = [
  { nome: 'imobiliario', rotulo: 'Imobiliário',
    ajuda: 'Usucapião, despejo, compra e venda, vícios construtivos, renovatória' },
  { nome: 'condominio', rotulo: 'Condomínio',
    ajuda: 'Assessoria a condomínio, cota condominial, convenção, assembleia' },
  { nome: 'empresarial', rotulo: 'Empresarial',
    ajuda: 'Societário, contrato social, sócios, compliance e LGPD' },
  { nome: 'contratos', rotulo: 'Contratos',
    ajuda: 'Elaboração e revisão, notificação extrajudicial, distrato' },
  { nome: 'cobranca', rotulo: 'Cobrança',
    ajuda: 'Ação de cobrança, execução, monitória, inadimplência' },
  { nome: 'trabalhista_empresa', rotulo: 'Trabalhista (empresa)',
    ajuda: 'Só o lado da empresa: defesa, passivo, consultoria preventiva' },
  { nome: 'tributario_fiscal', rotulo: 'Tributário / Fiscal',
    ajuda: 'Execução fiscal, auto de infração, IPTU e ITBI de imóvel' },
  { nome: 'outro', rotulo: 'Outras',
    ajuda: 'É da área dela, mas não encaixa em nenhuma família acima' },
] as const;

export type NomeCategoria = (typeof CATEGORIAS)[number]['nome'];

export function rotuloCategoria(nome: string): string {
  return CATEGORIAS.find((c) => c.nome === nome)?.rotulo ?? nome;
}

/** Como o post chegou. Espelha `tipo_demanda` em `bot/main.py`. */
export const TIPOS_DEMANDA = [
  { nome: 'lead_cliente', rotulo: 'Cliente direto' },
  { nome: 'parceria_advogado', rotulo: 'Parceria' },
  { nome: 'vaga_emprego', rotulo: 'Vaga de emprego' },
  { nome: 'divulgacao', rotulo: 'Divulgação' },
  { nome: 'nao_informado', rotulo: 'Não identificado' },
] as const;

export function rotuloTipoDemanda(nome: string): string {
  return TIPOS_DEMANDA.find((t) => t.nome === nome)?.rotulo ?? nome;
}

export const UFS = [
  'AC', 'AL', 'AP', 'AM', 'BA', 'CE', 'DF', 'ES', 'GO', 'MA', 'MT', 'MS',
  'MG', 'PA', 'PB', 'PR', 'PE', 'PI', 'RJ', 'RN', 'RS', 'RO', 'RR', 'SC',
  'SP', 'SE', 'TO',
] as const;

export type RegrasFonte = {
  enabled: boolean;
  /** `null` = herda a regra geral. */
  rejeitar_divulgacao: boolean | null;
  reject_english: boolean | null;
  aceitar_sem_local: boolean | null;
  min_score: number | null;
  /** Área ausente = ligada. Só o que foi desligado precisa ser gravado. */
  categorias: Record<string, boolean>;
};

export type ConfigBot = {
  /** 0 = SEM TETO. É o padrão deste projeto. */
  daily_limit: number;
  window_start: number;
  window_end: number;
  min_score: number;
  reject_english: boolean;
  rejeitar_divulgacao: boolean;
  aceitar_sem_local: boolean;
  /** Vazio = aceita qualquer UF. */
  ufs_atendidas: string[];
  sources: Record<string, RegrasFonte>;
};

export const PADRAO: ConfigBot = {
  daily_limit: 0,
  window_start: 6,
  window_end: 23,
  min_score: 0,
  reject_english: true,
  rejeitar_divulgacao: true,
  aceitar_sem_local: true,
  // Do rodapé do site da Milena: os estados que o escritório atende.
  ufs_atendidas: ['SP', 'RJ', 'SC', 'PR', 'RS', 'MG', 'BA', 'PE', 'DF', 'GO'],
  sources: Object.fromEntries(
    FONTES.map((f) => [f.nome, {
      enabled: true,
      rejeitar_divulgacao: null,
      reject_english: null,
      aceitar_sem_local: null,
      min_score: null,
      categorias: Object.fromEntries(CATEGORIAS.map((c) => [c.nome, true])),
    }]),
  ),
};

/** Limites de sanidade: o formulário é do dono, mas o banco não confia nele. */
function faixa(n: unknown, min: number, max: number, padrao: number): number {
  const v = Number(n);
  if (!Number.isFinite(v)) return padrao;
  return Math.min(max, Math.max(min, Math.round(v)));
}

export function sanear(bruto: Partial<ConfigBot>): ConfigBot {
  const inicio = faixa(bruto.window_start, 0, 23, PADRAO.window_start);
  // A janela precisa ter pelo menos uma hora, senão o bot nunca publica e o
  // sintoma ("o grupo morreu") não aponta para a causa.
  const fim = faixa(bruto.window_end, inicio + 1, 24, Math.max(inicio + 1, PADRAO.window_end));

  const fontes: Record<string, RegrasFonte> = {};
  for (const f of FONTES) {
    const r = (bruto.sources ?? {})[f.nome] ?? ({} as Partial<RegrasFonte>);
    fontes[f.nome] = {
      enabled: r.enabled !== false,
      rejeitar_divulgacao: r.rejeitar_divulgacao ?? null,
      reject_english: r.reject_english ?? null,
      aceitar_sem_local: r.aceitar_sem_local ?? null,
      min_score: r.min_score == null ? null : faixa(r.min_score, 0, 100, 0),
      // Normaliza para o conjunto conhecido: área que sumiu do código não fica
      // presa no banco, e área nova nasce ligada.
      categorias: Object.fromEntries(
        CATEGORIAS.map((c) => [c.nome, (r.categorias ?? {})[c.nome] !== false]),
      ),
    };
  }

  // Só siglas de UF de verdade. O bot descarta demanda com base nesta lista, e
  // um "S" digitado errado que sobrevivesse até aqui não filtraria nada — mas
  // uma UF inventada faria a regra rejeitar silenciosamente o que devia passar.
  const ufs = Array.from(
    new Set((bruto.ufs_atendidas ?? []).map((u) => String(u).toUpperCase().trim())),
  ).filter((u) => (UFS as readonly string[]).includes(u));

  return {
    // Mínimo ZERO, não 1: zero é o valor que significa "sem teto", e é o padrão
    // deste projeto. Com mínimo 1 seria impossível desligar o teto pelo painel.
    daily_limit: faixa(bruto.daily_limit, 0, 200, PADRAO.daily_limit),
    window_start: inicio,
    window_end: fim,
    min_score: faixa(bruto.min_score, 0, 100, 0),
    reject_english: bruto.reject_english !== false,
    rejeitar_divulgacao: bruto.rejeitar_divulgacao !== false,
    aceitar_sem_local: bruto.aceitar_sem_local !== false,
    ufs_atendidas: ufs,
    sources: fontes,
  };
}

export function rotuloFonte(nome: string): string {
  return FONTES.find((f) => f.nome === nome)?.rotulo ?? nome;
}
