// Phase 2 ⑥ 验证（真浏览器）：模型名下拉 + 一键刷新 + 来源说真话
//
// 判据：
//   ① 进「模型设置」就自动拉一次列表（用户不必先按刷新）
//   ② 拉到时：下拉框里有服务商返回的模型；提示语是绿的、说"已拉到 N 个"
//   ③ 点「刷新列表」→ 再发一次请求，且带 refresh=1（绕过缓存）
//   ④ 拉不到时：提示语变黄、说明原因，且**仍能选内置预设名**（不是空框）
//   ⑤ 选中某个模型 → 上面的"模型名"输入框真的变了（下拉不是摆设）
//
// ★ 安全：所有请求 page.route 拦截 + 假响应，0 次落到后端；不用 force click。
// ★ URL 用正则（本应用把密码拼在 query 上，glob 是全串匹配）。
// 用法：cd frontend && node scripts/verify_model_list.mjs
import { chromium } from 'playwright-core';
import { applyLanAuth } from './_lanauth.mjs';

const BASE = 'http://127.0.0.1:5173';
const results = [];
const check = (name, ok, detail = '') => {
  results.push({ name, ok: !!ok });
  console.log((ok ? 'OK  ' : 'BAD ') + name + (detail ? '  | ' + detail : ''));
};

const browser = await chromium.launch({ channel: 'msedge', headless: true });

async function openModelSection({ live = true } = {}) {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  await applyLanAuth(page);
  const reqs = [];
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e).slice(0, 140)));
  await page.route(/\/api\/v1\/models(\?|$)/, async (route) => {
    reqs.push(route.request().url());
    const body = live
      ? { provider: 'mimo', models: ['mimo-v2.6-flash', 'mimo-v2.5', 'mimo-v2.5-pro'],
          source: 'live', note: '已从服务商拉到 3 个模型', cached: false }
      : { provider: 'mimo', models: [], source: 'preset',
          note: '拉不到在线列表（ConnectError: 网络不通）—— 用内置预设即可', cached: false };
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  });
  await page.goto(BASE, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(600);
  const fab = page.locator('.settings-fab', { hasText: '设置' }).first();
  await fab.waitFor({ state: 'visible', timeout: 15000 });
  await fab.click();
  await page.waitForTimeout(500);
  await page.locator('button.nav-item', { hasText: '模型设置' }).first().click();
  await page.waitForTimeout(900);            // 等自动拉取回来
  return { page, reqs, errors };
}

// ── A. 拉得到 ──
const a = await openModelSection({ live: true });
check('① 进「模型设置」自动拉了一次（没按刷新也有请求）', a.reqs.length >= 1, `${a.reqs.length} 次`);
const opts = await a.page.locator('select.model-select option').allInnerTexts();
check('② 下拉框里有服务商返回的模型', opts.includes('mimo-v2.5-pro') && opts.includes('mimo-v2.5'),
  opts.join(' | ').slice(0, 80));
const noteText = (await a.page.locator('.model-source').first().innerText().catch(() => '')).replace(/\s+/g, ' ');
check('③ 提示语说真话（已从服务商拉到）', /已从服务商拉到/.test(noteText), noteText.slice(0, 60));
const cls = await a.page.locator('.model-source').first().getAttribute('class');
check('④ 拉到时提示是"好"色（ok）', /ok/.test(cls ?? ''), cls ?? '');

// 选中 → 输入框跟着变
await a.page.locator('select.model-select').selectOption('mimo-v2.5');
await a.page.waitForTimeout(200);
const modelField = await a.page.evaluate(() => {
  const labels = Array.from(document.querySelectorAll('label.fld'));
  const l = labels.find((x) => (x.textContent || '').startsWith('模型名'));
  return l ? l.querySelector('input').value : '';
});
check('⑤ 选中后"模型名"输入框真的变了', modelField === 'mimo-v2.5', `现在是 ${modelField || '(空)'}`);

// 刷新按钮 → 再发一次且带 refresh=1
const before = a.reqs.length;
await a.page.locator('button.btn-mini', { hasText: '刷新列表' }).first().click();
await a.page.waitForTimeout(700);
check('⑥ 点刷新会再拉一次', a.reqs.length > before, `${before} → ${a.reqs.length}`);
check('⑦ 刷新请求带 refresh=1（绕过缓存）', /refresh=1/.test(a.reqs[a.reqs.length - 1]), a.reqs[a.reqs.length - 1]);
check('⑧ A 组无页面错误', a.errors.length === 0, a.errors.join(' | '));

// ── B. 拉不到 ──
const b = await openModelSection({ live: false });
const noteB = (await b.page.locator('.model-source').first().innerText().catch(() => '')).replace(/\s+/g, ' ');
check('⑨ 拉不到时说明原因（不是静默空框）', /拉不到|ConnectError/.test(noteB), noteB.slice(0, 70));
const clsB = await b.page.locator('.model-source').first().getAttribute('class');
check('⑩ 拉不到时提示是"注意"色（warn）', /warn/.test(clsB ?? ''), clsB ?? '');
const optsB = await b.page.locator('select.model-select option').allInnerTexts();
check('⑪ 拉不到时仍能选内置预设名（不空）', optsB.length >= 2, optsB.join(' | ').slice(0, 80));
check('⑫ B 组无页面错误', b.errors.length === 0, b.errors.join(' | '));

await browser.close();
const bad = results.filter((r) => !r.ok).length;
console.log(`\n结果：${results.length - bad}/${results.length} 通过`);
process.exit(bad === 0 ? 0 : 1);
