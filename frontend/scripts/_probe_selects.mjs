// 一次性小工具：把页面上所有 <select> 及其选项摊开（排查"源码改了、界面没变"这类问题）。
// 用法：cd frontend && node scripts/_probe_selects.mjs
import { chromium } from 'playwright-core';
import { applyLanAuth } from './_lanauth.mjs';

const b = await chromium.launch({ channel: 'msedge', headless: true });
const p = await (await b.newContext({ viewport: { width: 1440, height: 950 } })).newPage();
await applyLanAuth(p);
await p.goto('http://127.0.0.1:5173', { waitUntil: 'domcontentloaded' });
await p.waitForTimeout(1200);
const teamBtn = p.locator('button, a').filter({ hasText: /^团队$/ }).first();
if (await teamBtn.count()) await teamBtn.click();
await p.waitForTimeout(1200);
// 切到群聊并点开第一个群（模式下拉只在群打开时渲染）
const chatTab = p.locator('button, div').filter({ hasText: /^群聊$/ }).first();
if (await chatTab.count()) { await chatTab.click(); await p.waitForTimeout(700); }
const firstGroup = p.locator('.team-group-name').first();
if (await firstGroup.count()) {
  await firstGroup.click();
  console.log('已点开群：' + (await firstGroup.innerText()).trim());
}
await p.waitForTimeout(900);

const info = await p.evaluate(() => [...document.querySelectorAll('select')].map((s, i) => ({
  idx: i,
  value: s.value,
  opts: [...s.options].map((o) => `${o.value}:${(o.textContent || '').trim().slice(0, 16)}`),
})));
console.log('页面上的 select 数量：' + info.length);
for (const s of info) console.log(`  #${s.idx} 当前=${s.value}\n     ${s.opts.join(' ｜ ')}`);
await b.close();
