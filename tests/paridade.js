// Confere que a conta do site (JS) dá o MESMO resultado que app/services/ia_risco.py (Python).
// Uso:  node tests/paridade.js site/index.html casos.json   (casos.json é gerado por tests/gerar_casos.py)
const fs = require('fs');
const html = fs.readFileSync(process.argv[2], 'utf8');
const ini = html.indexOf('/*MODELO-INI'), fim = html.indexOf('/*MODELO-FIM*/');
if (ini < 0 || fim < 0) { console.error('bloco do modelo não encontrado'); process.exit(2); }
const bloco = html.slice(html.indexOf('\n', ini), fim);
const { modelo, casos } = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const f = new Function(bloco + '; return { pontuar, modeloValido, sintomasParaVariaveis };')();
if (!f.modeloValido(modelo)) { console.error('modelo inválido para o JS'); process.exit(2); }
let maior = 0, ruins = 0;
casos.forEach((c) => {
  const p = f.pontuar(modelo, c.sintomas, c.idade, c.sexo);
  const d = Math.abs(p - c.esperado);
  maior = Math.max(maior, d);
  if (d > 1e-9) { ruins++; if (ruins < 5) console.error('DIFERE', JSON.stringify(c), p); }
});
console.log(`${casos.length} casos, maior diferença JS x Python = ${maior.toExponential(2)}`);
process.exit(ruins ? 1 : 0);
