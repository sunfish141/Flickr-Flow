import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import { chromium } from 'playwright-core';
import AxeBuilder from '@axe-core/playwright';

const base = process.argv[2] || 'http://127.0.0.1:8001/explore/';
const artifacts = process.argv[3] || '../artifacts/simulation-picker';
await mkdir(artifacts, { recursive: true });
const browser = await chromium.launch({ headless: true });
const reports = [];
try {
  for (const [width, height, scale] of [[1440, 1000, 1], [1280, 800, 1.25], [1024, 768, 1.5], [390, 844, 2]]) {
    const context = await browser.newContext({ viewport: { width, height }, deviceScaleFactor: scale });
    await context.route('**/*', route => new URL(route.request().url()).origin === new URL(base).origin
      ? route.continue() : route.abort());
    const page = await context.newPage(), errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(base);
    await page.waitForFunction(() => document.querySelector('#local-region')?.value === 'black-hawk-colorado');
    const picker = page.locator('#local-region');
    assert.equal(await picker.getAttribute('role'), 'combobox');
    await picker.click();
    await page.locator('#simulation-options').waitFor();
    const bounds = await page.evaluate(() => {
      const control = document.querySelector('#local-region').getBoundingClientRect();
      const menu = document.querySelector('#simulation-options').getBoundingClientRect();
      const sidebar = document.querySelector('.sidebar');
      return { controlWidth: control.width, menuWidth: menu.width, left: menu.left, right: menu.right,
        viewport: innerWidth, sidebarOverflow: sidebar.scrollWidth > sidebar.clientWidth,
        pageOverflow: document.documentElement.scrollWidth > innerWidth };
    });
    assert.ok(Math.abs(bounds.controlWidth - bounds.menuWidth) <= 1);
    assert.ok(bounds.left >= 0 && bounds.right <= bounds.viewport);
    assert.equal(bounds.sidebarOverflow, false);
    assert.equal(bounds.pageOverflow, false);
    assert.equal(await page.locator('#scenario-controls').getByText('Historical sources:', { exact: false }).count(), 0);
    assert.equal(await page.locator('#scenario-controls .research-note').count(), 0);
    const accessibility = await new AxeBuilder({ page }).include('.simulation-picker').analyze();
    assert.deepEqual(accessibility.violations, [], 'Picker accessibility');
    await page.screenshot({ path: `${artifacts}/picker-${width}-${scale}.png`, fullPage: width < 750 });
    // Escape cancels, arrows/type-ahead navigate, Enter commits, Tab dismisses.
    await picker.press('ArrowDown');
    await picker.press('Escape');
    assert.equal(await picker.getAttribute('value'), 'black-hawk-colorado');
    await picker.press('End');
    await picker.press('Enter');
    assert.equal(await picker.getAttribute('value'), 'hinton-alberta');
    await picker.press('Home');
    await picker.press('Enter');
    assert.equal(await picker.getAttribute('value'), '');
    await picker.press('b');
    await picker.press('Enter');
    assert.equal(await picker.getAttribute('value'), 'black-hawk-colorado');
    await picker.click();
    await page.locator('[role=option][data-value="hinton-alberta"]').click();
    assert.equal(await picker.getAttribute('value'), 'hinton-alberta');
    await picker.click();
    await picker.press('Tab');
    assert.equal(await picker.getAttribute('aria-expanded'), 'false');
    await picker.click();
    await page.locator('h1').click();
    assert.equal(await picker.getAttribute('aria-expanded'), 'false');
    await page.getByRole('button', { name: 'Details', exact: true }).click();
    assert.ok(await page.locator('#help-dialog').isVisible());
    assert.match(await page.locator('#help-dialog').innerText(), /Historical sources:.*NALCMS/s);
    assert.match(await page.locator('#help-dialog').innerText(), /General ember crossing is not modeled/);
    await page.locator('#close-help').click();
    assert.deepEqual(errors, []);
    reports.push({ width, height, deviceScaleFactor: scale, bounds, keyboard: true, mouse: true, accessibilityViolations: 0 });
    await context.close();
  }
  await writeFile(`${artifacts}/report.json`, JSON.stringify(reports, null, 2));
  console.log(JSON.stringify(reports, null, 2));
} finally { await browser.close(); }
