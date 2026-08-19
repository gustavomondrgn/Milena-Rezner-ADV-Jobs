/**
 * Cria o primeiro usuário do painel a partir do ambiente, no boot.
 *
 * Existe pelo mesmo motivo da semeadura da sessão do Facebook: sem isto, subir
 * o painel exige alguém entrar no container e rodar `criar-usuario` na mão —
 * e um painel no ar que ninguém consegue abrir parece um bug de login.
 *
 * Regras:
 *   • só age se ADMIN_EMAIL e ADMIN_PASSWORD existirem;
 *   • `ON CONFLICT DO NOTHING`: NUNCA sobrescreve senha de usuário existente.
 *     Se o dono trocar a senha no banco e a variável ficar velha, quem manda é
 *     o banco;
 *   • falhar aqui não pode derrubar o painel — quem falha é a semeadura, não o
 *     servidor.
 */
import bcrypt from 'bcryptjs';
import pg from 'pg';

const email = (process.env.ADMIN_EMAIL || '').trim().toLowerCase();
const senha = process.env.ADMIN_PASSWORD || '';
const nome = (process.env.ADMIN_NAME || '').trim();
const url = process.env.DATABASE_URL;

if (!email || !senha || !url) process.exit(0);
if (senha.length < 8) {
  console.error('[semear-usuario] ADMIN_PASSWORD com menos de 8 caracteres — ignorado.');
  process.exit(0);
}

const dormir = (ms) => new Promise((r) => setTimeout(r, ms));

// O bot aplica o schema no boot e os dois containers sobem juntos: numa
// primeira subida a tabela `users` pode não existir ainda por alguns segundos.
async function esperarTabela(cliente, tentativas = 30) {
  for (let i = 0; i < tentativas; i++) {
    const { rows } = await cliente.query("SELECT to_regclass('public.users') AS t");
    if (rows[0].t) return true;
    await dormir(3000);
  }
  return false;
}

const cliente = new pg.Client({ connectionString: url });
try {
  await cliente.connect();
  if (!await esperarTabela(cliente)) {
    console.error('[semear-usuario] tabela users não apareceu — o bot aplica o '
      + 'schema no boot; se ele não subiu, o painel abre vazio de propósito.');
    process.exit(0);
  }
  const hash = await bcrypt.hash(senha, 12);
  const { rowCount } = await cliente.query(
    `INSERT INTO users (email, password_hash, name) VALUES ($1, $2, $3)
     ON CONFLICT (email) DO NOTHING`,
    [email, hash, nome],
  );
  console.log(rowCount
    ? `[semear-usuario] usuário ${email} criado.`
    : `[semear-usuario] usuário ${email} já existia — senha do banco preservada.`);
} catch (erro) {
  console.error('[semear-usuario] falhou (o painel sobe assim mesmo):', erro.message);
} finally {
  await cliente.end().catch(() => {});
}
