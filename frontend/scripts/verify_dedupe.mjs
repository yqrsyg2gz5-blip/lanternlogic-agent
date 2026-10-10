// 应用层去重验证（真浏览器 · 真任务）：重复的回复收成一行 + 总账不再抄全文
//
// 背景（实测 task_20261004_98f9c932）：同一句 61 字在界面上出现 4 次 ——
//   #7 工具输出 → #12 模型"原样贴回来" → #16 task_done 参数 → #18 交付语
// 判据：
//   ① 与上面工具结果基本相同的助手回复，默认**收成一行**（"同上一步结果（重复内容已收起）"）
//   ② 点开能看到**全文**（不丢数据）
//   ③ task_done 卡片摘要只留预览（"交付：<前 20 字>…"），不再抄全文
//   ④ 总账里的「它最后说的」是一行指引（含"点这里跳过去"），不再是长篇
//   ⑤ 真正的新内容（不是重复的回复）**照常完整显示**（不能把正常回复也收掉）
//   ⑥ 无页面错误
//
// 用法：cd frontend && node scripts/verify_dedupe.mjs
import { chromium } from 'playwright-core';
import os from 'node:os';
import path from 'node:path';
import { mkdirSync } from 'node:fs';
import { applyLanAuth } from './_lanauth.mjs';

const BASE = 'http://127.0.0.1:5173';
const API = 'http://127.0.0.1:8642';
const SHOTS = path.join(os.tmpdir(), 'agent-shell-shots', 'dedupe');
mkdirSync(SHOTS, { recursive: true });
const results = [];
const check = (name, ok, detail = '') => {
  results.push({ name, ok: !!ok });
  console.log((ok ? 'OK  ' : 'BAD ') + name + (detail ? '  | ' + detail : ''));
};

const token = process.env.DSH_LAN_TOKEN || '';
const headers = { 'X-Auth-Token': token };
const tasks = await (await fetch(`${API}/api/v1/tasks`, { headers })).json();

// 找一个"回复基本等于工具结果"的任务（就是那个 A3 探针），再找一个"正常长回复"的任务
// 侧栏标题会重名（好几个任务都叫"只做一件事：用 shell…"），点开哪个不确定 ⇒
// 备几个候选任务，谁能看到"收起行"就算谁（第一版只认一个，点错就假红）
const dupTasks = [];
let normalTask = null;
for (const t of (tasks ?? []).slice(0, 14)) {
  const evs = await (await fetch(`${API}/api/v1/tasks/${t.id}/events`, { headers })).json();
  const obs = (evs ?? []).filter((e) => e.type === 'observation').map((e) => e.payload.result || '');
  for (const e of (evs ?? [])) {
    if (e.type !== 'message' || e.payload?.role !== 'assistant') continue;
    const text = String(e.payload.text ?? '');
    // 挑样本用"回复里含一段 ≥40 字的工具结果"，与前端判定同口径（整数窗太粗会漏判）
    const dupish = obs.some((o) => {
      const t = text.replace(/\s/g, '');
      const oo = o.replace(/\s/g, '');
      if (oo.length < 40) return false;
      for (let i = 0; i + 40 <= oo.length; i += 8) if (t.includes(oo.slice(i, i + 40))) return true;
      return false;
    });
    if (text.length > 60 && dupish && !dupTasks.some((x) => x.t.id === t.id)) dupTasks.push({ t, text });
    if (!normalTask && text.length > 300 && !dupish) normalTask = { t, text };
  }
  if (dupTasks.length >= 3 && normalTask) break;
}
if (!dupTasks.length) { console.log('BAD 没找到「重复回复」的任务（跑一个 echo 类任务再试）'); process.exit(1); }
console.log(`重复候选：${dupTasks.map((x) => x.t.id).join(', ')}`);
console.log(`正常样例：${normalTask ? normalTask.t.id + `（${normalTask.text.length} 字）` : '（没找到，跳过 ⑤）'}\n`);

const browser = await chromium.launch({ channel: 'msedge', headless: true });

