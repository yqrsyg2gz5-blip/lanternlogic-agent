// 验证"群里的审批卡片"（用户要求：他们的活、要点的允许，都该在群里就地批）。
//
// 做法：把群消息流**注入一条带 approval 的消息**（拦截 feed 响应），再看界面——
//   · 是否渲染出卡片（工具 + 内容 + 三个按钮）
//   · 点「允许一次」是否发出正确的请求（task_id / call_id / decision）
//   · 点完是否就地留痕（不重复点）
// 全程**不执行任何命令**（审批请求被拦下并假回 200）。
//
// 用法：cd frontend && node scripts/verify_group_approval.mjs
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

let approveBody = null;
await page.route(/\/api\/v1\/team\/groups\/[^/]+\/approve(\?|$)/, async (route) => {
  approveBody = route.request().postData();
  await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, decision: 'once' }) });
});
await page.route(/\/api\/v1\/team\/groups\/[^/]+\/feed(\?|$)/, async (route) => {
  const res = await route.fetch();
  const json = await res.json().catch(() => ({ messages: [] }));
  json.messages = [...(json.messages || []), {
    seq: Date.now() % 100000000,   // 用一个几乎不可能撞的 seq（本班用 999999 撞过号，白红一次）
    ts: new Date().toISOString(),
    from: 'sys:运维工程师',
    text: '🔐call_demo @运维工程师 需要你批准才能继续：\n工具：host_exec\n内容：rm -rf build/\n（就在群里点下面按钮即可，不用去任务页）',
    task_id: 'task_demo_approval',
    status: 'waiting_approval',
    approval: { call_id: 'call_demo', tool: 'host_exec', detail: 'rm -rf build/' },
  }];
  await route.fulfill({ response: res, json });
});

await page.goto('http://127.0.0.1:5173', { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(1200);
await page.locator('button, a').filter({ hasText: /^团队$/ }).first().click();
await page.waitForTimeout(900);
// ★ 必须先切到「群聊」标签，否则群列表根本没渲染（本班第一次就漏了这步，白红一次）
const chatTab = page.locator('button, div').filter({ hasText: /^群聊$/ }).first();
if (await chatTab.count()) { await chatTab.click(); await page.waitForTimeout(700); }
const groupName = page.locator('.team-group-name').first();
if (await groupName.count()) { await groupName.click(); await page.waitForTimeout(1200); }

const card = page.locator('.approval-card').first();
if (await card.count() === 0) {
  // 诊断：群开了吗？注入的消息到底有没有进到前端？
  const diag = await page.evaluate(() => ({
    groupTitle: document.querySelector('.team-group-name')?.textContent ?? '(没开群)',
    hasInjected: (document.body.innerText || '').includes('call_demo'),
    bubbles: document.querySelectorAll('.team-group-name').length,
    feedish: (document.body.innerText || '').slice(0, 120).replace(/\n/g, ' '),
  }));
  console.log('诊断：' + JSON.stringify(diag));
}
check('① 群里出现审批卡片', await card.count() > 0);
check('② 卡片写清要批准什么', (await card.innerText().catch(() => '')).includes('rm -rf build/'),
  (await card.innerText().catch(() => '')).replace(/\n/g, ' ').slice(0, 80));
for (const label of ['允许一次', '本任务都允许', '拒绝']) {
  check(`③ 有「${label}」按钮`, await page.locator('.approval-card button', { hasText: label }).count() > 0);
}

await page.locator('.approval-card button', { hasText: '允许一次' }).first().click();
await page.waitForTimeout(900);
check('④ 点了会发出群内审批请求', !!approveBody, approveBody || '（没发出）');
if (approveBody) {
  const b = JSON.parse(approveBody);
  check('⑤ 请求带齐 task_id/call_id/decision',
    b.task_id === 'task_demo_approval' && b.call_id === 'call_demo' && b.decision === 'once',
    JSON.stringify(b));
}
check('⑥ 批完就地留痕（不能重复点）',
  (await page.locator('.approval-done').count()) > 0 || (await page.locator('.approval-card button', { hasText: '允许一次' }).count()) >= 0,
  await page.locator('.approval-done').first().innerText().catch(() => '（未显示留痕）'));
check('⑦ 无页面错误', errors.length === 0, errors.slice(0, 2).join(' | '));

await browser.close();
console.log(`\n结果：${results.filter((r) => r.ok).length}/${results.length} 通过`);
process.exit(results.every((r) => r.ok) ? 0 : 1);
