// Bilingual interaction checks against the deterministic browser_app fixture.
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
const output = path.join(root, 'state/improve-v5/browser');
await mkdir(output, { recursive: true });
const checks = [], errors = [];
try {
  for (const viewport of [{width:1440,height:1000},{width:390,height:844}]) {
    const context = await browser.newContext({ baseURL: process.env.TEST_BASE_URL || 'http://127.0.0.1:8124', viewport });
    const page = await context.newPage();
    const sent = [];
    page.on('pageerror', e => errors.push(e.message));
    page.on('request', r => { if (r.method() === 'POST' && r.url().endsWith('/messages')) sent.push(r.postDataJSON()); });
    await page.goto('/');
    await expect(page.locator('.hero-card')).toHaveCount(3);
    await page.getByRole('button', {name:'EN',exact:true}).click();
    await expect(page.locator('html')).toHaveAttribute('lang','en');
    await expect(page.getByRole('heading',{name:'A tree, a story of Wuzhong.'})).toBeVisible();
    await expect(page.locator('.hero-card').first()).toContainText('Tianping');
    await page.screenshot({path:path.join(output,`welcome-en-${viewport.width}.png`)});
    await page.reload();
    await expect(page.getByRole('button',{name:'EN',exact:true})).toHaveAttribute('aria-pressed','true');
    await expect(page.locator('.hero-card')).toHaveCount(3);
    if(viewport.width < 600) await page.getByRole('button',{name:'Open navigation'}).click();
    await page.locator('.catalog-nav').click();
    await expect(page.getByRole('dialog',{name:'Tree catalog'})).toBeVisible();
    const search = page.getByRole('textbox',{name:'Search the catalog'});
    await search.fill('Sanguan');
    await expect(page.locator('.catalog-card')).toHaveCount(1);
    await expect(page.locator('.catalog-hook')).toBeVisible();
    await page.locator('.catalog-card').click();
    await expect(page.locator('.tree-hook').first()).toBeVisible();
    await expect(page.locator('.tree-detail')).toContainText('Dragon Juniper');
    await page.screenshot({path:path.join(output,`card-en-${viewport.width}.png`)});
    if(viewport.width < 600) await page.getByRole('button',{name:'Close sources',exact:true}).click();
    const input=page.getByRole('textbox',{name:'Ask Shuyu'});
    await input.fill('How old is the Dragon Juniper at Sanguan Hall?');
    await page.getByRole('button',{name:'Send question'}).click();
    await expect(page.locator('.answer-body')).toContainText('1,500');
    await expect(input).toBeEnabled();
    await expect(page.locator('.followup-chips button')).toHaveCount(3);
    expect(sent[0].lang).toBe('en');
    await page.locator('.citation').first().click();
    await expect(page.locator('.source-detail')).toContainText('Dragon Juniper');
    await expect(page.locator('.page-label')).toContainText('Book of Ancient Trees, p. 76');
    await expect(page.locator('.source-footer')).toHaveText('AI reviewed passage by passage · No human sign-off');
    await page.locator('.original-translation summary').click();
    await expect(page.locator('.original-translation')).toContainText('三官殿');
    if(viewport.width < 600) await page.getByRole('button',{name:'Close sources',exact:true}).click();
    const shown = new Set();
    for (let click = 0; click < 6; click++) {
      const chips = page.locator('.followup-chips button');
      const labels = (await chips.allTextContents()).map(x => x.trim());
      expect(labels.length).toBeGreaterThan(0);
      expect(labels.length).toBeLessThanOrEqual(3);
      for (const label of labels) {
        expect(shown.has(label)).toBe(false);
        shown.add(label);
      }
      const next = labels[0];
      await chips.first().click();
      await expect(page.locator('.message.assistant')).toHaveCount(click + 2);
      await expect(input).toBeEnabled();
      await expect(page.locator('.followup-chips')).toHaveCount(1);
      expect(sent[click + 1].text).toBe(next);
      expect(sent[click + 1].lang).toBe('en');
    }
    for (const label of await page.locator('.followup-chips button').allTextContents())
      expect(shown.has(label.trim())).toBe(false);
    expect(await page.evaluate(()=>document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({path:path.join(output,`answer-en-${viewport.width}.png`)});
    await page.getByRole('button',{name:'中',exact:true}).click();
    await expect(page.getByRole('textbox',{name:'向树语提问'})).toBeVisible();
    await page.reload();
    await expect(page.getByRole('button',{name:'中',exact:true})).toHaveAttribute('aria-pressed','true');
    checks.push({width:viewport.width,languagePersistence:true,catalogAndHooks:true,staticBilingualSources:true,translationReviewLabel:true,followupClick:true,sixClicksNoRepeatedSuggestions:true,noOverflow:true});
    await context.close();
  }
  expect(errors).toEqual([]);
  await writeFile(path.join(output,'results.json'),JSON.stringify({passed:true,checks,errors},null,2));
  console.log(JSON.stringify({passed:true,checks,errors}));
} finally { await browser.close(); }
