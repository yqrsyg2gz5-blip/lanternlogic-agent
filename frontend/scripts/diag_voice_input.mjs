// 语音输入端到端诊断（真浏览器 + **假麦克风**）：说话 → 转写 → 填进输入框
//
// 为什么要有这个脚本：用户报"语音输入根本不好使"。headless Chromium 可以用
//   --use-fake-device-for-media-stream   造一个源源不断的假麦克风（有声音，不是静音）
//   --use-fake-ui-for-media-stream       自动允许权限（不用手点授权弹窗）
// 于是"录音 → MediaRecorder → /voice/transcribe → 填框"这条路能**真的跑通**，
// 失败点也能看到底卡在哪一步（点击没进入录音 / 请求没发 / 后端报错 / 文字没填进去）。
//
// 用法：cd frontend && node scripts/diag_voice_input.mjs
import { chromium } from 'playwright-core';
import os from 'node:os';
import path from 'node:path';
import { mkdirSync } from 'node:fs';
import { applyLanAuth } from './_lanauth.mjs';

const BASE = 'http://127.0.0.1:5173';
const SHOTS = path.join(os.tmpdir(), 'agent-shell-shots', 'voice-diag');
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
const reqs = [];
const uploaded = [];
page.on('pageerror', (e) => errors.push(String(e).slice(0, 200)));
page.on('console', (m) => {
  const txt = m.text().slice(0, 300);
  if (m.type() === 'error') errors.push('[console] ' + txt);
  else if (/recwav|WAV|\[voice\]/i.test(txt)) console.log('    页面日志 ' + m.type() + '：' + txt);
});
// ★ 关键：看清**发出去的到底是什么**（RIFF=WAV / 0x1A45DFA3=webm），别只看状态码
page.on('request', (r) => {
  if (r.url().includes('/voice/transcribe')) {
    const buf = r.postDataBuffer();
    if (buf) {
      const magic = buf.subarray(0, 4).toString('hex');
      const head = buf.subarray(0, 4).toString('latin1');
      uploaded.push({ size: buf.length, magic, head, isWav: head === 'RIFF', isWebm: magic.startsWith('1a45dfa3') });
    }
  }
});
page.on('response', async (r) => {
  if (r.url().includes('/voice/transcribe')) {
    let body = '';
    try { body = await r.text(); } catch { /* ignore */ }
    reqs.push({ status: r.status(), body });
  }
});

await page.goto(BASE, { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(900);

// 新建一个任务，确保输入框是干净的
const newBtn = page.locator('button', { hasText: '新建任务' }).first();
if (await newBtn.count()) { await newBtn.click(); await page.waitForTimeout(700); }
const box = page.locator('textarea.draft, textarea').first();
await box.waitFor({ state: 'visible', timeout: 10000 });
check('① 输入框在', await box.count() > 0);

// getUserMedia 能不能拿到流（假设备应当可以）
const gum = await page.evaluate(async () => {
  try {
    const s = await navigator.mediaDevices.getUserMedia({ audio: true });
    const n = s.getAudioTracks().length;
    s.getTracks().forEach((t) => t.stop());
    return `ok:${n}`;
  } catch (e) { return 'err:' + String(e).slice(0, 90); }
});
check('② 浏览器能拿到麦克风流（假设备）', gum.startsWith('ok:'), gum);

const mic = page.locator('.voice-btn').first();
check('③ 有语音输入按钮', await mic.count() > 0);

await mic.click();
await page.waitForTimeout(300);
const on = await page.locator('.voice-btn.voice-on').count();
check('④ 点一下进入录音状态（按钮变红/方块）', on > 0, on ? 'voice-on ✓' : '没进入录音态');

await page.waitForTimeout(2600);          // 让假麦克风录一段（>2KB 才有内容）
const hintDuring = (await page.locator('.art-note').first().innerText().catch(() => '')).trim();
await mic.click();                        // 再点一下 = 停止并转写
await page.waitForTimeout(2500);
const hintAfter = (await page.locator('.art-note').first().innerText().catch(() => '')).trim();
const typed = await box.inputValue().catch(() => '');

check('⑤ 停止后发出了转写请求', reqs.length > 0,
  reqs.length ? `HTTP ${reqs[0].status}` : '一个请求都没有');
if (uploaded.length) {
  const u = uploaded[0];
  console.log(`    上传内容：${u.size} 字节，magic=${u.magic}（${u.isWav ? 'WAV ✓' : u.isWebm ? 'webm ✗（退回了老路）' : '未知'}）`);
}
if (reqs.length && reqs[0].status !== 200) {
  console.log(`    后端完整报错：${reqs[0].body}`);
}
check('⑥ 后端转写成功（200 且 ok）', reqs.length > 0 && reqs[0].status === 200,
  reqs.length ? `HTTP ${reqs[0].status}` : '没有请求');
check('⑥b 上传的是 WAV（不再依赖 ffmpeg 转码）', uploaded.length > 0 && uploaded[0].isWav,
  uploaded.length ? `${uploaded[0].size} 字节 magic=${uploaded[0].magic}` : '没抓到上传内容');
check('⑦ 转写文字**填进了输入框**', typed.trim().length > 0,
  typed ? `"${typed.slice(0, 60)}"` : `输入框还是空的；提示="${hintAfter || hintDuring}"`);
check('⑧ 页面无错误', errors.length === 0, errors.slice(0, 3).join(' | '));

await page.screenshot({ path: path.join(SHOTS, 'voice-after.png') });
await browser.close();
const bad = results.filter((r) => !r.ok).length;
console.log(`\n结果：${results.length - bad}/${results.length} 通过`);
console.log(`截图：${SHOTS}`);
process.exit(bad === 0 ? 0 : 1);
