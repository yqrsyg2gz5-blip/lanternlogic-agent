// 语音输入端到端验证（真浏览器 + 假麦克风）：**两个输入框都测** + 快捷键
//
// 为什么两个都要测：这个功能此前在 Hero（首页新建任务输入框）和 TaskView（任务内输入框）
// **各写了一份**，用户报"根本不好使"时我只修了后者、测的也是后者 ⇒ 用户天天用的首页那个
// 照旧坏着（录 webm → 网关只收 wav/mp3 → 502）。这个脚本把两处都跑一遍，谁都跑不掉。
//
// 判据：
//   ① 首页输入框：点麦克风 → 录音态 → 停止 → **HTTP 200**（200 就说明后端收到了 wav/mp3 ——
//      本机没有 ffmpeg，非 wav 一律 502，所以这一条等价于"上传的是 wav"）
//   ② 首页：**按住 Ctrl+空格 → 松开** 也能录音并转写（用户点名要的快捷方式）
//   ③ 任务内输入框：同样的两步也要 200
//   ④ 录音态有可见反馈（按钮变 stop 图标）
//   ⑤ 无页面错误（502 会在 console 里留 error，所以这条也是"没再 502"的旁证）
//
// 用法：cd frontend && node scripts/verify_voice_input.mjs
import { chromium } from 'playwright-core';
import os from 'node:os';
import path from 'node:path';
import { mkdirSync } from 'node:fs';
import { applyLanAuth } from './_lanauth.mjs';

const BASE = 'http://127.0.0.1:5173';
const API = 'http://127.0.0.1:8642';
const SHOTS = path.join(os.tmpdir(), 'agent-shell-shots', 'voice-verify');
mkdirSync(SHOTS, { recursive: true });
const results = [];
const check = (name, ok, detail = '') => {
  results.push({ name, ok: !!ok });
  console.log((ok ? 'OK  ' : 'BAD ') + name + (detail ? '  | ' + detail : ''));
};

const browser = await chromium.launch({
  channel: 'msedge', headless: true,
  args: ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'],
});
const ctx = await browser.newContext({ viewport: { width: 1440, height: 950 }, permissions: ['microphone'] });
const page = await ctx.newPage();
await applyLanAuth(page);
const errors = [];
const calls = [];
page.on('pageerror', (e) => errors.push(String(e).slice(0, 160)));
page.on('console', (m) => { if (m.type() === 'error') errors.push('[console] ' + m.text().slice(0, 120)); });
page.on('response', async (r) => {
  if (r.url().includes('/voice/transcribe')) {
    let body = '';
    try { body = (await r.text()).slice(0, 300); } catch { /* ignore */ }
    calls.push({ status: r.status(), body });
  }
});

await page.goto(BASE, { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(1200);

// ── ① 首页输入框：点一下录、再点一下停 ──
// ★ 必须按标题精确选：会议记录那个按钮**共用** voice-btn 这个 class（点错了就永远等不到转写请求）
const VOICE = '.voice-btn[aria-label="语音输入"]';   // 用 aria-label：标题会随录音状态变化，选择器会失配
const heroMic = page.locator(VOICE).first();
check('① 首页有语音输入按钮', await heroMic.count() > 0);
await heroMic.click();
await page.waitForTimeout(400);
check('② 点击后进入录音态（图标变停止）', await page.locator(`${VOICE}.voice-on`).count() > 0);
await page.waitForTimeout(2200);
await heroMic.click();
await page.waitForTimeout(2600);
check('③ 首页录音转写成功（HTTP 200 = 上传的是 wav/mp3）',
  calls.length > 0 && calls[0].status === 200,
  calls.length ? `HTTP ${calls[0].status}${calls[0].status !== 200 ? ' → ' + calls[0].body : ''}` : '没有请求');

// ── ② 首页：按住**右 Ctrl** → 松开（快捷键）──
//    ★ 必须按 ControlRight：Ctrl+空格 会被中文输入法抢走，拿它测会"假绿"（真机上收不到）
calls.length = 0;
await page.keyboard.down('ControlRight');
await page.waitForTimeout(2200);
const hotkeyRecording = await page.locator(`${VOICE}.voice-on`).count() > 0;
await page.keyboard.up('ControlRight');
await page.waitForTimeout(2600);
check('④ 按住右 Ctrl 会进入录音态', hotkeyRecording);
check('⑤ 松开右 Ctrl 后自动转写（HTTP 200）',
  calls.length > 0 && calls[0].status === 200,
  calls.length ? `HTTP ${calls[0].status}` : '松开后没有发请求');

// ── ③ 任务内输入框：同样两步 ──
calls.length = 0;
// 点侧栏里已有的任务（比"新建一个再等它跑完"确定得多 —— 跑动中整个流区会重挂载）
const items = page.locator('.taskitem');
const tries = Math.min(await items.count(), 6);
let opened = false;
for (let i = 0; i < tries; i += 1) {
  await items.nth(i).click();
  await page.waitForTimeout(900);
  if (await page.locator(VOICE).count() > 0) { opened = true; break; }
}
const inTaskMic = page.locator(VOICE).first();
check('⑥ 任务内有语音输入按钮', opened, `试了 ${tries} 个任务`);
await inTaskMic.click();
await page.waitForTimeout(400);
const inTaskRec = await page.locator(`${VOICE}.voice-on`).count() > 0;
await page.waitForTimeout(2200);
await inTaskMic.click();
await page.waitForTimeout(2600);
check('⑦ 任务内也能进入录音态', inTaskRec);
check('⑧ 任务内录音转写成功（HTTP 200）',
  calls.length > 0 && calls[0].status === 200,
  calls.length ? `HTTP ${calls[0].status}${calls[0].status !== 200 ? ' → ' + calls[0].body : ''}` : '没有请求');

check('⑨ 无页面错误（含 502 的 console error）', errors.length === 0, errors.slice(0, 2).join(' | '));

await page.screenshot({ path: path.join(SHOTS, 'voice.png') });
await browser.close();
const bad = results.filter((r) => !r.ok).length;
console.log(`\n结果：${results.length - bad}/${results.length} 通过`);
console.log(`截图：${SHOTS}`);
process.exit(bad === 0 ? 0 : 1);
