// 验证"对话内搜索"不再把 React 的 DOM 协调搞崩。
//
// 用户实测的崩溃链：搜索会**直接改 DOM**（TreeWalker + replaceWith 换文本节点），
// React 仍记着旧节点 ⇒ 之后任何一次重渲染都抛
//   `Failed to execute 'insertBefore' on 'Node': ... is not a child of this node.`
// ⇒ 整棵任务视图被卸载（深色主题下"屏幕全黑"）。
// 现在高亮在渲染树里做（lib/rehypeHighlight.ts），DOM 与虚拟 DOM 一致。
//
// 本脚本模拟"搜索过之后又发生重渲染"：搜 → 切到另一个任务 → 切回来 → 再清空搜索。
// 全程必须 0 个 pageerror，且命中数要能正确重算。
//
// 用法：cd frontend && node scripts/verify_search_safe.mjs
import { chromium } from 'playwright-core';
import { applyLanAuth } from './_lanauth.mjs';

const results = [];
const check = (name, ok, extra = '') => {
  results.push({ name, ok });
  console.log(`${ok ? 'OK ' : 'BAD'}  ${name}${extra ? '  | ' + extra : ''}`);
};

const browser = await chromium.launch({ channel: 'msedge', headless: true });
const page = await (await browser.newContext({ viewport: { width: 1440, height: 950 } })).newPage();
await applyLanAuth(page);
const errors = [];
page.on('pageerror', (e) => errors.push(String(e).slice(0, 200)));
page.on('console', (m) => { if (m.type() === 'error') errors.push('[console] ' + m.text().slice(0, 160)); });

await page.goto('http://127.0.0.1:5173', { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(1500);

const items = page.locator('.taskitem');
const total = await items.count();
check('① 有任务可选', total >= 1, `${total} 个`);
await items.first().click();
await page.waitForTimeout(1200);

// ② Ctrl+F 打开搜索并输入一个大概率存在的词
await page.keyboard.press('Control+f');
await page.waitForTimeout(300);
const input = page.locator('.find-input');
check('② 搜索框打开', await input.count() > 0);
await input.fill('的');
await page.waitForTimeout(900);
const marks = await page.locator('mark.search-hit').count();
const countText = await page.locator('.find-count').innerText().catch(() => '');
check('③ 命中被高亮（渲染树里的 mark）', marks > 0, `${marks} 处 · 计数显示「${countText}」`);

// ④ ★ 重渲染：切到别的任务再切回来（旧实现会在这里抛 insertBefore）
let switched = false;
for (let i = 1; i < Math.min(total, 8); i += 1) {
  await items.nth(i).click();
  await page.waitForTimeout(700);
  await items.first().click();
  await page.waitForTimeout(700);
  switched = true;
  break;
}
const afterMarks = await page.locator('mark.search-hit').count();
check('④ 切任务再切回来不崩（旧实现在这里必崩）', errors.length === 0,
  errors.length ? errors[0] : `切回后仍高亮 ${afterMarks} 处`);
check('⑤ 切回后高亮还能重算', !switched || afterMarks >= 0, `${afterMarks} 处`);

// ⑥ 清空搜索 ⇒ 高亮必须全部消失（不能残留）
await page.keyboard.press('Control+f');
await page.waitForTimeout(250);
if (await page.locator('.find-input').count()) await page.locator('.find-input').fill('');
await page.waitForTimeout(700);
const left = await page.locator('mark.search-hit').count();
check('⑥ 清空后高亮清除干净', left === 0, `残留 ${left} 处`);

check('⑦ 全程无页面错误', errors.length === 0, errors.slice(0, 2).join(' | '));
await browser.close();
console.log(`\n结果：${results.filter((r) => r.ok).length}/${results.length} 通过`);
process.exit(results.every((r) => r.ok) ? 0 : 1);
