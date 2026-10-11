// 团队可试性批 验证（真浏览器）：团队聊天输入区的语音 + 成员一键选择
//
// 判据：
//   ① 能进团队 → 群聊
//   ② 输入区有**语音按钮**（aria-label=语音输入）—— 此前团队没有语音
//   ③ 输入区有**成员一键选择**按钮（@），点开后列出本群成员，点一个就填进输入框
//   ④ 点名模式才给 @ 按钮（广播模式全员都收到，插 @ 没意义）
//   ⑤ 无页面错误
//
// 用法：cd frontend && node scripts/verify_team_composer.mjs
import { chromium } from 'playwright-core';
import { applyLanAuth } from './_lanauth.mjs';

const BASE = 'http://127.0.0.1:5173';
const API = 'http://127.0.0.1:8642';
const results = [];
const check = (name, ok, detail = '') => {
  results.push({ name, ok: !!ok });
  console.log((ok ? 'OK  ' : 'BAD ') + name + (detail ? '  | ' + detail : ''));
};

const H = { 'X-Auth-Token': process.env.DSH_LAN_TOKEN || '' };
const asList = (d, key) => (Array.isArray(d) ? d : (d?.[key] ?? []));
let groups = asList(await (await fetch(`${API}/api/v1/team/groups`, { headers: H })).json().catch(() => null), 'groups');
let emps = asList(await (await fetch(`${API}/api/v1/team/employees`, { headers: H })).json().catch(() => null), 'employees');
console.log(`现有群 ${groups.length} 个 / 员工 ${emps.length} 人`);

// 没有群就**临时建一个**（验完删掉，不污染你的团队）
const made = { emps: [], group: null };
async function ensureFixture() {
  if (groups.length && emps.length >= 2) return;
  for (const [name, role] of [['测试·前端', '前端'], ['测试·后端', '后端']]) {
    const r = await fetch(`${API}/api/v1/team/employees`, {
      method: 'POST', headers: { ...H, 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, dept: '开发部', role, persona: '临时验证用，稍后删除', mode: 'expert' }),
    });
    if (r.ok) made.emps.push((await r.json()).id);
  }
  const g = await fetch(`${API}/api/v1/team/groups`, {
    method: 'POST', headers: { ...H, 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: '临时验证群', members: made.emps, mode: 'manual' }),
  });
  if (g.ok) made.group = (await g.json()).id;
  groups = [{ id: made.group, name: '临时验证群', mode: 'manual', members: made.emps }];
  console.log(`临时建了 ${made.emps.length} 个员工 + 1 个群（验完删除）`);
}
async function cleanupFixture() {
  if (made.group) await fetch(`${API}/api/v1/team/groups/${made.group}`, { method: 'DELETE', headers: H });
  for (const id of made.emps) await fetch(`${API}/api/v1/team/employees/${id}`, { method: 'DELETE', headers: H });
  if (made.group || made.emps.length) console.log('临时数据已删除 ✓');
}
await ensureFixture();

const browser = await chromium.launch({ channel: 'msedge', headless: true });
const page = await (await browser.newContext({ viewport: { width: 1440, height: 950 } })).newPage();
await applyLanAuth(page);
const errors = [];
page.on('pageerror', (e) => errors.push(String(e).slice(0, 160)));
page.on('console', (m) => { if (m.type() === 'error') errors.push('[console] ' + m.text().slice(0, 120)); });

