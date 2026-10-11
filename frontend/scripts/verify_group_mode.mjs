import { applyLanAuth } from './_lanauth.mjs';
// 验证：群聊「派发模式」建群后可以改（二十六轮第 7 批 问题7c）
//
// 修前：群头部只是个只读徽章（MODE_LABEL[curGroup.mode]），想换模式只能删群重建，
//       而删群会连聊天记录一起删（delete_group 里 unlink feed_*.json）。
// 修后：徽章 → 下拉；改完即时 PUT /api/v1/team/groups/{gid} 并刷新。
//
// ★ 安全（施工纪律）：所有写请求（PUT/POST/PATCH/DELETE）一律 page.route 拦截 +
//   假响应 —— 绝不落到后端，因此【不会改动任何真实群数据】。全程不用 force click。
//
// 用法：cd frontend && node scripts/verify_group_mode.mjs
import { chromium } from 'playwright-core';

const BASE = 'http://127.0.0.1:5173';
const browser = await chromium.launch({ channel: 'msedge', headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
await applyLanAuth(page);
const errors = [];
page.on('pageerror', (e) => errors.push(String(e).slice(0, 140)));

const results = [];
const check = (name, ok, detail = '') => {
  results.push({ name, ok: !!ok });
  console.log((ok ? 'OK  ' : 'BAD ') + name + (detail ? '  | ' + detail : ''));
};

// 写请求闸门：记下来 + 假响应，绝不落后端
const writes = [];
let nextStatus = 200;
let nextBody = '{"ok":true,"mode":"broadcast"}';
await page.route('**/api/v1/team/groups/**', async (route) => {
  const req = route.request();
  if (['PUT', 'POST', 'PATCH', 'DELETE'].includes(req.method())) {
    writes.push({ method: req.method(), url: req.url(), body: req.postData() ?? '' });
    await route.fulfill({ status: nextStatus, contentType: 'application/json', body: nextBody });
    return;
  }
  await route.continue();
});

await page.goto(BASE, { waitUntil: 'domcontentloaded' });
// 侧栏底部的「团队」入口
const teamBtn = page.locator('button[title="团队（AI 员工群聊）"]').first();
await teamBtn.waitFor({ state: 'visible', timeout: 15000 });
await teamBtn.click();
await page.waitForSelector('[aria-label="团队管理"]', { timeout: 8000 });
await page.waitForTimeout(600);

// ★ 团队页默认停在「员工卡」标签，群列表在「群聊」标签里（不切过去永远是空的）
const chatTab = page.getByText('群聊', { exact: true }).first();
if (await chatTab.count()) {
  await chatTab.click();
  await page.waitForTimeout(700);
}

// 选一个群：群名渲染在 <b class="team-group-name">，点击冒泡到父 button 的 onClick
const groupBtn = page.locator('.team-group-name').first();
const groupCount = await page.locator('.team-group-name').count();
if (groupCount === 0) {
  console.log('SKIP：这个环境里一个群都没有（先在界面建一个群再跑本脚本）');
  await browser.close();
  process.exit(0);
}
await groupBtn.waitFor({ state: 'visible', timeout: 8000 });
await groupBtn.click();
await page.waitForTimeout(700);

const sel = page.locator('select[aria-label="派发模式"]');
const hasSel = await sel.count();
check('① 群头部出现「派发模式」下拉（不再是只读徽章）', hasSel > 0, `找到 ${hasSel} 个`);

if (hasSel > 0) {
  const opts = await sel.locator('option').allTextContents();
  check('② 三个模式都在选项里', opts.length === 3 && opts.join('') === '点名派发全员广播组长拆解', opts.join(' / '));

  // 真实选择动作（非 force）
  await sel.selectOption('broadcast');
  await page.waitForTimeout(500);
  const put = writes.find((w) => w.method === 'PUT');
  check('③ 改模式发出 PUT /team/groups/{gid}', !!put, put ? put.url : '（没有发出）');
  check('④ 请求体带正确的 mode', !!put && /"mode"\s*:\s*"broadcast"/.test(put.body), put ? put.body : '');

  // 后端拒绝时（如"切组长模式但没组长"）要就地显示原因
  nextStatus = 422;
  nextBody = JSON.stringify({ detail: '切到组长模式前，先给这个群指定一名组长' });
  await sel.selectOption('leader');
  await page.waitForTimeout(600);
  const errShown = await page.locator('text=先给这个群指定一名组长').count();
  check('⑤ 后端 422 时就地显示原因（不再静默失败）', errShown > 0, `命中 ${errShown} 处`);
}

check('⑥ 无页面错误', errors.length === 0, errors.join(' | '));

const bad = results.filter((r) => !r.ok).length;
console.log(`\n写请求拦截计数 = ${writes.length}（全部被 page.route 拦下，0 次落盘）`);
console.log(`结果：${results.length - bad}/${results.length} 通过`);
await browser.close();
process.exit(bad === 0 ? 0 : 1);
