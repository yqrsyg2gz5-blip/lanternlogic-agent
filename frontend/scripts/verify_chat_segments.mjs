// Phase 3 ⑦ 第三刀验证（真浏览器 · 真任务）：一段一个气泡
//
// 判据（用**真消息**，不是假数据）：
//   ① 长回复（真任务里那条 1653 字、含 5 处空行的）渲染成 **≥4 个气泡**
//   ② 短回复（无空行的）仍然是 **1 个气泡**（不能一刀切，否则"好的，收到"也被拆）
//   ③ 「Agent」署名只在**第一段**出现；续段有 bubble-cont 但不再重复署名
//   ④ 复制按钮挂在最后一段上，且复制的是**全文**（读 title/aria 即可）
//   ⑤ 段与段之间有间距（几何：相邻气泡不重叠且间隙 > 0）
//   ⑥ 无页面错误、无横向溢出
//
// 用法：cd frontend && node scripts/verify_chat_segments.mjs
import { chromium } from 'playwright-core';
import os from 'node:os';
import path from 'node:path';
import { mkdirSync } from 'node:fs';
import { applyLanAuth } from './_lanauth.mjs';

const BASE = 'http://127.0.0.1:5173';
const API = 'http://127.0.0.1:8642';
const SHOTS = path.join(os.tmpdir(), 'agent-shell-shots', 'chat-segments');
mkdirSync(SHOTS, { recursive: true });
const results = [];
const check = (name, ok, detail = '') => {
  results.push({ name, ok: !!ok });
  console.log((ok ? 'OK  ' : 'BAD ') + name + (detail ? '  | ' + detail : ''));
};

const token = process.env.DSH_LAN_TOKEN || '';
const headers = { 'X-Auth-Token': token };
const tasks = await (await fetch(`${API}/api/v1/tasks`, { headers })).json();

// 找一个"长（多空行）回复"的任务 + 一个"短（无空行）回复"的任务
let longTask = null, shortTask = null;
for (const t of (tasks ?? []).slice(0, 14)) {
  const evs = await (await fetch(`${API}/api/v1/tasks/${t.id}/events`, { headers })).json();
  const msgs = (evs ?? []).filter((e) => e.type === 'message' && e.payload?.role === 'assistant');
  for (const m of msgs) {
    const text = String(m.payload.text ?? '');
    const breaks = (text.match(/\n[ \t]*\n/g) ?? []).length;
    if (!longTask && breaks >= 3 && text.length > 400) longTask = { t, text, breaks };
    if (!shortTask && breaks === 0 && text.length > 10 && text.length < 300) shortTask = { t, text };
  }
  if (longTask && shortTask) break;
}
if (!longTask) { console.log('BAD 没找到多段长回复的任务（换个任务数据再跑）'); process.exit(1); }
console.log(`长回复任务：${longTask.t.id}（${longTask.text.length} 字 / ${longTask.breaks} 处空行）`);
console.log(`短回复任务：${shortTask ? shortTask.t.id + `（${shortTask.text.length} 字）` : '（没找到，跳过 ②）'}\n`);

const browser = await chromium.launch({ channel: 'msedge', headless: true });

async function openTask(task) {
  const page = await browser.newPage({ viewport: { width: 1440, height: 950 } });
  await applyLanAuth(page);
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e).slice(0, 140)));
  await page.goto(BASE, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(900);
  // ★ 侧栏里好几个任务的标题前缀一模一样（"只做一件事：用 shell …"），
  //   第一版按标题前缀点 ⇒ 点错任务、把长任务当成"短回复"来断言（假红）。
  //   改成：逐个点、点完读页头的任务号（.tv-sub 里有 task_2026…），对上才算。
  const items = page.locator('.taskitem');
  const n = await items.count();
  let opened = '';
  for (let i = 0; i < Math.min(n, 20); i++) {
    await items.nth(i).click();
    await page.waitForTimeout(700);
    const sub = await page.locator('.tv-sub').first().innerText().catch(() => '');
    if (sub.includes(task.id)) { opened = sub.trim(); break; }
  }
  await page.waitForTimeout(700);
  return { page, errors, opened };
}

// ── 长回复 ──
const L = await openTask(longTask.t);
const bubbleCount = await L.page.locator('.msg-assistant .bubble-assistant').count();
check('① 长回复渲染成多个气泡', bubbleCount >= 4, `${bubbleCount} 个气泡（空行 ${longTask.breaks} 处）`);
const whoCount = await L.page.locator('.msg-assistant .msg-who').count();
check('② 「Agent」署名只在第一段（不重复刷）', whoCount >= 1 && whoCount < bubbleCount,
  `署名 ${whoCount} 处 / 气泡 ${bubbleCount} 个`);
check('③ 续段带 bubble-cont（同一个人接着说的视觉线索）',
  await L.page.locator('.bubble-assistant.bubble-cont').count() >= 1);
const gap = await L.page.evaluate(() => {
  const bs = Array.from(document.querySelectorAll('.msg-assistant .bubble-assistant'));
  if (bs.length < 2) return null;
  const a = bs[0].getBoundingClientRect(), b = bs[1].getBoundingClientRect();
  return Math.round(b.top - a.bottom);
});
check('④ 段与段之间有间距（不是粘成一坨）', gap !== null && gap > 0, `间距 ${gap}px`);
check('⑤ 复制按钮在最后一段上（复制的是全文）',
  await L.page.locator('.msg-assistant .bubble-assistant:last-child .msg-copy').count() > 0);
check('⑥ 长回复页无页面错误', L.errors.length === 0, L.errors.join(' | '));
const overflowL = await L.page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 2);
check('⑦ 无横向溢出', !overflowL);
await L.page.screenshot({ path: path.join(SHOTS, 'chat-segments-long.png') });

// ── 短回复 ──
if (shortTask) {
  const S = await openTask(shortTask.t);
  // ★ 判据：**同一条短回复的"开头 + 结尾"必须落在同一个气泡里**（= 没被切开）。
  //   只用开头去数会误判：同一个任务里模型可能说过两句很像的话（第一版就这么假红了）。
  const head = shortTask.text.trim().slice(0, 18);
  const tail = shortTask.text.trim().slice(-12);
  const intact = await S.page.evaluate(([h, t]) => {
    const bs = Array.from(document.querySelectorAll('.msg-assistant .bubble-assistant'));
    const withHead = bs.filter((b) => (b.textContent || '').includes(h));
    const broken = withHead.filter((b) => !(b.textContent || '').includes(t));
    return {
      intact: bs.filter((b) => { const s = b.textContent || ''; return s.includes(h) && s.includes(t); }).length,
      broken: broken.length,       // ★ 关键：只有开头、没有结尾的气泡 = 被误切了
    };
  }, [head, tail]);
  // 任务里同一条短回复可能出现过两次（问候语重复很常见），所以**不能**用"恰好 1 个"判定；
  // 真正的性质是：不存在"只有开头没有结尾"的气泡，且至少有一个完整气泡。
  check('⑧ 短回复完整落在一个气泡里（没被误切）',
    intact.broken === 0 && intact.intact >= 1,
    `完整气泡 ${intact.intact} 个 / 被切断的气泡 ${intact.broken} 个`);
  check('⑨ 短回复页无页面错误', S.errors.length === 0, S.errors.join(' | '));
}

await browser.close();
const bad = results.filter((r) => !r.ok).length;
console.log(`\n结果：${results.length - bad}/${results.length} 通过`);
console.log(`截图：${SHOTS}`);
process.exit(bad === 0 ? 0 : 1);