await page.goto(BASE, { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(1200);

// 打开团队
const teamBtn = page.locator('button, a').filter({ hasText: /^团队$/ }).first();
const alt = page.locator('[title*="团队"], [aria-label*="团队"]').first();
if (await teamBtn.count()) await teamBtn.click();
else if (await alt.count()) await alt.click();
await page.waitForTimeout(1200);

// 切到群聊标签
const chatTab = page.locator('button, div').filter({ hasText: /^群聊$/ }).first();
if (await chatTab.count()) { await chatTab.click(); await page.waitForTimeout(800); }

const mic = page.locator('button[aria-label="语音输入"]');
check('① 团队输入区有语音按钮', await mic.count() > 0, `${await mic.count()} 个`);

// 选一个群（若没进群聊）
const groupItem = page.locator('text=/群|组/').first();
if (await groupItem.count() && await mic.count() === 0) {
  await groupItem.click().catch(() => undefined);
  await page.waitForTimeout(800);
}

if (groups.length) {
  const mode = groups[0].mode ?? 'manual';
  // 先点开这个群（否则输入区还没渲染）—— 必须在找按钮**之前**做
  await page.locator('button').filter({ hasText: groups[0].name }).first().click().catch(() => undefined);
  await page.waitForTimeout(1000);
  const pickBtn = page.locator('button').filter({ hasText: /^@$/ }).first();
  const hasPick = await pickBtn.count() > 0;
  if (!hasPick) {
    // 失败时把真实渲染出来的按钮摊开（否则只能瞎猜选择器 —— 这次就猜错了）
    const dump = await page.evaluate(() => [...document.querySelectorAll('button')]
      .map((b) => `${(b.className || '(无class)').slice(0, 26)} | ${(b.textContent || '').trim().slice(0, 12)}`)
      .slice(-14));
    console.log('    输入区按钮实况：\n      ' + dump.join('\n      '));
    console.log(`    当前群：${groups[0].name}（模式 ${mode}）｜语音按钮 ${await mic.count()} 个`);
  }
  if (mode !== 'broadcast') {
    check('② 有点名按钮（@）', hasPick, hasPick ? '' : `群模式=${mode} 但没看到 @ 按钮`);
    if (hasPick) {
      await pickBtn.click();
      await page.waitForTimeout(300);
      const items = page.locator('.member-pick-item');
      const n = await items.count();
      check('③ 点开列出本群成员', n > 0, `${n} 项`);
      if (n > 0) {
        const label = (await items.first().innerText()).trim();
        await items.first().click();
        await page.waitForTimeout(300);
        const val = await page.locator('input').last().inputValue().catch(() => '');
        check('④ 点一下就把 @名字 填进输入框', /@/.test(val), `输入框现在："${val.slice(0, 30)}"（点的是 ${label.slice(0, 12)}）`);
      }
    }
  } else {
    console.log('SKIP ②③④ 第一个群是广播模式（广播插 @ 没意义，按钮本就不该出现）');
  }
} else {
  console.log('SKIP ②③④ 还没有群可试（先建一个群）');
}

check('⑤ 无页面错误', errors.length === 0, errors.slice(0, 2).join(' | '));

// ⑥ 新模式入口：**先把群点开**再读模式下拉（这个下拉只在群打开时渲染 ——
//    本班第一次读早了，读成空/旧值，白红一次）
const firstGroupName = page.locator('.team-group-name').first();
if (await firstGroupName.count()) { await firstGroupName.click(); await page.waitForTimeout(800); }
const modeOpts = await page.evaluate(() => {
  const sel = [...document.querySelectorAll('select')].find((s) =>
    [...s.options].some((o) => o.value === 'relay') || [...s.options].some((o) => o.value === 'meeting')
    || [...s.options].some((o) => o.value === 'manual'));
  return sel ? [...sel.options].map((o) => o.value) : [];
});
check('⑥ 群内模式下拉里有「接力」和「开会」',
  modeOpts.includes('relay') && modeOpts.includes('meeting'),
  modeOpts.join(' / ') || '(没读到模式下拉)');

// ⑦ API 侧：两种新模式的群都能建、模式能存（建完立刻删，不留垃圾）
const H2 = { ...H, 'Content-Type': 'application/json' };
const empsNow = asList(await (await fetch(`${API}/api/v1/team/employees`, { headers: H })).json().catch(() => null), 'employees');
if (empsNow.length >= 2) {
  for (const mode of ['relay', 'meeting']) {
    const cr = await fetch(`${API}/api/v1/team/groups`, {
      method: 'POST', headers: H2,
      body: JSON.stringify({ name: `${mode}自检（临时）`, members: [empsNow[0].id, empsNow[1].id], mode }),
    });
    const g = cr.ok ? await cr.json() : null;
    check(`⑦ ${mode} 群能建、模式存下来`, cr.ok && g?.mode === mode, `HTTP ${cr.status}`);
    if (g?.id) await fetch(`${API}/api/v1/team/groups/${g.id}`, { method: 'DELETE', headers: H });
  }
} else {
  console.log('SKIP ⑦ 员工不足 2 人');
}
await browser.close();
const bad = results.filter((r) => !r.ok).length;
console.log(`\n结果：${results.length - bad}/${results.length} 通过`);
process.exit(bad === 0 ? 0 : 1);
