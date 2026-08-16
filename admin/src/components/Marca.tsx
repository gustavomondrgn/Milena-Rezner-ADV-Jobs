/**
 * A marca do ADV Jobs — o ramo de oliveira, com as cores ligadas ao tema.
 *
 * Por que oliveira: é o elemento que já existe na identidade da Milena — a
 * oliveira no vaso de travertino é o objeto central da foto de capa do site
 * dela. Vale mais que qualquer símbolo jurídico de catálogo, e evita de saída
 * o balcão de clichês (balança, martelo, coluna) que faz todo escritório
 * parecer o mesmo escritório.
 *
 * Duas cores em tokens, e não fixas, porque o painel tem tema claro e escuro:
 *
 *   --marca-ouro   → o latão do ramo (mesmo ouro nos dois temas)
 *   --marca-tinta  → o contorno/fundo: espresso no claro, osso no escuro
 *
 * Desenhado para sobreviver ao tamanho pequeno: formas cheias, sem traço fino,
 * sem detalhe interno. A 20px continua sendo um ramo.
 */

const FOLHA = 'M0 0 C 4 -4.2 11 -4.2 16 0 C 11 4.2 4 4.2 0 0 Z';
const FOLHA_MENOR = 'M0 0 C 3.4 -3.6 9.4 -3.6 13.6 0 C 9.4 3.6 3.4 3.6 0 0 Z';

export function Marca({ tamanho = 28 }: { tamanho?: number }) {
  return (
    <svg
      width={tamanho}
      height={tamanho}
      viewBox="0 0 48 48"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
      style={{ flexShrink: 0, display: 'block' }}
    >
      {/* Haste */}
      <path
        d="M12 40 C 17 31 24 22 33 15"
        stroke="var(--marca-ouro)"
        strokeWidth="3.2"
        strokeLinecap="round"
      />
      <g fill="var(--marca-ouro)">
        {/* Duas folhas para cima, uma contrabalançando embaixo — assimetria de
            propósito: um ramo simétrico vira brasão. */}
        <path d={FOLHA} transform="translate(24,23) rotate(-46)" />
        <path d={FOLHA} transform="translate(18,32) rotate(-10)" />
        <path d={FOLHA_MENOR} transform="translate(19,31) rotate(197)" />
        {/* A azeitona, no alto da haste. */}
        <circle cx="36" cy="11.5" r="4.2" />
      </g>
    </svg>
  );
}

export function MarcaCompleta({ tamanho = 30 }: { tamanho?: number }) {
  return (
    <span className="flex items-center gap-2.5" style={{ color: 'var(--texto)' }}>
      <Marca tamanho={tamanho} />
      <span className="leading-none">
        {/* Mesma hierarquia do site dela: o nome em cima, a linha de serviço
            embaixo em caixa alta com entreletra larga. */}
        <span className="block text-[15px] font-bold tracking-tight">Milena Rezner</span>
        <span
          className="block text-[10px] font-medium tracking-[0.16em] uppercase mt-0.5"
          style={{ color: 'var(--acento)' }}
        >
          ADV Jobs
        </span>
      </span>
    </span>
  );
}
