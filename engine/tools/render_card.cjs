// 로컬 HTML만 렌더링. 페이지의 네트워크와 JavaScript는 사용하지 않는다.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE_PATH || 'playwright');

async function main() {
  const [htmlPath, pngPath] = process.argv.slice(2);
  if (!htmlPath || !pngPath) throw new Error('HTML_AND_PNG_PATH_REQUIRED');
  const candidates = [process.env.CARD_BROWSER_PATH,
    path.join(process.env.PROGRAMFILES || 'C:/Program Files', 'Google/Chrome/Application/chrome.exe'),
    path.join(process.env['PROGRAMFILES(X86)'] || 'C:/Program Files (x86)', 'Microsoft/Edge/Application/msedge.exe')];
  const executablePath = candidates.find(p => p && fs.existsSync(p));
  const browser = await chromium.launch({ headless: true, ...(executablePath ? { executablePath } : {}) });
  try {
    const context = await browser.newContext({ viewport: { width: 1080, height: 1600 },
      deviceScaleFactor: 1, javaScriptEnabled: false, locale: 'ko-KR', timezoneId: 'Asia/Seoul' });
    const requests = [];
    await context.route('**/*', route => { requests.push(route.request().url()); return route.abort(); });
    const page = await context.newPage();
    await page.setContent(fs.readFileSync(htmlPath, 'utf8'), { waitUntil: 'load' });
    await page.evaluate(async () => { await document.fonts.ready; });
    const layout = await page.evaluate(() => {
      const card = document.querySelector('.news-template');
      const rect = card.getBoundingClientRect();
      const overflow = [...card.querySelectorAll('*')].filter(el => {
        const r = el.getBoundingClientRect();
        return r.left < rect.left - 1 || r.right > rect.right + 1 || r.top < rect.top - 1 || r.bottom > rect.bottom + 1
          || el.scrollWidth > el.clientWidth + 1;
      }).map(el => el.tagName + '.' + el.className);
      return { width: rect.width, height: Math.ceil(rect.height), overflow,
        fontLoaded: document.fonts.check('31px CardKorean'),
        minimumTextPx: 2 * Math.min(...[...card.querySelectorAll('h1,p,a,.news-template-label,.news-template-ai')].map(el => parseFloat(getComputedStyle(el).fontSize))) };
    });
    if (layout.overflow.length || !layout.fontLoaded || requests.length) throw new Error('LAYOUT_FONT_OR_NETWORK_CHECK_FAILED');
    await page.locator('.news-template').screenshot({ path: pngPath, type: 'png' });
    const bytes = fs.readFileSync(pngPath);
    process.stdout.write(JSON.stringify({ ...layout, bytes: bytes.length,
      sha256: crypto.createHash('sha256').update(bytes).digest('hex'), externalRequests: requests.length,
      browserVersion: browser.version(), renderer: 'playwright', rendererVersion: require((process.env.PLAYWRIGHT_MODULE_PATH || 'playwright') + '/package.json').version }));
  } finally { await browser.close(); }
}
main().catch(err => { process.stderr.write(err.message + '\n'); process.exitCode = 1; });
