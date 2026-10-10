// Phase 3 ⑦ 零碎批 验证（真浏览器 · 真任务）：消息时间戳 + 对话内搜索
//
// 判据：
//   ① 助手/用户消息上有时间戳（HH:MM）
//   ② 页头有搜索按钮；点它出现搜索条（且自动聚焦）
//   ③ 输入一个**任务里确实存在**的词 → 出现 <mark class="search-hit"> 命中，计数显示 n / m
//   ④ Enter 切到下一个命中（"当前"那个带 search-hit-on，且会滚动到视野里）
//   ⑤ Shift+Enter 回上一个
//   ⑥ 改搜索词 → 上一次的高亮被清掉（不越搜越乱：mark 数量等于新词的命中数）
//   ⑦ Esc 关闭搜索条，且高亮**全部清掉**（不留痕迹）
//   ⑧ Ctrl+F 能打开搜索条（抢掉浏览器自带的页内查找）
//   ⑨ 无页面错误
//
// 用法：cd frontend && node scripts/verify_find_and_time.mjs
import { chromium } from 'playwright-core';
import os from 'node:os';
import path from 'node:path';
import { mkdirSync } from 'node:fs';
import { applyLanAuth } from './_lanauth.mjs';

const BASE = 'http://127.0.0.1:5173';
const API = 'http://127.0.0.1:8642';
const SHOTS = path.join(os.tmpdir(), 'agent-shell-shots', 'find-time');
mkdirSync(SHOTS, { recursive: true });
const results = [];
const check = (name, ok, detail = '') => {
  results.push({ name, ok: !!ok });
  console.log((ok ? 'OK  ' : 'BAD ') + name + (detail ? '  | ' + detail : ''));
};

const headers = { 'X-Auth-Token': process.env.DSH_LAN_TOKEN || '' };
const tasks = await (await fetch(`${API}/api/v1/tasks`, { headers })).json();
let pick = null;
for (const t of (tasks ?? []).slice(0, 14)) {
  const evs = await (await fetch(`${API}/api/v1/tasks/${t.id}/events`, { headers })).json();
  const msgs = (evs ?? []).filter((e) => e.type === 'message' && (e.payload?.text ?? '').length > 20);
  if (msgs.length >= 2) { pick = { t, msgs }; break; }
}
if (!pick) { console.log('BAD 没找到有消息的任务'); process.exit(1); }
// 用真消息里的一个短词做搜索词（保证能命中）
const word = (pick.msgs[0].payload.text.match(/[\u4e00-\u9fa5]{2,3}/g) ?? ['任务'])[0];
console.log(`用任务：${pick.t.id}；搜索词：${word}\n`);

const browser = await chromium.launch({ channel: 'msedge', headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 950 } });
await applyLanAuth(page);
const errors = [];
page.on('pageerror', (e) => errors.push(String(e).slice(0, 140)));
await page.goto(BASE, { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(900);
const items = page.locator('.taskitem');
for (let i = 0; i < Math.min(await items.count(), 20); i++) {
  await items.nth(i).click();
  await page.waitForTimeout(600);
  const sub = await page.locator('.tv-sub').first().innerText().catch(() => '');
  if (sub.includes(pick.t.id)) break;
}
await page.waitForTimeout(800);

const times = await page.locator('.msg-time').allInnerTexts();
check('① 消息上有时间戳（HH:MM）',
  times.length > 0 && times.every((t) => /^\d{1,2}:\d{2}$/.test(t.trim())),
  `${times.length} 个：${times.slice(0, 3).join(' / ')}`);

// ② Ctrl+F 打开
await page.keyboard.press('Control+f');
await page.waitForTimeout(400);
check('② Ctrl+F 打开搜索条', await page.locator('.find-bar').count() > 0);
check('③ 搜索框自动聚焦', await page.evaluate(() =>
  document.activeElement?.classList.contains('find-input') === true));

const input = page.locator('.find-input').first();
await input.fill(word);
await page.waitForTimeout(500);
const marks = await page.locator('mark.search-hit').count();
const hitTotal = marks;   // 命中总数（⑦ 用它判断"只有一处时按 Enter 仍是 1/1"是正常的）
const countText = (await page.locator('.find-count').first().innerText().catch(() => '')).trim();
check('④ 命中并高亮', marks > 0, `mark ${marks} 个；计数"${countText}"`);
check('⑤ 计数显示 n / m', /^\d+ \/ \d+$/.test(countText), countText);
check('⑥ 当前命中带 search-hit-on', await page.locator('mark.search-hit.search-hit-on').count() === 1);

await input.press('Enter');
await page.waitForTimeout(500);
const onIdx = await page.evaluate(() => {
  const all = [...document.querySelectorAll('mark.search-hit')];
  return all.findIndex((m) => m.classList.contains('search-hit-on'));
});
// ★ 只有 1 处命中时，"下一个"回到自己（1/1）—— 那也算对，别误判成红
check('⑦ Enter 切到下一个', onIdx === 1 || hitTotal === 1, `当前第 ${onIdx + 1} 个 / 共 ${hitTotal}`);
await input.press('Shift+Enter');
await page.waitForTimeout(400);
const backIdx = await page.evaluate(() => {
  const all = [...document.querySelectorAll('mark.search-hit')];
  return all.findIndex((m) => m.classList.contains('search-hit-on'));
});
check('⑧ Shift+Enter 回上一个', backIdx === 0, `当前第 ${backIdx + 1} 个`);

// ⑥ 换词：上次的 mark 必须被清掉
await input.fill('绝不可能出现的词组ZZZ');
await page.waitForTimeout(500);
check('⑨ 换词后旧高亮被清掉（不越搜越乱）',
  await page.locator('mark.search-hit').count() === 0
  && /没找到/.test(await page.locator('.find-count').first().innerText().catch(() => '')),
  `mark ${await page.locator('mark.search-hit').count()} 个`);
await page.screenshot({ path: path.join(SHOTS, 'find-bar.png') });

// ⑦ Esc 关闭并清干净
await input.fill(word);
await page.waitForTimeout(400);
await page.keyboard.press('Escape');
await page.waitForTimeout(500);
check('⑩ Esc 关闭搜索条', await page.locator('.find-bar').count() === 0);
check('⑪ 关闭后高亮全部清掉（不留痕迹）', await page.locator('mark.search-hit').count() === 0);
check('⑫ 无页面错误', errors.length === 0, errors.join(' | '));

await browser.close();
const bad = results.filter((r) => !r.ok).length;
console.log(`\n结果：${results.length - bad}/${results.length} 通过`);
console.log(`截图：${SHOTS}`);
process.exit(bad === 0 ? 0 : 1);
