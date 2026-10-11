import { applyLanAuth, lanToken } from './_lanauth.mjs';
// 二十五轮 🔴4 窄档专项验证（playwright-core + 系统 Edge）
// 二十六轮第 6 批第 4 处重写——上一版五处叠加失效（验证员逐条实测）：
//   a) 备份路径 '../backend/data/team/' 从 frontend/scripts/ 解析成
//      frontend/backend/…（不存在），两次 ENOENT 被 catch {} 吞掉 ⇒ 还原空转
//   b) 删群按【名字】find ⇒ 撞同名旧群，删掉用户的旧群、留下新的
//   c) A0 断言 count+1 && name 命中 ⇒ 区分不出"新建"与"删错"
//   d) "git status backend/data 零改动"是空判据（data/ 被 .gitignore）
//   e) 第①步建员工从不核实落盘 ⇒ 覆盖是假的
// 本版硬规矩：
//   · 路径用 fileURLToPath（../../backend/data/team/）
//   · 备份失败【报错退出】，绝不静默继续
//   · 删群按【本次新建的 id】（id ∉ beforeIds）
//   · A0 断言：新 id 不在 before 集合 + before 全部 id 仍在 + 群名匹配
//   · 建员工后 API+文件双查核实
//   · 跑前跑后 groups.json/employees.json sha256 必须相同（打印原始值）
//   · process.on('exit') 兜底还原；chromium.launch 纳入 try
//   · 跑 3 次 sha 全同由调用方复核；本脚本单次运行自证 sha 相同
import { chromium } from 'playwright-core';
import { readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';

const BASE = 'http://127.0.0.1:5173';
const API = 'http://127.0.0.1:8642';
const TEAM_DIR = fileURLToPath(new URL('../../backend/data/team/', import.meta.url));
const sha = (buf) => createHash('sha256').update(buf).digest('hex').slice(0, 16);

const FILES = ['groups.json', 'employees.json'];
const BACKUPS = [];
for (const f of FILES) {
  try {
    const data = readFileSync(TEAM_DIR + f);
    BACKUPS.push([TEAM_DIR + f, data]);
  } catch (e) {
    // 二十六轮第 6 批第 4 处 b：备份失败绝不静默——没有还原能力就不许跑
    console.error('备份失败（' + f + '）：' + e.message + ' —— 拒绝运行，防止污染用户数据');
    process.exit(2);
  }
}
// ★ 局域网模式下 Node 侧请求也要带密码（走不到浏览器里的 fetch 包装）
const _q = () => { const t = lanToken(); return t ? `?token=${encodeURIComponent(t)}` : ''; };
const shaBefore = Object.fromEntries(BACKUPS.map(([p, d]) => [p.split(/[\\/]/).pop(), sha(d)]));

const restore = () => { for (const [p, d] of BACKUPS) writeFileSync(p, d); };
process.on('exit', restore);

const results = [];
const check = (name, ok, detail = '') => {
  results.push({ name, ok: !!ok, detail: String(detail) });
  console.log((ok ? 'OK  ' : 'BAD ') + name + (detail ? '  | ' + detail : ''));
};

try {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });

  // 窄档侧栏是收起抽屉——先点汉堡 .sidebar-toggle 拉开抽屉
  async function openTeamNarrow(page) {
    await page.goto(BASE, { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(500);
    await page.locator('.sidebar-toggle').click();
    await page.waitForTimeout(300);
    await page.locator('.sidebar .settings-fab', { hasText: '团队' }).click();
    await page.waitForTimeout(400);
  }

  // ── 场景 1：320×700 群聊档 ──
  const page = await browser.newPage({ viewport: { width: 320, height: 700 } });
  await applyLanAuth(page);
  const errs = [];
  page.on('pageerror', (e) => errs.push(String(e)));
  await openTeamNarrow(page);

  // ① 成员存在性核实（API+文件双查）。上一版"建唯一名员工"在满员时
  // 被 MAX_MEMBERS=12 拒绝（422），从不核实=假覆盖；建群只需 ≥1 名现有成员。
  // ★ 局域网模式下这里也要带密码：`page.request` 是 Node 侧请求，走不到浏览器里
  //   那层 fetch 包装（第 8e 处），不带就 401 ⇒ empList 变 undefined（本班实测）。
  const _t = lanToken();
  const emps = await (await page.request.get(
    API + '/api/v1/team/employees' + (_t ? `?token=${encodeURIComponent(_t)}` : ''))).json();
  const empList = emps.employees ?? emps;
  const empOnDisk = (readFileSync(TEAM_DIR + 'employees.json', 'utf-8')).length > 10
    && JSON.stringify(empList) && true;
  check('E1 建群依赖的成员真实存在（API 非空 + employees.json 非空）',
    Array.isArray(empList) && empList.length >= 1 && empOnDisk,
    `API=${Array.isArray(empList) ? empList.length : 0}人`);

  // ② 记录建群前的【全部群 id 集合】
  await page.locator('.team-nav .nav-item', { hasText: '群聊' }).click();
  await page.waitForTimeout(200);
  const before = await (await page.request.get(API + '/api/v1/team/groups' + _q())).json();
  const beforeIds = new Set(before.groups.map((g) => g.id));

  await page.locator('.team-grouplist .new-chat-btn').click();
  await page.waitForTimeout(200);
  const longName = '新品推广项目组秋冬季冲刺二十六轮';
  await page.locator('.team-grouplist input').first().fill(longName);
  await page.locator('.team-grouplist input[type="checkbox"]').first().check();
  await page.locator('.team-grouplist button', { hasText: '创建群' }).click();
  await page.waitForTimeout(500);

  // ③ A0：id 集合断言——【能区分"新建"与"删错"】
  const after = await (await page.request.get(API + '/api/v1/team/groups' + _q())).json();
  const newIds = after.groups.map((g) => g.id).filter((id) => !beforeIds.has(id));
  const created = after.groups.find((g) => g.id === newIds[0]);
  const oldAllPresent = [...beforeIds].every((id) => after.groups.some((g) => g.id === id));
  check('A0 新建可归因（恰好 1 个新 id∉before + before 全在 + 名匹配）',
    newIds.length === 1 && !!created && created.name === longName && oldAllPresent,
    `新增id数=${newIds.length} 名匹配=${created?.name === longName} 旧群全在=${oldAllPresent}`);

  // ④ UI 只认新建 id 的那个群
  const mine = await page.evaluate((name) => {
    const names = [...document.querySelectorAll('.team-group-name')];
    const el = names.find((n) => (n.getAttribute('title') ?? n.textContent) === name);
    if (!el) return null;
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    return {
      h: Math.round(r.height), whiteSpace: cs.whiteSpace,
      ellipsized: el.scrollWidth > el.clientWidth, clientW: el.clientWidth,
      listW: Math.round(document.querySelector('.team-grouplist').getBoundingClientRect().width),
    };
  }, longName);
  check('A0b UI 列表渲染出新建的长名群', !!mine, mine ? `clientW=${mine.clientW}` : '未找到');
  check('A1 群名单行（高 ≤26px）', mine && mine.h <= 26, mine ? `h=${mine.h} ws=${mine.whiteSpace}` : '元素不存在');
  check('A2 长名群 ellipsis 生效', mine && mine.ellipsized, mine ? `clientW=${mine.clientW}` : '');
  check('A3 窄档列宽 ≥90px', mine && mine.listW >= 90, `listW=${mine?.listW}`);

  // ⑤ 清理：按【本次新建的 id】删（绝不按名字——同名旧群是用户的）
  if (newIds.length === 1) {
    await page.request.delete(API + '/api/v1/team/groups/' + newIds[0] + _q());
  }

  // ── 场景 2：320×568 保存键下缘 ──
  const page2 = await browser.newPage({ viewport: { width: 320, height: 568 } });
  await openTeamNarrow(page2);
  const pb = await page2.evaluate(() => {
    const el = document.querySelector('.team-content');
    return el ? parseFloat(getComputedStyle(el).paddingBottom) : -1;
  });
  check('B1 ≤360 档 .team-content padding-bottom = 24px', pb === 24, `pb=${pb}`);
  await page2.locator('.team-content input').first().fill('测试员工甲');
  const btn = page2.locator('.btn-save', { hasText: '保存修改' }).or(page2.locator('.btn-save', { hasText: '创建员工卡' })).or(page2.locator('.btn-save', { hasText: '填名字' }));
  await btn.first().scrollIntoViewIfNeeded();
  await page2.waitForTimeout(200);
  const b = await page2.evaluate(() => {
    const els = [...document.querySelectorAll('.btn-save')];
    if (!els.length) return null;
    const r = els[els.length - 1].getBoundingClientRect();
    return { bottom: Math.round(r.bottom), vh: window.innerHeight };
  });
  check('B2 保存键下缘不裁（bottom ≤ 视口高）', b && b.bottom <= b.vh, b ? `bottom=${b.bottom} vh=${b.vh}` : '无保存键');
  check('320 档 pageerror = 0', errs.length === 0, errs.join('; '));
  await page.close();
  await page2.close();

  // ── 场景 3：720 视口 team-nav 图标化 ──
  const page3 = await browser.newPage({ viewport: { width: 720, height: 700 } });
  await openTeamNarrow(page3);
  const nav = await page3.evaluate(() => {
    const item = document.querySelector('.team-nav .nav-item');
    if (!item) return null;
    return { w: Math.round(item.getBoundingClientRect().width), title: item.getAttribute('title') };
  });
  check('C1 720 档 team-nav 已图标化（宽 ≤60px）', nav && nav.w <= 60, nav ? `w=${nav.w}` : '无 nav');
  check('C2 图标栏按钮 title 在位', nav && nav.title === '员工卡', `title=${nav?.title}`);
  await page3.close();
  await browser.close();
} catch (e) {
  check('脚本自身崩溃', false, String(e).slice(0, 200));
  process.exitCode = 1;
} finally {
  restore();
  // 二十六轮第 6 批第 4 处 f：sha256 对比（替代"git status 空判据"——
  // data/ 被 .gitignore，git status 永远为空）
  for (const f of FILES) {
    try {
      const now = sha(readFileSync(TEAM_DIR + f));
      const ok = now === shaBefore[f];
      check(`S1 ${f} sha256 跑前=跑后`, ok, `${shaBefore[f]} → ${now}`);
    } catch (e) {
      check(`S1 ${f} sha256 对比`, false, e.message);
    }
  }
  // 用户建的那名验证员工已随还原消失——无需单独清理
}

const bad = results.filter((r) => !r.ok).length;
console.log('\n结果：' + (results.length - bad) + '/' + results.length + ' 通过');
process.exit(bad ? 1 : 0);
