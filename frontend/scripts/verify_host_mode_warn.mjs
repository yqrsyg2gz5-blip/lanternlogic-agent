// A3 验证（真浏览器）：宿主模式（sandbox="off"）的**风险告知**是否真的看得见。
//
// 背景（修复前，本批开工实测）：
//   · 后端 `_shell` 宿主分支把命令输出原样返回 —— repr 就是 'A3-PROBE'，
//     一句话都没有；同一能力面的沙箱分支却会追加 sandbox_note。
//   · 界面上也没有任何标识：任务头只写 "自动编辑"，看不出命令落在真机上。
//
// 本脚本判两件事（而不是"代码里有没有那句话"）：
//   A. sandbox=off  → ① 任务头常驻徽标 ② 审批栏告知 ③ 设置页告警块，三处**真的可见**
//      （宽高>0、没被父容器裁掉、opacity 未被淡化）——"被裁掉的告知等于没有告知"
//   B. sandbox=docker → 三处**都不出现**（沙箱开着还说"在你电脑上跑"就是错误告知）
//
// ★ 安全（施工纪律）：GET /api/v1/settings 走 route.fetch() 取真值再改 sandbox 字段；
//   任何**写**请求一律本地假响应 —— 全程 0 次落盘，不碰用户的 config.json。不用 force click。
//
// 用法：cd frontend && node scripts/verify_host_mode_warn.mjs
import { chromium } from 'playwright-core';
import { writeFileSync, mkdirSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { applyLanAuth } from './_lanauth.mjs';

const BASE = 'http://127.0.0.1:5173';
const TASK_ID = 'task_20261004_a3aa';
// 截图落 $TEMP（与其它 verify_*.mjs 同口径）——不要把二进制证据丢进仓库工作区
const SHOT_DIR = path.join(os.tmpdir(), 'agent-shell-shots', 'a3');
mkdirSync(SHOT_DIR, { recursive: true });

const results = [];
const check = (name, ok, detail = '') => {
  results.push({ name, ok: !!ok });
  console.log((ok ? 'OK  ' : 'BAD ') + name + (detail ? '  | ' + detail : ''));
};

// 元素"真的看得见"：存在 + 有盒子 + 没被裁 + 不是隐身（opacity/visibility/display）
// ★ 必须传【真函数】给 page.evaluate：传字符串会被当成"求值出一个函数对象"
//   （函数不可序列化 ⇒ 永远拿到 undefined ⇒ 全部判据假红。本脚本第一版就踩了这个坑）。
const probeVisible = (sel) => {
  const el = document.querySelector(sel);
  if (!el) return null;
  const r = el.getBoundingClientRect();
  const cs = getComputedStyle(el);
  return {
    text: (el.textContent || '').trim().slice(0, 80),
    w: Math.round(r.width), h: Math.round(r.height),
    clippedX: el.scrollWidth - el.clientWidth, clippedY: el.scrollHeight - el.clientHeight,
    opacity: cs.opacity, visibility: cs.visibility, display: cs.display,
    color: cs.color,
  };
};

async function openCase(browser, sandbox) {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  await applyLanAuth(page);
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e).slice(0, 140)));
  let settingsBody = null;
  const writes = [];

  // ★ 用【正则】而不是 glob：本应用把访问密码拼在 query 上
  //（`/api/v1/settings?token=…`），而 Playwright 的 glob 是**全串匹配**——
  //  `**/api/v1/settings` 永远匹配不到带 query 的真实请求（本脚本第一版就踩了：
  //   mock 静默失效，docker 档读到的还是真配置 ⇒ 判据假红）。
  await page.route(/\/api\/v1\/settings(\?|$)/, async (route) => {
    if (route.request().method() !== 'GET') {
      writes.push(route.request().method() + ' ' + route.request().url());
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{"ok":true}' });
      return;
    }
    if (!settingsBody) settingsBody = await (await route.fetch()).json();
    const body = JSON.parse(JSON.stringify(settingsBody));
    body.executor = { ...(body.executor ?? {}), sandbox, sandbox_available: true };
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  });

  // 审批中状态：任务详情 + 事件列表（含 status.waiting_approval + 待批命令）
  const now = new Date().toISOString();
  await page.route(/\/api\/v1\/tasks\/[^/]+\/events(\?|$)/, async (route) => {
    await route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify([
        { id: 'evt_000001', seq: 1, task_id: TASK_ID, type: 'message', version: 1, ts: now,
          payload: { role: 'user', text: '把 D 盘那个大文件删掉' } },
        { id: 'evt_000002', seq: 2, task_id: TASK_ID, type: 'action', version: 1, ts: now,
          payload: { tool: 'shell_exec', params: { command: 'rm -rf D:/tmp/big.bin' }, call_id: 'call_a3_probe' } },
        { id: 'evt_000003', seq: 3, task_id: TASK_ID, type: 'status', version: 1, ts: now,
          payload: { state: 'waiting_approval', detail: '等待审批：rm -rf D:/tmp/big.bin', call_id: 'call_a3_probe' } },
      ]),
    });
  });
  // 事件流：给一个空 SSE（不连真后端，避免污染真实任务列表）
  await page.route(/\/api\/v1\/tasks\/[^/]+\/events\/stream/, async (route) => {
    await route.fulfill({ status: 200, contentType: 'text/event-stream', body: '' });
  });

  await page.goto(BASE, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(600);
  const item = page.locator('.taskitem:not(.skeleton)').first();  // 骨架项会被 detach，点它必超时
  await item.waitFor({ state: 'visible', timeout: 15000 });
  await item.click();
  await page.waitForSelector('.tv-composer', { timeout: 10000 });
  await page.waitForTimeout(500);

  const tag = await page.evaluate(probeVisible, '.host-exec-tag');
  const appr = await page.evaluate(probeVisible, '.approval-host-warn');
  const shotTask = await page.screenshot();

  // 设置 → 执行环境
  const fab = page.locator('button[title="设置"]').first();
  await fab.click();
  await page.waitForTimeout(500);
  await page.locator('button.nav-item', { hasText: '执行环境' }).first().click();
  await page.waitForTimeout(400);
  const warn = await page.evaluate(probeVisible, '.host-exec-warn');
  const warnText = warn ? (await page.locator('.host-exec-warn').innerText()).replace(/\s+/g, ' ') : '';

  return { page, tag, appr, warn, warnText, errors, writes, shotTask };
}

