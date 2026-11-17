// Deterministic real-browser checks. Start backend/tests/browser_app.py on port 8123 first.
import { createRequire } from 'node:module';
import { existsSync } from 'node:fs';
import { mkdir, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const require = createRequire(path.join(root, 'frontend/package.json'));
const { chromium, expect } = require('@playwright/test');
const chrome = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const browser = await chromium.launch({ headless: true, executablePath: existsSync(chrome) ? chrome : undefined });
const baseURL = process.env.TEST_BASE_URL || 'http://127.0.0.1:8123';
const output = path.join(root, 'state/improve-v3/browser');
await mkdir(output, { recursive: true });
const checks = [], errors = [];
try {
  for (const viewport of [{width:1440,height:1000},{width:390,height:844}]) {
    const context = await browser.newContext({ baseURL, viewport });
    const page = await context.newPage();
    page.on('pageerror', e => errors.push(e.message));
    await page.goto('/');
    if (viewport.width < 600) await page.getByRole('button', {name:'打开对话导航'}).click();
    await page.locator('.catalog-nav').click();
    const search = page.getByRole('textbox', {name:'搜索古树目录'});
    for (const [query, count, text] of [['吴中203',1,'石楠'],['穹窿山',2,'海云院'],['甪直',3,'瑶盛村'],['三官殿',1,'002'],['灵源寺',1,'罗汉松'],['清奇古怪',1,'圆柏']]) {
      await search.fill(query);
      await expect(page.locator('.catalog-card')).toHaveCount(count);
      await expect(page.locator('.catalog-grid')).toContainText(text);
    }
    await search.fill(' 银杏 ');
    expect(await page.locator('.catalog-card').count()).toBeGreaterThan(1);
    await page.getByRole('combobox', {name:'按树种筛选'}).selectOption('罗汉松');
    await expect(page.locator('.catalog-card')).toHaveCount(0);
    await search.fill('');
    expect(await page.locator('.catalog-card').count()).toBeGreaterThan(0);
    await expect(page.locator('.catalog-card').first()).toContainText('罗汉松');
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({path:path.join(output, `catalog-${viewport.width}.png`)});
    checks.push(`目录搜索、树种筛选及布局 ${viewport.width}px`);
    await page.getByRole('button', {name:'关闭古树目录'}).click();
    const input = page.getByRole('textbox', {name:'向树语提问'});
    let gets = 0;
    page.on('request', request => { if (request.method() === 'GET' && /\/conversations\/[^/]+\/messages$/.test(request.url())) gets++; });
    await input.fill('正常完成');
    await page.getByRole('button', {name:'发送问题'}).click();
    await expect(page.getByRole('button', {name:'停止回答'})).toBeEnabled();
    await page.waitForTimeout(4500);
    expect(gets).toBe(0);
    await expect(input).toBeEnabled({timeout:10000});
    await expect(page.locator('.answer-body')).toContainText('1500');
    expect(gets).toBe(1); // Final reconciliation only; no periodic GET while streaming.
    checks.push(`SSE 连接正常时无双重轮询 ${viewport.width}px`);
    await page.getByRole('button', {name:'反馈回答问题', exact:true}).click();
    await page.getByRole('textbox', {name:'补充说明（可选）'}).fill('保留过的备注');
    await page.getByRole('button', {name:'记录反馈'}).click();
    await page.getByRole('button', {name:'回答有帮助', exact:true}).click();
    await expect(page.getByRole('button', {name:'回答有帮助', exact:true})).toHaveClass('rated');
    await page.getByRole('button', {name:'反馈回答问题', exact:true}).click();
    await expect(page.getByRole('textbox', {name:'补充说明（可选）'})).toHaveValue('');
    await page.keyboard.press('Escape');
    checks.push(`好评清除旧备注 ${viewport.width}px`);
    await input.fill('取消中的问题');
    await page.getByRole('button', {name:'发送问题'}).click();
    await expect(page.getByRole('button', {name:'停止回答'})).toBeEnabled();
    const started = Date.now();
    await page.getByRole('button', {name:'停止回答'}).click();
    await expect(input).toBeEnabled({timeout:1000});
    expect(Date.now() - started).toBeLessThan(1000);
    await expect(page.locator('.message.assistant').last()).toContainText('已停止本次回答');
    await input.fill('你好');
    await page.getByRole('button', {name:'发送问题'}).click();
    await expect(input).toBeEnabled();
    checks.push(`停止后立即可提问 ${viewport.width}px`);
    await input.fill('刷新恢复中的问题');
    await page.getByRole('button', {name:'发送问题'}).click();
    await expect(page.getByRole('button', {name:'停止回答'})).toBeEnabled();
    await page.reload();
    await expect(page.getByRole('button', {name:'停止回答'})).toBeEnabled();
    const beforePoll = gets;
    await page.waitForTimeout(4500);
    expect(gets).toBeGreaterThan(beforePoll);
    await page.getByRole('button', {name:'停止回答'}).click();
    await expect(input).toBeEnabled({timeout:1000});
    checks.push(`刷新后轮询恢复与停止 ${viewport.width}px`);
    await page.screenshot({path:path.join(output, `chat-${viewport.width}.png`)});
    // Hold the old stream's final GET across a cancellation and a new chat.
    let holdFirst = true, releaseOld;
    await page.route('**/api/conversations/*/messages', async route => {
      if (route.request().method() === 'GET' && holdFirst) {
        holdFirst = false;
        const response = await route.fetch();
        await new Promise(resolve => { releaseOld = async () => { await route.fulfill({response}); resolve(); }; });
      } else await route.continue();
    });
    await input.fill('正常完成');
    await page.getByRole('button', {name:'发送问题'}).click();
    await expect.poll(() => !!releaseOld, {timeout:10000}).toBe(true);
    await page.getByRole('button', {name:'停止回答'}).click();
    await expect(input).toBeEnabled({timeout:1000});
    if (viewport.width < 600) await page.getByRole('button', {name:'打开对话导航'}).click();
    await page.getByRole('button', {name:'开启新的对话'}).click();
    await input.fill('新会话草稿');
    await releaseOld();
    await page.waitForTimeout(150);
    await expect(page.locator('.message')).toHaveCount(0);
    await expect(input).toHaveValue('新会话草稿');
    checks.push(`旧会话延迟响应不会覆盖新会话 ${viewport.width}px`);

    await page.unroute('**/api/conversations/*/messages');
    // The request and its first recovery GET both fail before accepted arrives.
    let recoveryOffline = true;
    await page.route('**/api/conversations/*/messages', async route => {
      if (route.request().method() === 'POST' || recoveryOffline) await route.abort('internetdisconnected');
      else await route.continue();
    });
    await input.fill('断网前没有接受的草稿');
    await page.getByRole('button', {name:'发送问题'}).click();
    await expect(input).toHaveValue('断网前没有接受的草稿');
    recoveryOffline = false;
    await expect(input).toBeEnabled({timeout:6500});
    await expect(input).toHaveValue('断网前没有接受的草稿');
    checks.push(`未收到 accepted 的草稿断网恢复 ${viewport.width}px`);
    await page.unroute('**/api/conversations/*/messages');

    // A stale accepted event is insufficient proof of persistence: after
    // polling, restore the question if it is absent from the server history.
    recoveryOffline = true;
    let receivedRecoveryGet = false;
    await page.route('**/api/conversations/*/messages', async route => {
      if (route.request().method() === 'POST') {
        await route.fulfill({status:200, contentType:'text/event-stream', body:'event: accepted\ndata: {}\n\n'});
      } else if (recoveryOffline) {
        receivedRecoveryGet = true;
        await route.abort('internetdisconnected');
      } else await route.continue();
    });
    await input.fill('恢复时服务端未保存的草稿');
    await page.getByRole('button', {name:'发送问题'}).click();
    await expect.poll(() => receivedRecoveryGet).toBe(true);
    recoveryOffline = false;
    await expect(input).toBeEnabled({timeout:6500});
    await expect(input).toHaveValue('恢复时服务端未保存的草稿');
    checks.push(`恢复轮询发现问题未保存时还原草稿 ${viewport.width}px`);
    await page.unroute('**/api/conversations/*/messages');
    if (viewport.width < 600) await page.getByRole('button', {name:'打开对话导航'}).click();
    await page.getByRole('button', {name:'开启新的对话'}).click();
    await page.route('**/api/conversations', async route => {
      if (route.request().method() === 'POST') await route.abort('internetdisconnected');
      else await route.continue();
    });
    await input.fill('创建对话前断网的草稿');
    await page.getByRole('button', {name:'发送问题'}).click();
    await expect(input).toBeEnabled();
    await expect(input).toHaveValue('创建对话前断网的草稿');
    checks.push(`创建对话失败保留草稿 ${viewport.width}px`);
    await page.screenshot({path:path.join(output, `offline-${viewport.width}.png`)});

    await context.close();
  }
  expect(errors).toEqual([]);
  await writeFile(path.join(output,'checks.json'),JSON.stringify({passed:true,checks,errors},null,2));
  console.log(JSON.stringify({passed:true,checks},null,2));
} finally {
  await browser.close();
}
