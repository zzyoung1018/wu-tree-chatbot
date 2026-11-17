// Real browser + real API integration. Run while the local server is up.
import { createRequire } from 'node:module';
import { existsSync } from 'node:fs';
import { mkdir, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const require = createRequire(path.join(root, 'frontend/package.json'));
const { chromium, expect } = require('@playwright/test');
const localChrome = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const executablePath = process.env.CHROME_PATH || (existsSync(localChrome) ? localChrome : undefined);
const browser = await chromium.launch({ headless: true, executablePath });
const baseURL = process.env.TEST_BASE_URL || 'http://127.0.0.1:5173';
const output = path.join(root, 'state/browser');
await mkdir(output, { recursive: true });
const results = [];
let ownedContext;
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, baseURL });
  ownedContext = context;
  const page = await context.newPage();
  const errors = [], external = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => { if (!request.url().startsWith(baseURL) && !request.url().startsWith('data:')) external.push(request.url()); });
  await page.goto('/');
  await expect(page.getByRole('heading', { name: '一棵树， 一段吴中往事。' })).toBeVisible();
  await expect(page.locator('.hero-card')).toHaveCount(3);
  await expect(page.locator('.review-badge')).toHaveCount(0);
  await expect(page.locator('body')).not.toContainText('现场核实');
  await page.screenshot({ path: path.join(output, 'desktop.png'), fullPage: true });
  await page.getByRole('button', { name: /01 三官殿的龙柏/ }).click();
  await expect(page.locator('.answer-body')).toContainText('1500', { timeout: 60000 });
  await expect(page.getByRole('textbox', { name: '向树语提问' })).toBeEnabled();
  const vectorCheck = await page.evaluate(async () => {
    const session = await (await fetch('/api/session')).json();
    const id = localStorage.getItem('tree-chat-id');
    const messages = await (await fetch('/api/conversations/' + id + '/messages')).json();
    return { session: session.retrieval, audit: messages.at(-1).result.audit.retrieval,
      modelStages: messages.at(-1).result.audit.model_calls.map(call => call.stage) };
  });
  if (vectorCheck.session.mode !== 'vector' || !vectorCheck.session.ready || vectorCheck.session.vectors !== 138 || vectorCheck.audit.mode !== 'vector' || vectorCheck.audit.rerank.status !== 'applied' || !vectorCheck.audit.embedding || vectorCheck.modelStages[0] !== 'understand' || vectorCheck.modelStages.some(stage => stage.includes('route')) || vectorCheck.audit.candidate_entries.length) throw new Error('Expected understand followed by vector retrieval and reranking');
  results.push({ check: 'real question uses query embedding and reranking; 138 vectors ready', passed: true });
  await page.locator('.citation').first().click();
  await expect(page.locator('.source-detail')).toContainText('三官殿');
  await expect(page.locator('.source-detail h2')).toHaveText('龙柏');
  await expect(page.locator('.page-label')).toContainText('《树碑立传》第');
  await page.getByRole('button', { name: '回答有帮助', exact: true }).click();
  await expect(page.locator('.answer-actions')).toContainText('已记录反馈');
  const input = page.getByRole('textbox', { name: '向树语提问' });
  await input.fill('它在哪里？');
  await page.getByRole('button', { name: '发送问题' }).click();
  // Wait for the new answer: the previous answer also contains 三官殿.
  await expect(page.locator('.answer-body')).toHaveCount(2, { timeout: 60000 });
  await expect(page.locator('.answer-body').last()).toContainText('三官殿', { timeout: 60000 });
  await expect(input).toBeEnabled();
  await page.screenshot({ path: path.join(output, 'desktop-answer.png'), fullPage: true, animations: 'disabled' });
  await page.reload();
  await expect(page.locator('.message.assistant')).toHaveCount(2);
  await expect(page.locator('.answer-actions').first()).toContainText('已记录反馈');
  results.push({ check: 'desktop: real answer, item followup, source, feedback, reload persistence', passed: true });
  await page.getByRole('button', {name: '开启新的对话'}).click();
  await input.fill('给我介绍吴中区一些树');
  await page.getByRole('button', {name: '发送问题'}).click();
  await expect(page.locator('.answer-images img').first()).toBeVisible({timeout: 120000});
  await expect(input).toBeEnabled();
  const overview = await page.evaluate(async () => {
    const id = localStorage.getItem('tree-chat-id');
    const messages = await (await fetch('/api/conversations/' + id + '/messages')).json();
    return messages.at(-1).result;
  });
  if (overview.status !== 'answered' || overview.route !== 'explore' || overview.cards.length < 2 || overview.audit.retrieval.mode !== 'vector' || overview.audit.retrieval.candidate_entries.length || overview.audit.model_calls.some(call => call.stage.includes('route'))) throw new Error('Broad introduction must retrieve vectors without catalog sampling');
  if (!overview.audit.retrieval.corpus_fully_recalled || overview.audit.retrieval.selected_entries.length > 6 || overview.claims.map(c => c.text).join('').length < 300) throw new Error('Expected a developed answer based on curated parent cards');
  const inlineImages = overview.claims.flatMap(c => c.images || []);
  if (!inlineImages.length || !overview.claims.some(c => c.heading)) throw new Error('Expected headings and inline book images');
  for (const claim of overview.claims) {
    for (const picture of claim.images || []) {
      const card = overview.cards.find(c => c.images.some(im => im.image_id === picture.image_id));
      const cited = overview.sources.filter(s => claim.source_ids.includes(s.id));
      if (!card || !cited.some(s => s.unit_id === card.unit_id && (!picture.item_no || s.item_no === picture.item_no))) throw new Error('Image must belong to a tree item cited in the paragraph');
    }
  }
  await page.locator('.answer-image-button').first().click();
  await expect(page.getByRole('dialog', {name:'书内图片'})).toBeVisible();
  await expect(page.getByRole('dialog', {name:'书内图片'})).toContainText('《树碑立传》第');
  await page.getByRole('button', {name:'关闭放大图片'}).click();
  await page.reload();
  await expect(page.locator('.answer-images img')).toHaveCount(inlineImages.length);
  await page.locator('.answer-image-button').first().scrollIntoViewIfNeeded();
  await page.screenshot({path:path.join(output, 'district-overview.png'), fullPage:true, animations:'disabled'});
  await page.setViewportSize({width:390,height:844});
  await page.locator('.answer-image-button').first().scrollIntoViewIfNeeded();
  await expect.poll(() => page.locator('.answer-images img').first().evaluate(im => im.complete && im.naturalWidth > 0)).toBe(true);
  const richOverflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth);
  if (richOverflow) throw new Error('Rich mobile answer overflows the viewport');
  await page.screenshot({path:path.join(output, 'mobile-rich-answer.png'), fullPage:true, animations:'disabled'});
  await page.locator('.answer-image-button').first().click();
  await expect(page.getByRole('dialog', {name:'书内图片'})).toBeVisible();
  await page.getByRole('button', {name:'关闭放大图片'}).click();
  await page.setViewportSize({width:1440,height:1000});
  results.push({check:'all 138 passages checked; developed answer, headings, inline images, item association, zoom and reload on desktop/mobile',passed:true});
  await page.getByRole('button', {name: '开启新的对话'}).click();
  await input.fill('给我介绍一些古杨树以及关于他们的故事');
  await page.getByRole('button', {name: '发送问题'}).click();
  await expect(page.locator('.answer-body')).toContainText('双杨', {timeout: 60000});
  await expect(page.locator('.answer-body')).toContainText('传说');
  await expect(input).toBeEnabled();
  const bodyMention = await page.evaluate(async () => {
    const id = localStorage.getItem('tree-chat-id');
    const messages = await (await fetch('/api/conversations/' + id + '/messages')).json();
    return messages.at(-1).result;
  });
  if (bodyMention.status !== 'answered' || bodyMention.focus !== null ||
      bodyMention.sources.some(source => source.entry_no !== 66) ||
      bodyMention.claims.some(claim => /枫杨|黄杨/.test(claim.text)) ||
      bodyMention.audit.retrieval.mode !== 'vector' || bodyMention.audit.retrieval.candidate_count !== 138 ||
      bodyMention.audit.retrieval.rerank.status !== 'applied') throw new Error('Incidental species story regression');
  await expect(page.locator('.answer-images')).toHaveCount(0);
  await page.locator('.citation').first().click();
  await expect(page.locator('.source-detail')).toContainText('双杨村');
  await expect(page.locator('.page-label')).toContainText('131、132');
  await page.screenshot({path:path.join(output, 'body-mention-answer.png'), fullPage:true, animations:'disabled'});
  results.push({check:'original poplar question: body mention, legend qualifier, pages, vector and rerank; no other species or parent focus',passed:true});
  await page.getByRole('button', { name: /古树目录 105/ }).click();
  await page.getByRole('textbox', { name: '搜索古树目录' }).fill('天平山');
  await expect(page.locator('.catalog-card')).toHaveCount(1);
  await page.locator('.catalog-card').click();
  await expect(page.locator('.tree-detail')).toContainText('马尾松');
  await expect(page.locator('.tree-status')).toHaveCount(0);
  await expect(page.locator('body')).not.toContainText('未认定已不存');
  await expect(page.locator('body')).not.toContainText('待审核');
  results.push({ check: 'catalog filter, detail, multiple items', passed: true });
  await context.setOffline(true);
  await page.getByRole('button', { name: '开启新的对话' }).click();
  await input.fill('测试断网提示');
  await page.getByRole('button', { name: '发送问题' }).click();
  await expect(page.getByRole('alert')).toBeVisible();
  await expect(page.getByRole('alert')).toContainText('网络连接中断');
  await context.setOffline(false);
  await expect.poll(() => page.evaluate(() => [...document.images].filter(i => !i.complete || !i.naturalWidth).length)).toBe(0);
  results.push({ check: 'network failure visible and recoverable', passed: true });
  // Fresh mobile browser must not inherit another browser's history.
  const mobile = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, baseURL });
  await mobile.addInitScript(() => { Object.defineProperty(Crypto.prototype, 'randomUUID', {value: undefined}); });
  const phone = await mobile.newPage();
  phone.on('pageerror', error => errors.push(error.message));
  await phone.goto('/');
  await expect(phone.getByRole('heading', { name: '一棵树， 一段吴中往事。' })).toBeVisible();
  await phone.screenshot({ path: path.join(output, 'mobile.png'), fullPage: true });
  await phone.getByRole('button', { name: /03 书中哪些树/ }).click();
  await expect(phone.locator('.answer-body')).toContainText('4 项', { timeout: 60000 });
  await expect(phone.locator('.list-claim')).toHaveCount(4);
  await phone.locator('.citation').first().click();
  await expect(phone.locator('.evidence')).toHaveClass(/is-open/);
  await expect(phone.locator('.source-detail')).toContainText('马尾松');
  await phone.screenshot({ path: path.join(output, 'mobile-source.png'), fullPage: true, animations: 'disabled' });
  await phone.getByRole('button', { name: '关闭原文面板' }).click();
  await phone.getByRole('button', { name: '反馈回答问题' }).click();
  await phone.getByLabel('补充说明（可选）').fill('浏览器验收测试：核对完整名单。');
  await phone.getByRole('button', { name: '记录反馈' }).click();
  await expect(phone.locator('.answer-actions')).toContainText('已记录反馈');
  await phone.screenshot({ path: path.join(output, 'mobile-answer.png'), fullPage: true, animations: 'disabled' });
  for (const [label, view] of [['desktop', page], ['mobile', phone]]) {
    await expect(view.locator('body')).not.toContainText('现场核实');
    await expect(view.locator('body')).not.toContainText('待人工复核');
    const check = await view.evaluate(() => ({ overflow: document.documentElement.scrollWidth > innerWidth,
      brokenImages: [...document.images].filter(i => i.complete && !i.naturalWidth).length }));
    if (check.overflow || check.brokenImages) throw new Error(label + ': invalid layout/images');
    results.push({ check: label + ': no horizontal overflow or broken images', passed: true });
  }
  if (errors.length || external.length) throw new Error(JSON.stringify({ errors, external }));
  results.push({ check: 'mobile list, citations, drawer, feedback, isolated history; no JS errors or external frontend calls', passed: true });
  // Clean only conversations created by these isolated test browser sessions.
  for (const ctx of [context, mobile]) {
    const list = await (await ctx.request.get('/api/conversations')).json();
    for (const item of list) await ctx.request.delete('/api/conversations/' + item.id, { data: {} });
    await ctx.close();
  }
  await writeFile(path.join(output, 'report.json'), JSON.stringify({ passed: true, results }, null, 2));
  console.log(JSON.stringify({ passed: true, checks: results.length }, null, 2));
} catch (error) {
  if (ownedContext) { const pages = ownedContext.pages(); if (pages[0]) await pages[0].screenshot({ path: path.join(output, 'failure.png'), fullPage: true }); }
  await writeFile(path.join(output, 'report.json'), JSON.stringify({ passed: false, results, error: error.message }, null, 2));
  console.error(error.message);
  process.exitCode = 1;
} finally {
  await browser.close();
}
