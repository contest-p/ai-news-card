// Execute only the trusted frontend template in Node; rendered pages contain no JS.
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../..');

async function main() {
  const input = JSON.parse(fs.readFileSync(0, 'utf8'));
  if (![1, 2].includes(input.index)) throw new Error('CARD_INDEX_INVALID');
  const templateSource = fs.readFileSync(path.join(root, 'frontend/card-template.js'), 'utf8');
  const { renderCardTemplate } = await import('data:text/javascript;base64,' + Buffer.from(templateSource).toString('base64'));
  const markup = renderCardTemplate(input.data).replace(
    /<article class="news-template-card" data-card-number="([12])"[^>]*>[\s\S]*?<\/article>/g,
    (html, number) => Number(number) === input.index ? html : '');
  const css = fs.readFileSync(path.join(root, 'frontend/styles.css'), 'utf8');
  if (/@import|url\(/i.test(css)) throw new Error('EXTERNAL_STYLES_NOT_ALLOWED');
  if (!/^[A-Za-z0-9+/=]+$/.test(input.font_base64)) throw new Error('FONT_REQUIRED');
  const title = String(input.data.title).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c]);
  process.stdout.write(`<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>${title}</title><style>
${css}
@font-face{font-family:CardKorean;src:url(data:font/ttf;base64,${input.font_base64})}
:root{font-family:CardKorean,sans-serif}body{margin:0;background:#fff}
.card{width:1080px;border:0;border-radius:0;padding:0;box-shadow:none}
.card .news-template{zoom:2;width:540px;margin:0;border-radius:0;box-shadow:none}
.card .news-template-header{flex-wrap:wrap}.card .news-card-date{white-space:normal}
</style></head><body><main class="card">${markup}</main></body></html>`);
}
main().catch(() => { process.stderr.write('FRONTEND_TEMPLATE_BUILD_FAILED\n'); process.exitCode=1; });
