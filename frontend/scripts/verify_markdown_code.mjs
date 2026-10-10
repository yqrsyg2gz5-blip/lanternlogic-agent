// Phase 3 ⑦ 零碎批：Markdown 渲染（代码块语言标签 + 一键复制 + GFM 表格）
//
// 判据（全部在**真任务**上验证，不造假数据）：
//   ① 助手回复里的代码块带 `.md-code` 外壳
//   ② 顶部有语言标签（没写语言时显示"文本"）
//   ③ 点「复制」→ 剪贴板内容 == 该代码块的**原始文本**（按去掉首尾空行比）
//   ④ 点过后按钮变"已复制"（有反馈）
//   ⑤ GFM 表格能渲染成真 `<table>`（任务里有表格就查；没有就如实标 SKIP）
//   ⑥ 无页面错误
//
// 用法：cd frontend && node scripts/verify_markdown_code.mjs
import { chromium } from 'playwright-core';
import os from 'node:os';
import path from 'node:path';
import { mkdirSync } from 'node:fs';
import { applyLanAuth } from './_lanauth.mjs';

const BASE = 'http://127.0.0.1:5173';
const API = 'http://127.0.0.1:8642';
const SHOTS = path.join(os.tmpdir(), 'agent-shell-shots', 'md-code');
mkdirSync(SHOTS, { recursive: true });
const results = [];
const check = (name, ok, detail = '') => {
  results.push({ name, ok: !!ok });
  console.log((ok ? 'OK  ' : 'BAD ') + name + (detail ? '  | ' + detail : ''));
};

const headers = { 'X-Auth-Token': process.env.DSH_LAN_TOKEN || '' };
const tasks = await (await fetch(`${API}/api/v1/tasks`, { headers })).json();
// 找一个**消息里带围栏代码块**的任务（真数据）
let pick = null;
let tableTask = null;
for (const t of (tasks ?? []).slice(0, 20)) {
  const evs = await (await fetch(`${API}/api/v1/tasks/${t.id}/events`, { headers })).json();
  for (const e of evs ?? []) {
    const txt = e.type === 'message' ? (e.payload?.text ?? '') : '';
    if (!pick && /```[\s\S]{20,}?```/.test(txt)) pick = { t, text: txt };
    if (!tableTask && /\n\|[^\n]*\|[^\n]*\n\|[\s:|-]+\|/.test(txt)) tableTask = { t, text: txt };
  }
  if (pick && tableTask) break;
}
if (!pick) { console.log('BAD 没找到带代码块的任务'); process.exit(1); }
console.log(`代码块样例：${pick.t.id}；表格样例：${tableTask ? tableTask.t.id : '（没找到，将标 SKIP）'}\n`);

const browser = await chromium.launch({ channel: 'msedge', headless: true });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 950 }, permissions: ['clipboard-read', 'clipboard-write'] });
const page = await ctx.newPage();
await applyLanAuth(page);
const errors = [];
page.on('pageerror', (e) => errors.push(String(e).slice(0, 160)));
page.on('console', (m) => { if (m.type() === 'error') errors.push('[console] ' + m.text().slice(0, 120)); });

await page.goto(BASE, { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(1000);

async function openTask(tid) {
  const items = page.locator('.taskitem');
  for (let i = 0; i < Math.min(await items.count(), 20); i += 1) {
    await items.nth(i).click();
    await page.waitForTimeout(600);
    const sub = await page.locator('.tv-sub').first().innerText().catch(() => '');
    if (sub.includes(tid)) return true;
  }
  return false;
}

check('① 打开到带代码块的任务', await openTask(pick.t.id));
// 那条消息可能被**去重收起**了（↩ 同上一步结果）—— 先展开再找，这也是用户的真实动作
if (await page.locator('.md-code').count() === 0) {
  const dups = page.locator('.dup-row');
  for (let i = 0; i < await dups.count(); i += 1) {
    await dups.nth(i).click().catch(() => undefined);
    await page.waitForTimeout(200);
  }
  await page.waitForTimeout(400);
}
const blocks = page.locator('.md-code');
check('② 代码块有专门外壳', await blocks.count() > 0, `${await blocks.count()} 块`);
if (await blocks.count()) {
  const lang = (await blocks.first().locator('.md-code-lang').innerText().catch(() => '')).trim();
  check('③ 顶部有语言标签', lang.length > 0, `"${lang}"`);
  // 代码块里的原始文本（含换行）
  const raw = await blocks.first().locator('pre code').innerText().catch(() => '');
  await blocks.first().locator('.md-code-copy').click();
  await page.waitForTimeout(400);
  const clip = await page.evaluate(() => navigator.clipboard.readText());
  const norm = (s) => s.replace(/\r\n/g, '\n').replace(/\s+$/g, '');
  check('④ 复制内容 == 代码块原文', norm(clip) === norm(raw),
    `剪贴板 ${clip.length} 字符 / 原文 ${raw.length} 字符`);
  check('⑤ 点过后按钮有反馈（已复制）',
    /已复制/.test(await blocks.first().locator('.md-code-copy').innerText().catch(() => '')));
}

if (tableTask) {
  const ok2 = await openTask(tableTask.t.id);
  const tables = page.locator('.md table');
  check('⑥ GFM 表格渲染成真 <table>', ok2 && await tables.count() > 0,
    ok2 ? `${await tables.count()} 个表格` : '打不开表格样例任务');
  if (await tables.count()) {
    check('⑦ 表格有表头单元格', await page.locator('.md table th').count() > 0);
  }
} else {
  console.log('SKIP ⑥⑦ 手头任务里没有 Markdown 表格样例（渲染能力由 remarkGfm + .md table 样式保证）');
}

check('⑧ 无页面错误', errors.length === 0, errors.slice(0, 2).join(' | '));
await page.screenshot({ path: path.join(SHOTS, 'md-code.png') });
await browser.close();
const bad = results.filter((r) => !r.ok).length;
console.log(`\n结果：${results.length - bad}/${results.length} 通过`);
console.log(`截图：${SHOTS}`);
process.exit(bad === 0 ? 0 : 1);
