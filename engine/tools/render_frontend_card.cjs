// Render the shared frontend card template with local assets only.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE_PATH || 'playwright');

async function main() {
  const [jsonPath, numberText, htmlPath, pngPath, fontPath] = process.argv.slice(2);
  const cardNumber = Number(numberText);
  if (!jsonPath || ![1, 2].includes(cardNumber) || !htmlPath || !pngPath || !fontPath) throw new Error('FRONTEND_CARD_ARGS_REQUIRED');
  const frontend = path.resolve(__dirname, '../../frontend');
  const templateSource = fs.readFileSync(path.join(frontend, 'card-template.js'), 'utf8');
  const { renderCardTemplate } = await import(`data:text/javascript;base64,${Buffer.from(templateSource).toString('base64')}`);
  const data = JSON.parse(fs.readFileSync(jsonPath, 'utf8'));
  const markup = renderCardTemplate(data, { cardNumber });
  const css = fs.readFileSync(path.join(frontend, 'styles.css'), 'utf8');
  const font = fs.readFileSync(fontPath).toString('base64');
  const html = `<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>@font-face{font-family:CardKorean;src:url(data:font/ttf;base64,${font})} :root{font-family:CardKorean,Arial,sans-serif}body{margin:0;background:#f3f6fa;padding:24px}.card{width:600px;margin:0 auto}.news-template{font-family:CardKorean,Arial,sans-serif}.news-template-title,.news-card-sentence,.news-term,.news-source-link{overflow-wrap:anywhere;word-break:break-word}.news-template-card{overflow-wrap:anywhere}</style><style>${css}</style></head><body><div class="card">${markup}</div></body></html>`;
  fs.writeFileSync(htmlPath, html);
  const candidates = [process.env.CARD_BROWSER_PATH,
    path.join(process.env.PROGRAMFILES || 'C:/Program Files', 'Google/Chrome/Application/chrome.exe'),
    path.join(process.env['PROGRAMFILES(X86)'] || 'C:/Program Files (x86)', 'Microsoft/Edge/Application/msedge.exe')];
  const executablePath = candidates.find(p => p && fs.existsSync(p));
  const browser = await chromium.launch({ headless: true, ...(executablePath ? { executablePath } : {}) });
  try {
    const context = await browser.newContext({ viewport: { width: 760, height: 1200 }, deviceScaleFactor: 1, javaScriptEnabled: false, locale: 'ko-KR', timezoneId: 'Asia/Seoul' });
    const requests = [];
    await context.route('**/*', route => { requests.push(route.request().url()); return route.abort(); });
    const page = await context.newPage();
    await page.setContent(html, { waitUntil: 'load' });
    await page.evaluate(async () => { await document.fonts.ready; });
    const layout = await page.evaluate(() => {
      const card = document.querySelector('.card'); const rect = card.getBoundingClientRect();
      const overflow = [...card.querySelectorAll('*')].filter(el => { const r = el.getBoundingClientRect(); return r.left < rect.left - 1 || r.right > rect.right + 1 || el.scrollWidth > el.clientWidth + 1; }).map(el => el.tagName + '.' + el.className);
      return { width: rect.width, height: Math.ceil(rect.height), overflow, fontLoaded: document.fonts.check('16px CardKorean') };
    });
    if (layout.overflow.length || !layout.fontLoaded || requests.length) throw new Error('FRONTEND_CARD_LAYOUT_CHECK_FAILED');
    await page.locator('.card').screenshot({ path: pngPath, type: 'png' });
    const bytes = fs.readFileSync(pngPath);
    process.stdout.write(JSON.stringify({ ...layout, bytes: bytes.length, sha256: crypto.createHash('sha256').update(bytes).digest('hex'), externalRequests: requests.length, renderer: 'playwright-frontend-template' }));
    await context.close();
  } finally { await browser.close(); }
}
main().catch(err => { process.stderr.write(err.message + '\n'); process.exitCode = 1; });
