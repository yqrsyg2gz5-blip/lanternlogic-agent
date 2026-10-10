// ★ P0-1~P0-5 全流程回归（真群 · 真模型）。
//
// 要验的一串（不只是单测）：
//   组长拆解 → **依赖波次**（有前置的活不许提前开工）→ 交付 → **两级验收** →
//   交接契约（下一位拿到前置的产物路径与摘要）→ 收口 → **账单**
//
// 做法：建一个临时小群（架构师 + 程序员 + 测试），给一个小目标
// （"先定接口契约，再按契约实现" ⇒ 天然产生依赖），然后轮询群消息流。
//
// 用法：cd frontend && node scripts/e2e_team_regression.mjs
import { applyLanAuth } from './_lanauth.mjs';   // 只为统一 token 读取方式
import { writeFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const API = 'http://127.0.0.1:8642';
const H = { 'X-Auth-Token': process.env.DSH_LAN_TOKEN || '', 'Content-Type': 'application/json' };
const GOAL = process.argv[2] || '做一个极简的待办清单命令行工具：**先由架构师定死命令行接口与数据格式（写进 docs/todo-api.md）**，然后程序员严格按那份契约定实现一个可运行的脚本（写进 todo.py），最后测试按契约写一个最小自检脚本（写进 test_todo.py）并真跑一遍。规模要小，别铺开。';

const j = async (p, opt) => {
  const r = await fetch(`${API}${p}`, { headers: H, ...opt });
  const t = await r.text();
  try { return { ok: r.ok, status: r.status, body: JSON.parse(t) }; }
  catch { return { ok: r.ok, status: r.status, body: t }; }
};

// ── 准备：挑员工、建群 ──
// ── 前置自检：后端必须是**新代码**（★ 2026-10-05 实测教训）──
// 第一次跑这场回归时，后端进程还是"波次那批之前"的旧代码 ⇒ 结果是"一把全并行"，
// 而群数据里连 leader_plan 这个键都没有 —— 白跑一场（还烧了 token）。
// 判据：新端点 /team/groups/<不存在>/resume 会回 {"detail":"群不存在"}；
//       老进程没有这个路由 ⇒ FastAPI 回 {"detail":"Not Found"}（两者可区分）。
const probe = await j('/api/v1/team/groups/__probe__/resume', {
  method: 'POST', body: JSON.stringify({ task_id: 'x' }) });
const detail = String(probe.body?.detail || probe.body || '');
if (!detail.includes('群不存在')) {
  console.log(`❌ 后端看起来是旧代码（探测返回：${detail.slice(0, 60)}）——先 restart-backend.ps1 再跑`);
  process.exit(2);
}
console.log('后端是新代码 ✓\n');

const emps = (await j('/api/v1/team/employees')).body.employees ?? [];
const pick = (name) => emps.find((e) => e.name === name);
const arch = pick('架构师'), prog = pick('程序员'), qa = pick('测试工程师') ?? pick('测试');
if (!arch || !prog) { console.log('缺少员工（架构师/程序员），先建员工再跑'); process.exit(1); }
const members = [arch.id, prog.id, ...(qa ? [qa.id] : [])];
const name = `回归验证-${new Date().toISOString().slice(5, 16).replace(/[:T]/g, '')}`;
const g = (await j('/api/v1/team/groups', { method: 'POST', body: JSON.stringify({
  name, members, leader: arch.id, mode: 'leader' }) })).body;
console.log(`群已建：${g.name}（${g.id}）成员 ${members.length} 人 · 组长=${arch.name}`);
console.log(`目标：${GOAL.slice(0, 90)}…\n`);

const say = await j(`/api/v1/team/groups/${g.id}/say`, { method: 'POST', body: JSON.stringify({ text: GOAL }) });
console.log(`派发回执：${JSON.stringify(say.body).slice(0, 220)}\n`);

// ── 轮询群消息（顺带自动放行审批：真群实测一个写脚本的任务会连着要很多次批准）──
const seen = new Set();
const all = [];
const approved = new Set();
const clicks = new Map();      // task_id → 点了几次（验收跑要看"是不是每个任务只点一次"）
let plan0 = [];
const t0 = Date.now();
const MAX_MS = Number(process.env.E2E_MAX_MS || 18 * 60 * 1000);
while (Date.now() - t0 < MAX_MS) {
  const feed = (await j(`/api/v1/team/groups/${g.id}/feed`)).body.messages ?? [];
  for (const m of feed) {
    if (seen.has(m.seq)) continue;
    seen.add(m.seq);
    all.push(m);
    const txt = String(m.text || '').replace(/\n/g, ' ');
    console.log(`  #${String(m.seq).padStart(3)} [${m.from}] ${txt.slice(0, 110)}`);
  }
  // 自动放行：★ 2026-10-05 起用「本任务全部允许」—— 每个任务**只点一次**，
  //   之后这个任务的命令（含 $()/heredoc）不再逐条询问。这里统计"每个任务点了几次"，
  //   用来验证"从 25 次点击降到 1 次"到底成不成立。
  const card = [...feed].reverse().find((m) => m.approval?.call_id && !approved.has(m.approval.call_id));
  if (card) {
    approved.add(card.approval.call_id);
    const tid = String(card.task_id || '');
    clicks.set(tid, (clicks.get(tid) || 0) + 1);
    const r = await j(`/api/v1/team/groups/${g.id}/approve`, { method: 'POST',
      body: JSON.stringify({ task_id: card.task_id, call_id: card.approval.call_id, decision: 'all' }) });
    console.log(`  🔓 放行 ${card.approval.call_id}（本任务全部允许，这个任务第 ${clicks.get(tid)} 次点击）→ ${r.status}`);
  }
  const gs = (await j('/api/v1/team/groups')).body.groups ?? [];
  const cur = gs.find((x) => x.id === g.id) || {};
  plan0 = cur.leader_plan || plan0;
  const st = { total: plan0.length, done: 0, failed: 0, blocked: 0, running: 0, pending: 0 };
  for (const it of plan0) st[it.status] = (st[it.status] || 0) + 1;
  if (st.total && st.done + st.failed + st.blocked >= st.total) {
    console.log(`\n所有项都已收口：${JSON.stringify(st)}`);
    break;
  }
  await new Promise((r) => setTimeout(r, 5000));
}

// ── 结论：拿数据说话 ──
const texts = all.map((m) => String(m.text || ''));
const plan = plan0;
const waveLines = texts.filter((t) => t.includes('第 1 批') || t.includes('第') && t.includes('批开工') || t.includes('先开工'));
const verifyLines = texts.filter((t) => t.includes('验收通过') || t.includes('打回'));
const costLines = texts.filter((t) => t.includes('📊'));
const resumeLines = texts.filter((t) => t.includes('接着跑'));
const blockedLines = texts.filter((t) => t.includes('被前置挡住') || t.includes('挡'));

console.log('\n================ 回归结论 ================');
console.log(`计划项：${plan.map((i) => `${i.name}(${i.status}${(i.depends_on || []).length ? ` ← 等 ${i.depends_on.join('/')}` : ''})`).join('、')}`);
console.log(`批次/开工行：${waveLines.length} 条`);
console.log(`验收行：${verifyLines.length} 条 → ${verifyLines.slice(0, 2).map((t) => t.replace(/\n/g, ' ').slice(0, 70)).join(' ｜ ')}`);
console.log(`账单行：${costLines.length} 条 → ${costLines.slice(-1)[0]?.replace(/\n/g, ' ').slice(0, 130) || '（无）'}`);
console.log(`审批点击：${[...clicks.entries()].map(([t, n]) => `${t.slice(-6)}×${n}`).join(' ') || '0 次'}` +
  `（★ 验收标准：每个任务只点 1 次；旧版实测一个任务 25 次）`);
const accept = texts.filter((t) => t.includes('最后一道门') || t.includes('项目验收'));
console.log(`项目验收门：${accept.length} 条 → ${accept.slice(-1)[0]?.replace(/\n/g, ' ').slice(0, 120) || '（无）'}`);
const struct = texts.filter((t) => t.includes('缺必需小节'));
console.log(`因缺小节被打回：${struct.length} 次`);
console.log(`续跑入口出现：${resumeLines.length > 0 ? '是' : '否'}（失败时才会有）`);
console.log(`挡住提示：${blockedLines.length} 条`);

// ★ 核心断言：有前置的那一项，**在它的前置交付之前不许出现"开始执行"**
let violation = '';
const planOrder = new Map(plan.map((i) => [i.name, i]));
for (const it of plan) {
  for (const dep of it.depends_on || []) {
    const depFirstDone = all.find((m) => String(m.from) === `emp:${dep}` && (m.status === 'done' || m.status === 'partial'));
    const depDeliver = all.find((m) => String(m.from) === `emp:${dep}` && String(m.text || '').includes('交付'));
    const mineStart = all.find((m) => String(m.from) === `emp:${it.name}` && String(m.text || '').includes('开始执行'));
    if (mineStart && depDeliver && mineStart.seq < depDeliver.seq) {
      violation = `${it.name} 在第 ${mineStart.seq} 条就开工了，而前置 ${dep} 的交付在第 ${depDeliver.seq} 条`;
    } else if (mineStart && !depDeliver && depFirstDone && mineStart.seq < depFirstDone.seq) {
      violation = `${it.name} 早于前置 ${dep} 的交付开工（${mineStart.seq} < ${depFirstDone.seq}）`;
    }
  }
}
console.log(violation ? `❌ 波次违规：${violation}` : '✅ 波次：没有任何"前置未交付就开工"的情况');

const out = path.join(os.tmpdir(), `agent-shell-e2e-${g.id}.json`);
writeFileSync(out, JSON.stringify({ group: g, goal: GOAL, plan, messages: all }, null, 2), 'utf-8');
console.log(`\n原始记录：${out}`);
console.log(`群留着给你看：${g.name}（${g.id}）`);
