// 浅色主题下右下角那两个浮动件的实拍 + 计算色（客观证据）
import { chromium } from 'playwright-core';
import { applyLanAuth } from './_lanauth.mjs';

const OUT = 'D:\\丹东云杉网络工作室\\agent-shell-评审';
const browser = await chromium.launch({ channel: 'msedge', headless: true });
const page = await browser.newPage({ viewport: { width: 1360, height: 900 } });
await page.addInitScript(() => localStorage.setItem('theme', 'light'));   // App.tsx 读它 ✓
await applyLanAuth(page);
await page.goto('http://127.0.0.1:8642', { waitUntil: 'networkidle' });
await page.waitForTimeout(1500);

// ① 打开一个**有内容**的任务（class = .taskitem ✓ 上一步探到的）
const item = page.locator('.taskitem').first();
console.log('任务项数:', await page.locator('.taskitem').count());
await item.click();
await page.waitForTimeout(2500);

// ② 往上滚 ⇒ 「回到底部」才出现（showJumpBottom ✓）
await page.mouse.move(700, 450);
await page.mouse.wheel(0, -1500);
await page.waitForTimeout(1000);

const probe = async (sel) => {
  const el = page.locator(sel).first();
  if (!(await el.count())) return { sel, missing: true };
  return await el.evaluate((n) => {
    const cs = getComputedStyle(n);
    const r = n.getBoundingClientRect();
    return { text: (n.textContent || '').trim().slice(0, 20), bg: cs.backgroundColor, color: cs.color,
             box: [Math.round(r.x), Math.round(r.y)] };
  });
};
console.log('主题 =', await page.evaluate(() => document.documentElement.dataset.theme));
for (const sel of ['.usage-badge', '.jump-bottom']) console.log(sel, JSON.stringify(await probe(sel)));

await page.screenshot({ path: `${OUT}\\浅色-右下角.png`,
                        clip: { x: 1360 - 400, y: 900 - 250, width: 400, height: 250 } });
console.log('已截图 ✓');
await browser.close();
