import type { Metadata, Viewport } from 'next';
import { SCRIPT_ANTI_FLASH } from '@/components/Tema';
import './globals.css';

export const metadata: Metadata = {
  title: 'Milena Rezner · ADV Jobs',
  description: 'Painel do robô que garimpa demandas jurídicas em grupos do Facebook',
  // Painel interno: não deve aparecer em buscador nenhum.
  robots: { index: false, follow: false },
};

export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  themeColor: [
    { media: '(prefers-color-scheme: light)', color: '#faf8f5' },
    { media: '(prefers-color-scheme: dark)', color: '#16110e' },
  ],
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="pt-BR" suppressHydrationWarning>
      <head>
        {/* Aplica o tema salvo antes da primeira pintura — sem isto, quem usa
            tema escuro vê um flash branco a cada navegação. */}
        <script dangerouslySetInnerHTML={{ __html: SCRIPT_ANTI_FLASH }} />
      </head>
      <body>{children}</body>
    </html>
  );
}
