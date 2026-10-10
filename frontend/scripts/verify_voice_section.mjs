// Phase 2 ④⑤ 验证（真浏览器）：设置页新增的「语音」栏
//
// 判据：
//   ① 侧栏有「语音」入口，点进去能看到两档 ASR（云端 MiMo / 本地千问3）
//   ② 每档带**状态徽章**（可用 / 缺 Key / 未安装）—— 不是一律写"可用"
//   ③ 当前档位有「当前使用」标记；另一档有「用这个」按钮
//   ④ 点「用这个」→ POST /api/v1/settings/asr，且回执文案显示出来
//   ⑤ 本地档位推荐显示出来（含探测依据：显存/内存）
//   ⑥ TTS 那一栏如实标注"切换还没做"（不装作能切）
//
// ★ 安全：GET /api/v1/capabilities 走 route.fetch 取真值再改字段（真机数据）；
//   POST /settings/asr 一律假响应 —— 0 次落盘、不动 config.json。
// 用法：cd frontend && node scripts/verify_voice_section.mjs
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

await page.route(/\/api\/v1\/settings\/asr(\?|$)/, async (route) => {
  posts.push(route.request().postData() ?? '');
  await route.fulfill({
    status: 200, contentType: 'application/json',
    body: JSON.stringify({ ok: true, provider: 'local_qwen3', state: 'not_installed',
      detail: '还没安装（pip install faster-whisper 后按档位下模型）',
      note: '已记下这个档位，但它现在还用不了：还没安装' }),
  });
});
await page.route(/\/api\/v1\/capabilities(\?|$)/, async (route) => {
  const real = await (await route.fetch()).json();     // 真机数据（含真实显存探测）
  await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(real) });
});

await page.goto(BASE, { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(600);
const fab = page.locator('.settings-fab', { hasText: '设置' }).first();
await fab.waitFor({ state: 'visible', timeout: 15000 });
await fab.click();
await page.waitForTimeout(500);

const navVoice = page.locator('button.nav-item', { hasText: '语音' }).first();
check('① 侧栏有「语音」入口', await navVoice.count() > 0);
await navVoice.click();
await page.waitForTimeout(900);

// ★ 只读设置层本身（role=dialog）—— 第一版选了 main，抓到的是背景工作区页面
const body = await page.locator('[role="dialog"]').first().innerText().catch(() => '');
const text = body.replace(/\s+/g, ' ');
check('② 两档 ASR 都在（云端 MiMo / 本地千问3）',
  /云端 MiMo/.test(text) && /千问3-ASR|本地 千问3/.test(text), text.slice(0, 90));
check('③ 状态徽章如实（不是一律"可用"）',
  /缺 Key|可用/.test(text) && /未安装/.test(text), (text.match(/缺 Key|未安装|可用/g) ?? []).join(','));
check('④ 当前档位有「当前使用」标记', /当前使用/.test(text));
check('⑤ 另一档有「用这个」按钮', await page.locator('button.btn-mini', { hasText: '用这个' }).count() > 0);
check('⑥ 本地档位推荐显示出来（含探测依据）',
  /本地档位推荐/.test(text) && /显存|探测/.test(text), (text.match(/本地档位推荐[^）]*）?/) ?? [''])[0].slice(0, 100));
check('⑦ TTS 栏如实标注"切换还没做"', /还没做/.test(text) && /(MeloTTS|语音合成)/.test(text));

await page.locator('button.btn-mini', { hasText: '用这个' }).first().click();
await page.waitForTimeout(700);
check('⑧ 点「用这个」发出 POST /settings/asr', posts.length > 0 && /local_qwen3/.test(posts[0]),
  posts[0] ?? '没有请求');
check('⑨ 回执文案显示出来（能不能用说清楚）',
  /用不了|没安装|已记下/.test((await page.locator('.voice-note').first().innerText().catch(() => '')).replace(/\s+/g, ' ')));

check('⑩ 无页面错误', errors.length === 0, errors.join(' | '));
await page.screenshot({ path: 'voice_section.png' }).catch(() => undefined);
await browser.close();
const bad = results.filter((r) => !r.ok).length;
console.log(`\n结果：${results.length - bad}/${results.length} 通过`);
process.exit(bad === 0 ? 0 : 1);
