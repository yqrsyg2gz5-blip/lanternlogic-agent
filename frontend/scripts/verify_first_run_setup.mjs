// Phase 1 ① 验证（真浏览器）：没配 Key 时，欢迎页第一眼要看得见提示，且「去设置」真能打开设置面板。
//
// 判据（两条方向相反的用例）：
//   A. key_set=false（需要 Key 但没配）→ 提示条出现、文案含三要素、「去设置」点开后
//      真的出现 role=dialog 的设置面板（不是摆样子）
//   B. key_set=true / null（已配 / 不需要 Key）→ 提示条**不许出现**（防假警报：
//      本地 Ollama 与 Mock 用户不该被叫去配 Key）
//
// ★ 安全：GET /api/v1/settings 走 route.fetch() 取真值再改字段；任何写请求本地假响应
//   —— 全程 0 次落盘，不碰 config.json。不用 force click。
// ★ URL 用【正则】：本应用把密码拼在 query 上（?token=…），Playwright 的 glob 是全串匹配。
//
// 用法：cd frontend && node scripts/verify_first_run_setup.mjs
import { chromium } from 'playwright-core';
import os from 'node:os';
import path from 'node:path';
import { mkdirSync } from 'node:fs';
import { applyLanAuth } from './_lanauth.mjs';

const BASE = 'http://127.0.0.1:5173';
// 截图落 $TEMP（与其它 verify_*.mjs 同口径）——别把二进制证据丢进仓库工作区
const SHOT_DIR = path.join(os.tmpdir(), 'agent-shell-shots', 'first-run');
mkdirSync(SHOT_DIR, { recursive: true });
const results = [];
const check = (name, ok, detail = '') => {
  results.push({ name, ok: !!ok });
  console.log((ok ? 'OK  ' : 'BAD ') + name + (detail ? '  | ' + detail : ''));
};

async function openHero(browser, keySet, provider = 'mimo') {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  await applyLanAuth(page);
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e).slice(0, 140)));
  let real = null;
  await page.route(/\/api\/v1\/settings(\?|$)/, async (route) => {
    if (route.request().method() !== 'GET') {
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{"ok":true}' });
      return;
    }
    if (!real) real = await (await route.fetch()).json();
    const body = JSON.parse(JSON.stringify(real));
    body.model = { ...(body.model ?? {}), key_set: keySet, provider, api_key_env: 'XIAOMI_MIMO_API_KEY' };
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  });
  await page.goto(BASE, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('main textarea', { timeout: 15000 });   // Hero 的输入框
  await page.waitForTimeout(600);                                    // 等 getSettings 回来
  const strip = await page.evaluate(() => {
    const el = document.querySelector('.taskview [role="status"]');
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return { text: (el.textContent || '').replace(/\s+/g, ' ').trim(), w: Math.round(r.width), h: Math.round(r.height) };
  });
  return { page, strip, errors };
}

const browser = await chromium.launch({ channel: 'msedge', headless: true });

// ── A. key_set=false（首次上手场景）──
const a = await openHero(browser, false);
check('① key_set=false 时欢迎页出现提示条', !!a.strip, JSON.stringify(a.strip)?.slice(0, 90));
check('② 提示条真的渲染出来（宽高 > 0）', !!a.strip && a.strip.w > 200 && a.strip.h > 40,
  a.strip ? `w=${a.strip.w} h=${a.strip.h}` : '缺失');
check('③ 文案含三要素（去哪配 / 存哪 / 要重启）',
  !!a.strip && a.strip.text.includes('设置 → 模型设置') && a.strip.text.includes('环境变量')
  && a.strip.text.includes('不写进配置文件') && a.strip.text.includes('重启'),
  a.strip?.text?.slice(0, 120) ?? '');
check('④ 文案含当前提供者（用户知道在配哪个）',
  !!a.strip && a.strip.text.includes('mimo'), a.strip?.text?.slice(-40) ?? '');
check('⑤ 提示条不许挡住输入框（输入框仍可见可点）',
  await a.page.locator('main textarea').isVisible());
check('⑥ 无页面错误', a.errors.length === 0, a.errors.join(' | '));
await a.page.screenshot({ path: path.join(SHOT_DIR, 'hero_setup_strip.png') });   // 点之前：提示条本体

// ── A2.「去设置」真能打开设置面板 ──
const btn = a.page.locator('.taskview button', { hasText: '去设置' }).first();
await btn.click();
await a.page.waitForTimeout(700);
const dialog = await a.page.evaluate(() => {
  const d = document.querySelector('[role="dialog"]');
  return d ? (d.getAttribute('aria-label') || 'dialog') : null;
});
check('⑦ 点「去设置」真的打开了设置面板（role=dialog）', !!dialog, String(dialog));
check('⑧ 面板里能看到「模型设置」分区', await a.page.locator('button.nav-item', { hasText: '模型设置' }).count() > 0);

// ── B. key_set=true / null（已配 / 本地模型）→ 不许出现提示条 ──
const b1 = await openHero(browser, true);
check('⑨ key_set=true 时提示条不出现（已配好就别打扰）', b1.strip == null, JSON.stringify(b1.strip));
const b2 = await openHero(browser, null, 'ollama');
check('⑩ key_set=null（本地 Ollama / Mock）时提示条不出现（防假警报）', b2.strip == null, JSON.stringify(b2.strip));
check('⑪ B 组无页面错误', b1.errors.length === 0 && b2.errors.length === 0,
  [...b1.errors, ...b2.errors].join(' | '));

await a.page.screenshot({ path: path.join(SHOT_DIR, 'hero_setup_opened_settings.png') });
await browser.close();
const bad = results.filter((r) => !r.ok).length;
console.log(`\n结果：${results.length - bad}/${results.length} 通过`);
console.log(`截图：${SHOT_DIR}`);
process.exit(bad === 0 ? 0 : 1);
