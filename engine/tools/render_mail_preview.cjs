// 실제 메일 클라이언트 시험 전, 로컬 HTML을 데스크톱·모바일 폭에서 확인한다.
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE_PATH || 'playwright');

async function main() {
  const root = process.argv[2];
  if (!root) throw new Error('MAIL_PREVIEW_DIRECTORY_REQUIRED');
  const report = JSON.parse(fs.readFileSync(path.join(root, 'mail_result.json'), 'utf8'));
  const expectedText = { news_card_1: '오늘의 핵심', news_card_2: '오늘의 핵심', no_news: '오늘은 새 브리핑이 없습니다',
    end_notice: '뉴스 브리핑 구독이 종료되었습니다' }[report.content_kind];
  if (!expectedText) throw new Error('MAIL_CONTENT_KIND_INVALID');
  const candidates = [process.env.CARD_BROWSER_PATH,
    'C:/Program Files/Google/Chrome/Application/chrome.exe',
    'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'];
  const executablePath = candidates.find(p => p && fs.existsSync(p));
  const browser = await chromium.launch({ headless: true, ...(executablePath ? { executablePath } : {}) });
  try {
    const results = [];
    for (const width of [800, 390]) {
      for (const file of ['preview.html', 'images-blocked.html']) {
        const context = await browser.newContext({ viewport: { width, height: 1000 }, javaScriptEnabled: false });
        let externalRequests = 0;
        await context.route('**/*', route => { externalRequests++; return route.abort(); });
        const page = await context.newPage();
        await page.setContent(fs.readFileSync(path.join(root, file), 'utf8'), { waitUntil: 'load' });
        await page.evaluate(async () => { await document.fonts.ready; });
        const layout = await page.evaluate((expected) => ({ scrollWidth: document.documentElement.scrollWidth,
          viewportWidth: innerWidth, imageCount: document.images.length,
          missingImages: [...document.images].filter(img => !img.complete || !img.naturalWidth).length,
          textVisible: document.body.innerText.includes(expected),
          sourceLinkCount: document.querySelectorAll('a').length }), expectedText);
        if (layout.scrollWidth > width || layout.missingImages || externalRequests || !layout.textVisible) throw new Error('MAIL_LAYOUT_CHECK_FAILED');
        const png = path.join(root, `${file.replace('.html', '')}-${width}.png`);
        await page.screenshot({ path: png, fullPage: true });
        results.push({ ...layout, externalRequests, file, screenshot: png });
        await context.close();
      }
    }
    fs.writeFileSync(path.join(root, 'layout_result.json'), JSON.stringify({ browserPreviewOnly: true, results }, null, 2));
    process.stdout.write(JSON.stringify(results, null, 2));
  } finally { await browser.close(); }
}
main().catch(err => { process.stderr.write(err.message + '\n'); process.exitCode = 1; });
