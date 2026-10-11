// 「这一趟做了什么」折叠小结 验证（真浏览器 · 真任务）
//
// 判据（对着用户的话逐条验）：
//   ① 小结块钉在对话流**最底下**（在所有消息气泡与工具卡片之后）
//   ② 默认**收起**（不占屏，只一行）
//   ③ 收起时就能看清"这一趟干了啥"：你说过几次 / 几步 / 各类工具次数 / 产出几个文件
//   ④ 点开能看到：你提的要求（不用往上翻）、逐步过程（带 ✓/✗ 与耗时）、产出与来源、它最后说的
//   ⑤ 上面对话流**照旧一问一答**（一次回答一个气泡，没有被切碎）
//   ⑥ 无页面错误、无横向溢出
//
// 用法：cd frontend && node scripts/verify_task_digest.mjs
import { chromium } from 'playwright-core';
import os from 'node:os';
import path from 'node:path';
import { mkdirSync } from 'node:fs';
import { applyLanAuth } from './_lanauth.mjs';

const BASE = 'http://127.0.0.1:5173';
const API = 'http://127.0.0.1:8642';
const SHOTS = path.join(os.tmpdir(), 'agent-shell-shots', 'task-digest');
mkdirSync(SHOTS, { recursive: true });
const results = [];
const check = (name, ok, detail = '') => {
  results.push({ name, ok: !!ok });
  console.log((ok ? 'OK  ' : 'BAD ') + name + (detail ? '  | ' + detail : ''));
};

const token = process.env.DSH_LAN_TOKEN || '';
const headers = { 'X-Auth-Token': token };
const tasks = await (await fetch(`${API}/api/v1/tasks`, { headers })).json();

// 挑一个"多步 + 有多条助手回复"的任务（小结才有内容）
let pick = null;
for (const t of (tasks ?? []).slice(0, 14)) {
  const evs = await (await fetch(`${API}/api/v1/tasks/${t.id}/events`, { headers })).json();
  const acts = (evs ?? []).filter((e) => e.type === 'action').length;
  const msgs = (evs ?? []).filter((e) => e.type === 'message' && e.payload?.role === 'assistant').length;
  if (acts >= 2 && msgs >= 2) { pick = { t, acts, msgs, evs }; break; }
}
if (!pick) { console.log('BAD 没找到多步任务（先跑一个用工具的任务）'); process.exit(1); }
console.log(`用任务：${pick.t.id}（${pick.msgs} 条助手回复 / ${pick.acts} 个工具调用）\n`);

const browser = await chromium.launch({ channel: 'msedge', headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 950 } });
await applyLanAuth(page);
const errors = [];
page.on('pageerror', (e) => errors.push(String(e).slice(0, 140)));
await page.goto(BASE, { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(900);

// 侧栏标题可能重名 ⇒ 逐个点、核对页头任务号
const items = page.locator('.taskitem');
const n = await items.count();
for (let i = 0; i < Math.min(n, 20); i++) {
  await items.nth(i).click();
  await page.waitForTimeout(650);
  const sub = await page.locator('.tv-sub').first().innerText().catch(() => '');
  if (sub.includes(pick.t.id)) break;
}
await page.waitForTimeout(900);

const box = page.locator('.digest');
check('① 有小结块', await box.count() === 1, `${await box.count()} 个`);
check('② 默认收起', !(await box.evaluate((el) => el.hasAttribute('open'))));
const headText = (await box.locator('summary').innerText()).replace(/\s+/g, ' ');
check('③ 收起时一行就说清了：次数/步数/工具/产出',
  /这一趟做了什么/.test(headText) && /步/.test(headText),
  headText.slice(0, 110));

// 位置：小结块必须在最后一个消息气泡之后（= 钉在最底下）
const order = await page.evaluate(() => {
  const stream = document.querySelector('.stream');
  if (!stream) return null;
  const kids = Array.from(stream.children);
  const digestIdx = kids.findIndex((k) => k.classList.contains('digest'));
  const lastMsgIdx = kids.map((k, i) => (k.querySelector?.('.msg-assistant') ? i : -1))
    .filter((i) => i >= 0).pop() ?? -1;
  return { digestIdx, lastMsgIdx, total: kids.length };
});
check('④ 小结块在所有消息之后（钉在最底下）',
  !!order && order.digestIdx > order.lastMsgIdx,
  order ? `小结在第 ${order.digestIdx + 1} / 共 ${order.total} 个子节点，最后一条消息在第 ${order.lastMsgIdx + 1}` : '取不到结构');

await box.locator('summary').click();
await page.waitForTimeout(300);
const bodyText = (await box.locator('.digest-body').innerText().catch(() => '')).replace(/\s+/g, ' ');
check('⑤ 点开后能看到「你提的要求」（不用往上翻）', /你提的要求/.test(bodyText), bodyText.slice(0, 90));
check('⑥ 能看到逐步过程（带工具名与耗时）', /过程/.test(bodyText) && /(ms|s\b)/.test(bodyText));
check('⑦ 能看到「它最后说的」', /它最后说的/.test(bodyText));

// 对话流照旧：一次回答 = 一个气泡（没有被切碎）
const bubbleStat = await page.evaluate(() => {
  const evs = document.querySelectorAll('.stream > *');
  let maxPerMsg = 0;
  evs.forEach((el) => {
    if (el.classList.contains('msg-assistant')) maxPerMsg = Math.max(maxPerMsg, el.querySelectorAll('.bubble-assistant').length);
    else {
      const inner = el.querySelectorAll('.msg-assistant .bubble-assistant').length;
      maxPerMsg = Math.max(maxPerMsg, inner);
    }
  });
  return { maxPerMsg, stacks: document.querySelectorAll('.msg-stack, .bubble-cont').length };
});
check('⑧ 上面对话流照旧一问一答（单条回复 1 个气泡，没被切碎）',
  bubbleStat.maxPerMsg <= 1 && bubbleStat.stacks === 0,
  `单条最多气泡 ${bubbleStat.maxPerMsg} 个 / 残留切段元素 ${bubbleStat.stacks} 个`);

check('⑨ 无页面错误', errors.length === 0, errors.join(' | '));
const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 2);
check('⑩ 无横向溢出', !overflow);

await page.screenshot({ path: path.join(SHOTS, 'task-digest-open.png') });
await box.locator('summary').click();
await page.waitForTimeout(250);
await page.screenshot({ path: path.join(SHOTS, 'task-digest-collapsed.png') });
await browser.close();

const bad = results.filter((r) => !r.ok).length;
console.log(`\n结果：${results.length - bad}/${results.length} 通过`);
console.log(`截图：${SHOTS}`);
process.exit(bad === 0 ? 0 : 1);
