/* Run with NODE_PATH pointing at a directory containing playwright-core. */
const { chromium } = require('playwright-core');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const assert = require('node:assert/strict');

(async () => {
  const root = path.resolve(__dirname, '..');
  const results = path.join(root, 'test-results');
  fs.mkdirSync(results, { recursive: true });
  const temporary = fs.mkdtempSync(path.join(results, 'browser-'));
  const server = spawn('python', ['server.py', '--port', '0', '--db', path.join(temporary, 'test.sqlite3')], { cwd: root, windowsHide: true });
  let browser;
  try {
    const base = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('Test server startup timed out')), 12000);
      server.stdout.on('data', data => {
        const match = data.toString().match(/http:\/\/127\.0\.0\.1:\d+/);
        if (match) { clearTimeout(timer); resolve(match[0]); }
      });
      server.on('error', reject);
    });
    browser = await chromium.launch({ executablePath: process.env.BROWSER_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe', headless: true });
    const context = await browser.newContext({ viewport: { width: 1440, height: 1100 }, deviceScaleFactor: 1 });
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', err => errors.push(err.message));
    await page.goto(base);
    await page.waitForFunction(() => document.querySelector('#mode-label').textContent.includes('社区已连接'));
    assert.equal(await page.locator('.model-card').count(), 12);
    await page.screenshot({ path: path.join(results, 'desktop.png'), fullPage: true });

    await page.locator('#search').fill('深度求索');
    assert.equal(await page.locator('.model-card').count(), 1);
    assert.equal(await page.locator('.model-name').innerText(), 'DeepSeek');
    await page.locator('#search').fill('nothing-matches-123');
    assert.equal(await page.locator('.empty-state').count(), 1);
    await page.locator('[data-reset]').click();
    await page.locator('[data-category="image"]').click();
    assert.equal(await page.locator('.model-card').count(), 5);
    await page.locator('[data-type="family"]').click();
    assert.equal(await page.locator('.model-card').count(), 2);
    await page.locator('[data-nav="discover"]').click();
    await page.locator('[data-save="claude"]').click();
    await page.locator('#saved-nav').click();
    assert.equal(await page.locator('.model-card').count(), 1);
    await page.reload();
    await page.waitForFunction(() => document.querySelector('#mode-label').textContent.includes('社区已连接'));
    await page.locator('#saved-nav').click();
    assert.equal(await page.locator('.model-card').count(), 1);
    await page.locator('[data-nav="discover"]').click();
    await page.locator('.model-name [data-model="claude"]').click();
    await page.locator('[data-open-auth]').click();
    await page.locator('[data-auth-tab="register"]').click();
    await page.locator('#nickname').fill('体验测试员');
    await page.locator('#username').fill('browser_test');
    await page.locator('#password').fill('Test-Password-123');
    await page.locator('#auth-submit').click();
    await page.waitForSelector('#review-form');
    await page.locator('[data-rate="4"]').click();
    const content = '用于整理文章和解释代码，输出结构清晰。<img src=x onerror="window.xss=true">';
    await page.locator('#review-content').fill(content);
    await page.locator('#review-scenario').selectOption('编程开发');
    await page.locator('#review-version').fill('浏览器集成测试');
    await page.locator('#review-submit').click();
    await page.waitForSelector('#review-list .review-item');
    assert.equal(await page.locator('.score-number').innerText(), '4.0');
    assert.equal(await page.locator('.review-body').innerText(), content);
    assert.equal(await page.evaluate(() => window.xss), undefined);
    assert.equal(await page.locator('.review-body img').count(), 0);

    const anonContext = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 1 });
    const anon = await anonContext.newPage();
    anon.on('pageerror', err => errors.push(err.message));
    await anon.goto(base + '/#model/claude');
    await anon.waitForSelector('#review-list .review-item');
    assert.equal(await anon.locator('.review-body').innerText(), content);
    assert.equal(await anon.locator('[data-delete-prompt]').count(), 0);
    assert.equal(await anon.locator('[data-open-auth]').count(), 1);
    await anon.screenshot({ path: path.join(results, 'mobile-detail.png'), fullPage: true });
    await anon.locator('[data-close="detail-dialog"]').click();
    assert.ok(await anon.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), 'Mobile page overflows horizontally');
    await anon.screenshot({ path: path.join(results, 'mobile.png'), fullPage: true });

    // Another user posts while the first user has an unsaved draft open.
    const session = await (await anonContext.request.get(base + '/api/session')).json();
    const secondLogin = await anonContext.request.post(base + '/api/register', {
      headers: { Origin: base, 'X-CSRF-Token': session.csrf },
      data: { username: 'second_user', nickname: '第二位测试员', password: 'Test-Password-456' }
    });
    assert.equal(secondLogin.status(), 200);
    const secondSession = await secondLogin.json();
    await page.locator('#review-content').fill('还没有提交的草稿，应该在新点评到来后继续保留。');
    await page.locator('[data-rate="2"]').click();
    const secondReview = await anonContext.request.post(base + '/api/reviews', {
      headers: { Origin: base, 'X-CSRF-Token': secondSession.csrf },
      data: { model_id: 'claude', rating: 3, content: '第二个账号的真实测试内容，用于验证自动刷新与草稿保留。', scenario: '内容写作', version: '' }
    });
    assert.equal(secondReview.status(), 200);
    await page.evaluate(() => refreshReviews());
    assert.equal(await page.locator('#review-list .review-item').count(), 2);
    assert.equal(await page.locator('.score-number').innerText(), '3.5');
    assert.equal(await page.locator('#review-content').inputValue(), '还没有提交的草稿，应该在新点评到来后继续保留。');
    assert.equal(await page.locator('[data-rate="2"]').getAttribute('aria-pressed'), 'true');
    await anonContext.request.delete(base + '/api/reviews/claude', { headers: { Origin: base, 'X-CSRF-Token': secondSession.csrf } });
    await page.evaluate(() => refreshReviews());
    await anonContext.request.post(base + '/api/logout', { headers: { Origin: base, 'X-CSRF-Token': secondSession.csrf }, data: {} });

    await page.route('**/api/reviews', route => route.abort());
    await page.evaluate(() => refreshReviews());
    assert.equal(await page.locator('#connection-banner').isVisible(), true);
    assert.ok((await page.locator('#mode-label').innerText()).includes('连接中断'));
    await page.unroute('**/api/reviews');
    await page.evaluate(() => refreshReviews());
    assert.equal(await page.locator('#connection-banner').isVisible(), false);

    await page.locator('[data-rate="5"]').click();
    await page.locator('#review-content').fill('更新后的使用体验：阅读长文档很方便，代码解释也很清楚。');
    await page.locator('#review-submit').click();
    await page.waitForFunction(() => document.querySelector('.score-number').textContent === '5.0');
    assert.equal(await page.locator('#review-list .review-item').count(), 1);
    await page.locator('[data-delete-prompt]').click();
    await page.locator('[data-confirm-delete]').click();
    await page.waitForSelector('.review-empty');
    assert.equal(await page.locator('.score-number').innerText(), '—');
    await page.locator('[data-close="detail-dialog"]').click();
    await page.locator('#auth-button').click();
    await page.locator('#logout-button').click();
    await page.waitForFunction(() => document.querySelector('#auth-button').textContent.includes('登录 / 注册'));

    await anon.goto(base);
    await anon.waitForFunction(() => document.querySelector('#mode-label').textContent.includes('社区已连接'));
    await anon.screenshot({ path: path.join(results, 'mobile.png'), fullPage: true });
    await page.locator('[data-view="list"]').click();
    assert.equal(await page.locator('.list-view .model-card').count(), 12);
    await page.locator('[data-view="grid"]').click();
    await page.locator('[data-nav="ranking"]').click();
    assert.equal(await page.locator('.empty-state').count(), 1);

    const local = await context.newPage();
    local.on('pageerror', err => errors.push(err.message));
    await local.goto(pathToFileURL(path.join(root, 'index.html')).href);
    await local.waitForFunction(() => document.querySelector('#mode-label').textContent.includes('本地预览'));
    await local.locator('.model-name [data-model="deepseek"]').click();
    await local.locator('[data-open-auth]').click();
    assert.equal(await local.locator('#password').isVisible(), false);
    await local.locator('#nickname').fill('本地体验者');
    await local.locator('#auth-submit').click();
    await local.waitForSelector('#review-form');
    await local.locator('[data-rate="3"]').click();
    await local.locator('#review-content').fill('这是保存在本地浏览器的体验点评，用于验证页面功能。');
    await local.locator('#review-submit').click();
    await local.waitForSelector('#review-list .review-item');
    await local.reload();
    await local.waitForSelector('#review-list .review-item');
    assert.equal(await local.locator('.score-number').innerText(), '3.0');
    assert.equal(errors.length, 0, errors.join('\n'));
    console.log('PASS: search, category/type filters, favorites persistence, real registration, public anonymous reviews, XSS escaping, live refresh with draft preservation, connection failure recovery, edit/delete, logout, list view, empty ranking, mobile layout, standalone preview persistence.');
    console.log('Screenshots: test-results/desktop.png, mobile.png, mobile-detail.png');
  } finally {
    if (browser) await browser.close();
    server.kill();
  }
})().catch(err => { console.error(err); process.exitCode = 1; });
