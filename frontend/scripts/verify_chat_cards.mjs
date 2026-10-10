// Phase 3 ⑦ 迁移验证（真浏览器 · **真任务真事件**）：真界面的"工具调用卡片 + 助手署名"
//
// 与原型验证的区别：这里**不喂假数据**，直接打开一个真有工具调用事件的任务，
// 看真界面的呈现。判据：
//   ① 工具动作有图标 + 工具名 + 一行摘要（不是裸日志行）
//   ② 动作与紧随其后的结果**视觉上连成一张卡**（动作无下边框、结果无上边框、圆角相接）
//   ③ 结果有成功/失败的左侧色条（失败不能和成功长一样）
//   ④ 动作的 details 仍能展开看到参数（原有的可折叠能力没丢）
//   ⑤ 助手消息有"Agent"署名；正在生成时（若恰好抓到）有"生成中…"
//   ⑥ 正常界面其它部分没被破坏：审批条/输入区/右栏图标仍在
//
// 用法：cd frontend && node scripts/verify_chat_cards.mjs
import { chromium } from 'playwright-core';
import os from 'node:os';
import path from 'node:path';
import { mkdirSync } from 'node:fs';
import { applyLanAuth } from './_lanauth.mjs';

const BASE = 'http://127.0.0.1:5173';
const API = 'http://127.0.0.1:8642';
const SHOTS = path.join(os.tmpdir(), 'agent-shell-shots', 'chat-cards');
mkdirSync(SHOTS, { recursive: true });
const results = [];
const check = (name, ok, detail = '') => {
  results.push({ name, ok: !!ok });
  console.log((ok ? 'OK  ' : 'BAD ') + name + (detail ? '  | ' + detail : ''));
};

// 找一个**真有工具调用**的任务（有 shell 类动作 + 观察结果）
const token = process.env.DSH_LAN_TOKEN || '';
const t = await (await fetch(`${API}/api/v1/tasks`, { headers: { 'X-Auth-Token': token } })).json();
let pick = null;
for (const task of (t ?? []).slice(0, 12)) {
  const evs = await (await fetch(`${API}/api/v1/tasks/${task.id}/events`, { headers: { 'X-Auth-Token': token } })).json();
  const acts = (evs ?? []).filter((e) => e.type === 'action');
  const obs = (evs ?? []).filter((e) => e.type === 'observation');
  if (acts.length > 0 && obs.length > 0) { pick = { task, acts: acts.length, obs: obs.length }; break; }
}
if (!pick) { console.log('BAD 找不到带工具调用的任务（请先跑一个用 shell 的任务）'); process.exit(1); }
console.log(`用任务：${pick.task.id}（${pick.acts} 个动作 / ${pick.obs} 个结果）\n`);

const browser = await chromium.launch({ channel: 'msedge', headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 950 } });
await applyLanAuth(page);
const errors = [];
page.on('pageerror', (e) => errors.push(String(e).slice(0, 140)));
await page.goto(`${BASE}/?task=${pick.task.id}`, { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(1200);
// 左侧任务列表里点开那个任务（保证走的是真实交互路径）
const item = page.locator('.taskitem', { hasText: pick.task.title?.slice(0, 8) ?? '' }).first();
if (await item.count()) { await item.click(); await page.waitForTimeout(1200); }

const action = page.locator('.ev-action').first();
check('① 工具动作渲染成卡片行（不是裸日志）', await action.count() > 0, `本页 ${await page.locator('.ev-action').count()} 个`);
check('② 动作带图标 + 工具名 + 摘要', await action.locator('svg.tool-icon').count() > 0
  && (await action.locator('.tool-name').innerText().catch(() => '')).length > 0,
  await action.locator('.tool-name').innerText().catch(() => ''));
check('③ 动作仍有折叠三角（展开看参数的能力没丢）',
  await action.locator('details > summary').count() > 0);

// 卡片相接：动作无下边框、结果无上边框、结果左侧有色条
const style = await page.evaluate(() => {
  const a = document.querySelector('.ev-action');
  const o = document.querySelector('.ev-obs');
  if (!a || !o) return null;
  const ca = getComputedStyle(a), co = getComputedStyle(o);
  return {
    aBottom: ca.borderBottomWidth, oTop: co.borderTopWidth,
    oLeft: co.borderLeftWidth, oLeftColor: co.borderLeftColor,
    oClass: o.className,
  };
});
check('④ 动作与结果连成一张卡（下/上边框相接）',
  !!style && style.aBottom === '0px' && style.oTop === '0px',
  style ? `动作下边框=${style.aBottom} 结果上边框=${style.oTop}` : '取不到样式');
check('⑤ 结果有左侧色条（成功/失败可分辨）',
  !!style && parseFloat(style.oLeft) >= 2, style ? `左边框=${style.oLeft} 颜色=${style.oLeftColor}（${style.oClass}）` : '');

// 展开参数
await action.locator('details > summary').click();
await page.waitForTimeout(250);
const opened = await page.evaluate(() => {
  const d = document.querySelector('.ev-action details');
  const pre = d?.querySelector('pre');
  return { open: d?.hasAttribute('open') ?? false, len: (pre?.textContent ?? '').length };
});
check('⑥ 点开后能看到参数（原有能力保持）', opened.open && opened.len > 2, `${opened.len} 字符`);

check('⑦ 助手消息有"Agent"署名', await page.locator('.msg-who').count() > 0);
check('⑧ 界面其它部分没被破坏（输入区 + 右栏按钮在）',
  await page.locator('.composer, textarea').count() > 0
  && await page.locator('.tv-actions, .aside-toggle, button').count() > 0);
check('⑨ 无页面错误', errors.length === 0, errors.join(' | '));

await page.screenshot({ path: path.join(SHOTS, 'chat-cards-real.png') });
await browser.close();
const bad = results.filter((r) => !r.ok).length;
console.log(`\n结果：${results.length - bad}/${results.length} 通过`);
console.log(`截图：${SHOTS}`);
process.exit(bad === 0 ? 0 : 1);
