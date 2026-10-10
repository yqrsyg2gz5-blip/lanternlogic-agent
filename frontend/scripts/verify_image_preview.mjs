// Phase 3 ⑦ 预览批 验证（真浏览器 · 真任务）：图片灯箱 + 正文图片解析
//
// 判据：
//   ① 给任务上传一张真 PNG（走 /upload 接口，不花 token），产物面板里能看到它
//   ② 产物面板里的图**可点**（`img[data-preview]` + cursor: zoom-in）
//   ③ 点一下 → 弹出灯箱（.preview-mask + .preview-img），且灯箱里的 src 就是那张图
//   ④ 灯箱有"在新标签打开"与关闭按钮；按 **Esc** 能关
//   ⑤ 点遮罩空白处也能关
//   ⑥ 对话里带图片附件的消息，缩略图同样可点（同一套全局委托）
//   ⑦ 无页面错误
//
// 用法：cd frontend && node scripts/verify_image_preview.mjs
import { chromium } from 'playwright-core';
import os from 'node:os';
import path from 'node:path';
import { mkdirSync, writeFileSync } from 'node:fs';
import { applyLanAuth } from './_lanauth.mjs';

const BASE = 'http://127.0.0.1:5173';
const API = 'http://127.0.0.1:8642';
const SHOTS = path.join(os.tmpdir(), 'agent-shell-shots', 'img-preview');
mkdirSync(SHOTS, { recursive: true });
const results = [];
const check = (name, ok, detail = '') => {
  results.push({ name, ok: !!ok });
  console.log((ok ? 'OK  ' : 'BAD ') + name + (detail ? '  | ' + detail : ''));
};

const TOKEN = process.env.DSH_LAN_TOKEN || '';
const headers = { 'X-Auth-Token': TOKEN };

// 造一张 2×2 的真 PNG（最小合法文件），上传到第一个任务的工作区里
const PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAYAAABytg0kAAAAFElEQVR42mP8z8Dwn4GBgYGJAQoAHgQCAZ7cVxsAAAAASUVORK5CYII=',
  'base64',
);
const pngPath = path.join(SHOTS, 'preview-probe.png');
writeFileSync(pngPath, PNG);

const tasks = await (await fetch(`${API}/api/v1/tasks`, { headers })).json();
const task = (tasks ?? []).find((t) => t.status !== 'running');
if (!task) { console.log('BAD 没有可用任务'); process.exit(1); }
console.log(`用任务：${task.id}`);

const form = new FormData();
form.append('file', new Blob([PNG], { type: 'image/png' }), 'preview-probe.png');
const up = await fetch(`${API}/api/v1/tasks/${task.id}/files`, { method: 'POST', headers, body: form });
const upBody = await up.text();
check('① 上传 PNG 到任务工作区', up.ok, `HTTP ${up.status} ${upBody.slice(0, 100)}`);

const browser = await chromium.launch({ channel: 'msedge', headless: true });
const page = await (await browser.newContext({ viewport: { width: 1440, height: 950 } })).newPage();
await applyLanAuth(page);
const errors = [];
page.on('pageerror', (e) => errors.push(String(e).slice(0, 160)));
page.on('console', (m) => { if (m.type() === 'error') errors.push('[console] ' + m.text().slice(0, 120)); });

await page.goto(BASE, { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(1000);
// 打开那个任务
const items = page.locator('.taskitem');
for (let i = 0; i < Math.min(await items.count(), 20); i += 1) {
  await items.nth(i).click();
  await page.waitForTimeout(500);
  const sub = await page.locator('.tv-sub').first().innerText().catch(() => '');
  if (sub.includes(task.id)) break;
}
await page.waitForTimeout(1200);

// 产物面板：先切到「产物」标签，再**点开那张图**（面板是"列表 + 预览"结构，
// 不点文件名的话图根本不渲染 —— 第一版就漏了这一步，白红一次）
const artTab = page.locator('button', { hasText: '产物' }).first();
if (await artTab.count()) {
  await artTab.click();
  await page.waitForTimeout(1800);        // 面板有轮询，等它刷一轮
}
const entry = page.locator('.art-item, .art-name').filter({ hasText: 'preview-probe' }).first();
if (await entry.count()) {
  await entry.click();
  await page.waitForTimeout(900);
}
const artImg = page.locator('img.art-img[data-preview]').first();
const hasArt = await artImg.count() > 0;
check('② 产物面板里的图可点（带 data-preview）', hasArt,
  hasArt ? '' : `面板里没渲染出图片（列表项 ${await page.locator('.art-item').count()} 个）`);

if (hasArt) {
  await artImg.click();
  await page.waitForTimeout(400);
  check('③ 点一下弹出灯箱', await page.locator('.preview-img').count() > 0);
  const shotSrc = await page.locator('.preview-img').first().getAttribute('src').catch(() => '');
  check('④ 灯箱里就是那张图', /files\/raw/.test(shotSrc || '') && /preview-probe\.png/.test(shotSrc || ''),
    (shotSrc || '').slice(0, 90));
  // ★ 决定性判据：图**真的加载出来了**（naturalWidth>0）—— 只断言元素存在会漏掉 401 空壳
  const loaded = await page.locator('.preview-img').first()
    .evaluate((el) => el.naturalWidth > 0).catch(() => false);
  check('④b 图片字节真的下来了（naturalWidth>0，不是 401 的空壳）', loaded);
  check('⑤ 灯箱有"在新标签打开"与关闭按钮',
    await page.locator('.preview-open').count() > 0 && await page.locator('.preview-x').count() > 0);
  await page.screenshot({ path: path.join(SHOTS, 'lightbox.png') });
  await page.keyboard.press('Escape');
  await page.waitForTimeout(300);
  check('⑥ Esc 能关掉灯箱', await page.locator('.preview-img').count() === 0);
  // 再开一次，点遮罩空白处关闭
  await artImg.click();
  await page.waitForTimeout(300);
  await page.locator('.preview-mask').click({ position: { x: 8, y: 8 } });
  await page.waitForTimeout(300);
  check('⑦ 点遮罩空白处也能关', await page.locator('.preview-img').count() === 0);
}

// ⑥ 对话里的图片附件（若该任务消息里有 attachments 图片）
const chatImg = page.locator('img.chat-img[data-preview]');
const chatCount = await chatImg.count();
if (chatCount > 0) {
  check('⑧ 对话里的图片附件同样可点（同一套全局委托）', true, `${chatCount} 张`);
} else {
  console.log('SKIP ⑧ 该任务消息里没有图片附件（无样例，不算失败）');
}

check('⑨ 无页面错误（401 会在 console 留 error，这条也等于「鉴权没漏」）', errors.length === 0,
  errors.slice(0, 2).join(' | '));

await browser.close();
const bad = results.filter((r) => !r.ok).length;
console.log(`\n结果：${results.length - bad}/${results.length} 通过`);
console.log(`截图：${SHOTS}`);
process.exit(bad === 0 ? 0 : 1);
