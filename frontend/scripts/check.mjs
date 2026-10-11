
import { chromium } from 'playwright-core';
const URL = 'file:///' + 'D:/AI/agent-shell/backend/data/groups/grp_1d8498/workspace/index.html'.replace(/^\//, '');
const b = await chromium.launch({ channel: 'msedge', headless: true });
const p = await b.newPage();
const errs = [];
p.on('console', m => { if (m.type() === 'error') errs.push('CONSOLE: ' + m.text().slice(0, 160)); });
p.on('pageerror', e => errs.push('PAGEERROR: ' + String(e).slice(0, 160)));
await p.goto(URL);
await p.waitForTimeout(1500);
console.log('页面标题:', await p.title());
const btns = await p.locator('button').count();
console.log('页面上按钮数:', btns);

// 真点前 6 个按钮，看有没有任何界面变化 / 报错
for (let i = 0; i < Math.min(btns, 6); i++) {
  const el = p.locator('button').nth(i);
  const label = ((await el.textContent()) || '').trim().slice(0, 20);
  const before = (await p.locator('body').innerText()).length;
  let clicked = true;
  try { await el.click({ timeout: 1500 }); } catch (e) { clicked = false; }
  await p.waitForTimeout(600);
  const after = (await p.locator('body').innerText()).length;
  console.log(`按钮${i + 1}「${label}」 点得到=${clicked} 点击前后页面文字:${before}→${after} ${before === after ? '（无变化 ✗）' : '（有变化 ✓）'}`);
}
console.log('JS 报错数:', errs.length);
for (const e of errs.slice(0, 6)) console.log('   ', e);
await b.close();
