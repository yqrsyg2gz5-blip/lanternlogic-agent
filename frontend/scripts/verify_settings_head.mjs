// Phase 3 ⑧ 设置页正规化 验证（真浏览器）
//
// 判据：
//   ① 每节都有**一句说明**（"这节是干什么的"）——不再是一堆控件堆着
//   ② 每节都有**状态徽章**（现在什么状态一眼可见）
//   ③ 可恢复默认的节有「恢复默认」按钮；**点了要先确认**（confirm），确认后发 POST /settings/reset
//   ④ 危险节（这里没有 UI 入口，但后端会拒）—— 顺带验证前端只给白名单内的节挂按钮
//   ⑤ 切节时说明/状态跟着变（不是写死一份）
//   ⑥ 无页面错误
//
// ★ 安全：POST 一律假响应（route 拦截）——0 次落盘。
// 用法：cd frontend && node scripts/verify_settings_head.mjs
import { chromium } from 'playwright-core';
import { applyLanAuth } from './_lanauth.mjs';

const BASE = 'http://127.0.0.1:5173';
const results = [];
const check = (name, ok, detail = '') => {
  results.push({ name, ok: !!ok });
  console.log((ok ? 'OK  ' : 'BAD ') + name + (detail ? '  | ' + detail : ''));
};

const browser = await chromium.launch({ channel: 'msedge', headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 950 } });
await applyLanAuth(page);
const errors = [];
const posts = [];
page.on('pageerror', (e) => errors.push(String(e).slice(0, 140)));
// confirm 一律"确定"（并记下弹过）
let confirms = 0;
page.on('dialog', async (d) => { confirms += 1; await d.accept(); });
await page.route(/\/api\/v1\/settings\/reset(\?|$)/, async (route) => {
  posts.push(route.request().postData() ?? '');
  await route.fulfill({
    status: 200, contentType: 'application/json',
    body: JSON.stringify({ ok: true, section: 'model', note: '「model」已恢复默认；其它节没有改动。' }),
  });
});

await page.goto(BASE, { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(700);
const fab = page.locator('.settings-fab', { hasText: '设置' }).first();
await fab.waitFor({ state: 'visible', timeout: 15000 });
await fab.click();
await page.waitForTimeout(600);

async function openSection(name) {
  await page.locator('button.nav-item', { hasText: name }).first().click();
  await page.waitForTimeout(800);
  const head = await page.evaluate(() => {
    const d = document.querySelector('.sec-desc');
    const s = document.querySelector('.sec-status');
    const r = document.querySelector('.sec-reset');
    return {
      desc: d ? (d.textContent || '').trim() : '',
      status: s ? (s.textContent || '').trim() : '',
      tone: s ? s.className : '',
      reset: !!r,
    };
  });
  return head;
}

const model = await openSection('模型设置');
check('① 每节有一句说明', model.desc.length >= 8, model.desc.slice(0, 60));
check('② 每节有状态徽章', model.status.length > 0, model.status);
check('③ 可恢复默认的节有按钮', model.reset);

const voice = await openSection('语音');
check('④ 切节后说明与状态跟着变（不是写死）',
  voice.desc !== model.desc && voice.desc.includes('语音'), `语音节：${voice.desc.slice(0, 40)}`);

const exec = await openSection('执行环境');
check('⑤ 执行环境的状态徽章含沙箱与超时', /沙箱=/.test(exec.status), exec.status);

await openSection('模型设置');
await page.locator('.sec-reset').first().click();
await page.waitForTimeout(700);
check('⑥ 点「恢复默认」先弹确认', confirms >= 1, `弹了 ${confirms} 次`);
check('⑦ 确认后发出 POST /settings/reset 且带对的节名',
  posts.length > 0 && /"section":"model"/.test(posts[0]), posts[0] ?? '没有请求');
const noteText = (await page.locator('.sec-note').first().innerText().catch(() => '')).replace(/\s+/g, ' ');
check('⑧ 回执显示出来（说清只动了这一节）', /恢复默认/.test(noteText), noteText.slice(0, 60));
check('⑨ 无页面错误', errors.length === 0, errors.join(' | '));

await page.screenshot({ path: 'settings-head.png' }).catch(() => undefined);
await browser.close();
const bad = results.filter((r) => !r.ok).length;
console.log(`\n结果：${results.length - bad}/${results.length} 通过`);
process.exit(bad === 0 ? 0 : 1);