async function open(task) {
  const page = await browser.newPage({ viewport: { width: 1440, height: 950 } });
  await applyLanAuth(page);
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e).slice(0, 140)));
  await page.goto(BASE, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(900);
  const items = page.locator('.taskitem');
  const n = await items.count();
  for (let i = 0; i < Math.min(n, 20); i++) {
    await items.nth(i).click();
    await page.waitForTimeout(600);
    const sub = await page.locator('.tv-sub').first().innerText().catch(() => '');
    if (sub.includes(task.id)) break;
  }
  await page.waitForTimeout(900);
  return { page, errors };
}

// ── ① 重复回复收起（在**若干候选任务**里找一个能看到的 —— 侧栏标题重名，
//        单点某个任务打开常常会点错，第一版就因此假红过）──
let D = null, dupRows = 0, openedId = '';
for (const cand of dupTasks.slice(0, 4)) {
  const r = await open(cand.t);
  const n = await r.page.locator('.dup-row').count();
  openedId = r.opened;
  if (n > 0) { D = r; dupRows = n; break; }
  if (!D) D = r;                       // 都没收起来时留第一个做后续断言
}
check('① 重复的回复默认收成一行（候选任务里至少有一个能看到）', dupRows >= 1,
  `实际打开 ${openedId || '(未确认)'}；收起行 ${dupRows} 行`);
if (dupRows) {
  const rowText = (await D.page.locator('.dup-row').first().innerText()).replace(/\s+/g, ' ');
  check('② 那行说清了是什么', /同上一步结果/.test(rowText) && /字/.test(rowText), rowText.slice(0, 80));
  await D.page.locator('.dup-row').first().click();
  await D.page.waitForTimeout(300);
  const fullVisible = await D.page.evaluate(() => {
    const b = Array.from(document.querySelectorAll('.msg-assistant .bubble-assistant'));
    return b.some((x) => (x.textContent || '').includes('A3-LIVE-PROBE'));
  });
  check('③ 点开能看到全文（不丢数据）', fullVisible);
}
// task_done 卡片摘要只留预览
const doneLine = await D.page.evaluate(() => {
  const rows = Array.from(document.querySelectorAll('.ev-action'));
  for (const r of rows) {
    if ((r.textContent || '').includes('task_done')) {
      const cmd = r.querySelector('.ev-cmd');
      return cmd ? (cmd.textContent || '') : '';
    }
  }
  return '';
});
check('④ task_done 卡片只留预览（本页若有该卡片）',
  !doneLine || (/交付：/.test(doneLine) && doneLine.length < 40), `"${doneLine}"（空=本页没这张卡）`);

// 总账：最后一条回复改成一行指引
await D.page.locator('.digest > summary').click();
await D.page.waitForTimeout(300);
const digestLast = await D.page.evaluate(() => {
  const j = document.querySelector('.digest-jump');
  return j ? (j.textContent || '').replace(/\s+/g, ' ') : '';
});
check('⑤ 总账「它最后说的」是一行指引（不是长篇）',
  /点这里跳过去/.test(digestLast) && digestLast.length < 130, digestLast.slice(0, 90));
check('⑥ 重复样例页无页面错误', D.errors.length === 0, D.errors.join(' | '));
await D.page.screenshot({ path: path.join(SHOTS, 'dedupe-collapsed.png') });

// ── ② 正常回复不能被误收 ──
if (normalTask) {
  const N = await open(normalTask.t);
  const stat = await N.page.evaluate(() => ({
    dup: document.querySelectorAll('.dup-row').length,
    bubbles: document.querySelectorAll('.msg-assistant .bubble-assistant').length,
  }));
  check('⑦ 正常长回复照常完整显示（没被误收）',
    stat.bubbles >= 1 && stat.dup === 0, `气泡 ${stat.bubbles} 个 / 收起行 ${stat.dup} 个`);
  check('⑧ 正常样例页无页面错误', N.errors.length === 0, N.errors.join(' | '));
}

await browser.close();
const bad = results.filter((r) => !r.ok).length;
console.log(`\n结果：${results.length - bad}/${results.length} 通过`);
console.log(`截图：${SHOTS}`);
process.exit(bad === 0 ? 0 : 1);