const browser = await chromium.launch({ channel: 'msedge', headless: true });

// ═══ A. 宿主模式（sandbox=off）——三处告知必须真的可见 ═══
const off = await openCase(browser, 'off');
const vis = (o) => o && o.w > 0 && o.h > 0 && o.clippedX <= 1 && o.clippedY <= 1
  && o.display !== 'none' && o.visibility !== 'hidden' && Number(o.opacity) > 0.9;

check('① 任务头出现常驻徽标 .host-exec-tag', !!off.tag, JSON.stringify(off.tag));
check('② 徽标真的可见（宽高>0 / 未被裁 / opacity>0.9——.tv-sub 是 0.55 会被淡化）',
  vis(off.tag), off.tag ? `w=${off.tag.w} h=${off.tag.h} clipX=${off.tag.clippedX} opacity=${off.tag.opacity}` : '缺失');
check('③ 徽标文案说明"本机执行"', !!off.tag && off.tag.text.includes('本机执行'), off.tag?.text ?? '');

check('④ 审批栏出现宿主模式告知 .approval-host-warn', !!off.appr, JSON.stringify(off.appr));
check('⑤ 审批栏告知真的可见（独立成行——父栏 overflow:hidden，塞进主行会被裁掉）',
  vis(off.appr), off.appr ? `w=${off.appr.w} h=${off.appr.h} clipX=${off.appr.clippedX}` : '缺失');
check('⑥ 审批栏告知点名"真实作用于你的电脑"',
  !!off.appr && off.appr.text.includes('真实作用于你的电脑'), off.appr?.text ?? '');

check('⑦ 设置页出现告警块 .host-exec-warn', !!off.warn, JSON.stringify(off.warn));
check('⑧ 设置页告警块真的可见', vis(off.warn), off.warn ? `w=${off.warn.w} h=${off.warn.h} clipX=${off.warn.clippedX}` : '缺失');
check('⑨ 设置页告警含"真实电脑"+"不可撤销"（说了在哪跑 + 说了代价）',
  off.warnText.includes('真实电脑') && off.warnText.includes('不可撤销'), off.warnText.slice(0, 100));
check('⑩ 无页面错误', off.errors.length === 0, off.errors.join(' | '));
check('⑪ 全程 0 次写请求落盘（没碰 config.json）', off.writes.length === 0, off.writes.join(' | '));
await off.page.screenshot({ path: path.join(SHOT_DIR, 'host_mode_off_settings.png'), fullPage: false });
writeFileSync(path.join(SHOT_DIR, 'host_mode_task_off.png'), off.shotTask);

// ═══ B. 沙箱开启（sandbox=docker）——三处都不许出现（防误报） ═══
const on = await openCase(browser, 'docker');
check('⑫ 沙箱开启时：任务头徽标不出现', on.tag == null, JSON.stringify(on.tag));
check('⑬ 沙箱开启时：审批栏告知不出现', on.appr == null, JSON.stringify(on.appr));
check('⑭ 沙箱开启时：设置页告警块不出现', on.warn == null, JSON.stringify(on.warn));
check('⑮ 无页面错误（docker 档）', on.errors.length === 0, on.errors.join(' | '));
await on.page.screenshot({ path: path.join(SHOT_DIR, 'host_mode_docker_settings.png'), fullPage: false });

await browser.close();
const bad = results.filter((r) => !r.ok).length;
console.log(`\n结果：${results.length - bad}/${results.length} 通过`);
console.log(`截图目录：${SHOT_DIR}`);
process.exit(bad === 0 ? 0 : 1);
