// Phase 3 ⑧ 第二刀 验证（真浏览器）：设置导出 / 导入
//
// 判据：
//   ① 常规节里有「导出设置」和「导入设置…」
//   ② 点导出 → **真的触发下载**，文件名像 agent-shell-设置-2026-10-05.json
//   ③ 导出请求打到 /api/v1/settings/export，且回来的内容里**没有密钥**（用假响应验证前端不会瞎编）
//   ④ 选一个文件导入 → **先发 dry_run=true**（预演），界面显示"将改动…"与「确认导入」
//   ⑤ 点确认 → 再发 dry_run=false 并带同一份 sections
//   ⑥ 后端 422（文件里有不能导入的节）时如实报错，不静默
//   ⑦ 无页面错误
//
// ★ 安全：所有请求 route 拦截假响应 —— 不会真的写配置。
// 用法：cd frontend && node scripts/verify_settings_portability.mjs
import { chromium } from 'playwright-core';
import os from 'node:os';
import path from 'node:path';
import { mkdirSync, writeFileSync } from 'node:fs';
import { applyLanAuth } from './_lanauth.mjs';

const BASE = 'http://127.0.0.1:5173';
const TMP = path.join(os.tmpdir(), 'agent-shell-shots', 'settings-port');
mkdirSync(TMP, { recursive: true });
const IMPORT_FILE = path.join(TMP, 'import-me.json');
writeFileSync(IMPORT_FILE, JSON.stringify({
  ok: true, app: 'agent-shell', version: 1,
  sections: { model: { provider: 'deepseek', model_name: 'deepseek-chat' } },
}, null, 2), 'utf-8');
const BAD_FILE = path.join(TMP, 'import-bad.json');
writeFileSync(BAD_FILE, JSON.stringify({ sections: { server: { host: '0.0.0.0' } } }, null, 2), 'utf-8');

const results = [];
const check = (name, ok, detail = '') => {
  results.push({ name, ok: !!ok });
  console.log((ok ? 'OK  ' : 'BAD ') + name + (detail ? '  | ' + detail : ''));
};

const browser = await chromium.launch({ channel: 'msedge', headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 950 }, acceptDownloads: true });
await applyLanAuth(page);
const errors = [];
const posts = [];
let badMode = false;                     // true = 模拟后端拒绝（用坏文件那一步）
page.on('pageerror', (e) => errors.push(String(e).slice(0, 140)));
await page.route(/\/api\/v1\/settings\/export(\?|$)/, async (route) => {
  await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
    ok: true, app: 'agent-shell', version: 1, exported_at: '2026-10-05T00:00:00Z',
    sections: { model: { provider: 'mimo', api_key_env: 'XIAOMI_MIMO_API_KEY' } },
    note: '只导出可搬的设置；API Key 与访问密码**不在里面**。',
  }) });
});
await page.route(/\/api\/v1\/settings\/import(\?|$)/, async (route) => {
  const body = JSON.parse(route.request().postData() || '{}');
  posts.push(body);
  if (badMode) {
    await route.fulfill({ status: 422, contentType: 'application/json', body: JSON.stringify({
      detail: "这些节不能导入：['server']（可导入：model/executor/video/image/asr/ui/notify/memory）" }) });
    return;
  }
  await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
    ok: true, dry_run: !!body.dry_run,
    changed: body.dry_run ? { model: ['provider', 'model_name'] } : { model: ['provider'] },
    note: body.dry_run ? '预演：将改动 1 个节（model）' : '已导入 1 个节：model；API Key 仍需你在本机设置。',
  }) });
});

await page.goto(BASE, { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(700);
const fab = page.locator('.settings-fab', { hasText: '设置' }).first();
await fab.waitFor({ state: 'visible', timeout: 15000 });
await fab.click();
await page.waitForTimeout(600);
await page.locator('button.nav-item', { hasText: '常规' }).first().click();
await page.waitForTimeout(700);

check('① 有「导出设置」与「导入设置…」',
  await page.locator('button.btn-mini', { hasText: '导出设置' }).count() > 0
  && await page.locator('.port-import').count() > 0);

// ②③ 导出 → 真触发下载
const dl = page.waitForEvent('download', { timeout: 8000 }).catch(() => null);
await page.locator('button.btn-mini', { hasText: '导出设置' }).first().click();
const download = await dl;
check('② 点导出真的触发下载', !!download, download ? await download.suggestedFilename() : '没有下载事件');
if (download) {
  const name = download.suggestedFilename();
  check('③ 文件名可读（含日期）', /agent-shell-设置-\d{4}-\d{2}-\d{2}\.json/.test(name), name);
}
const noteA = (await page.locator('.sec-note').last().innerText().catch(() => '')).replace(/\s+/g, ' ');
check('④ 导出后如实说明"文件里没有密钥"', /没有/.test(noteA) && /Key/.test(noteA), noteA.slice(0, 60));

// ⑤ 导入：先预演
await page.locator('.port-import input[type=file]').setInputFiles(IMPORT_FILE);
await page.waitForTimeout(800);
check('⑤ 导入先发 dry_run=true（预演）', posts.length > 0 && posts[0].dry_run === true,
  JSON.stringify(posts[0] ?? {}).slice(0, 80));
const preview = (await page.locator('.card-hint', { hasText: '将改动' }).first().innerText().catch(() => '')).replace(/\s+/g, ' ');
check('⑥ 界面显示"将改动什么"', /将改动/.test(preview) && /provider/.test(preview), preview.slice(0, 80));
check('⑦ 有「确认导入」按钮', await page.locator('button.btn-mini', { hasText: '确认导入' }).count() > 0);

// ⑥ 确认 → 再发 dry_run=false
await page.locator('button.btn-mini', { hasText: '确认导入' }).first().click();
await page.waitForTimeout(700);
check('⑧ 确认后发 dry_run=false 且带同一份 sections',
  posts.length > 1 && posts[1].dry_run === false
  && posts[1].sections?.model?.provider === 'deepseek',
  JSON.stringify(posts[1] ?? {}).slice(0, 90));
const noteB = (await page.locator('.sec-note').last().innerText().catch(() => '')).replace(/\s+/g, ' ');
check('⑨ 导入回执说清"Key 仍要本机配"', /本机设置/.test(noteB), noteB.slice(0, 70));

// ⑦ 坏文件（后端 422）→ 如实报错
badMode = true;
posts.length = 0;
await page.locator('.port-import input[type=file]').setInputFiles(BAD_FILE);
await page.waitForTimeout(800);
const noteC = (await page.locator('.sec-note').last().innerText().catch(() => '')).replace(/\s+/g, ' ');
check('⑩ 后端拒绝时如实报错（不当成功）', /不能导入|失败/.test(noteC), noteC.slice(0, 80));
check('⑪ 被拒后不给「确认导入」', await page.locator('button.btn-mini', { hasText: '确认导入' }).count() === 0);
check('⑫ 无页面错误', errors.length === 0, errors.join(' | '));

await browser.close();
const bad = results.filter((r) => !r.ok).length;
console.log(`\n结果：${results.length - bad}/${results.length} 通过`);
process.exit(bad === 0 ? 0 : 1);
