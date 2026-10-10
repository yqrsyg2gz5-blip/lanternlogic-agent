// 第十七/十八轮：composer 布局 1px 全区间扫描（正式入库版）。
// 判据（十八轮 🔴4-3 修正——选判据前先做过反事实验证）：
//   ① send 所在行按钮成员数 ≥ 3（tail-group：meeting+gain+send 恒同排；
//      "≥2"判据在修复前也通过——十六轮 send-cluster 已保证 gain+send 绑定，
//      区分不出本轮改动，故废弃）
//   ② 无横向溢出（弹层关闭时）
//   ③ 弹层打开时右缘 ≤ 视口、docOverflow=0（夹取）
// 用法：cd frontend && node scripts/verify_composer_1px.mjs
//   默认扫 360→1440 步长 1（1081 档）；SHOT=1 时对问题档截图到 $TEMP/agent-shell-shots
import { chromium } from 'playwright-core';
import os from 'node:os';
import path from 'node:path';
import { mkdirSync } from 'node:fs';
import { applyLanAuth } from './_lanauth.mjs';

const BASE = 'http://127.0.0.1:5173';
const STEP = Number(process.env.STEP ?? 1);
const SHOT = process.env.SHOT === '1';
const dir = path.join(os.tmpdir(), 'agent-shell-shots');
mkdirSync(dir, { recursive: true });

const browser = await chromium.launch({ channel: 'msedge', headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
// ★ 漏网补上（2026-10-04 A3 批实测）：手机直连开启后后端密码闸门对所有 /api/* 生效，
//   本脚本是唯一没走 _lanauth 的任务页扫描脚本 ⇒ 侧栏任务列表永远 401、只剩骨架，
//   脚本 rc=1 ——是**测试基建缺口**（问题4 那批声称"脚本已统一处理"，
//   实际漏了本文件），不是产品缺陷。补 applyLanAuth 后恢复可跑。
await applyLanAuth(page);
await page.goto(BASE, { waitUntil: 'domcontentloaded' });
// ★ 同类变体补齐：侧栏首屏先渲染 .taskitem.skeleton 占位骨架，
//   点它会"element was detached from the DOM"超时——这个坑 verify_usage_badge.mjs /
//   verify_jump_bottom.mjs 早就注释记录了，本脚本与 verify_model_unify.mjs 漏了同步。
await page.locator('.taskitem:not(.skeleton)').first().waitFor({ state: 'visible', timeout: 15000 });
await page.locator('.taskitem:not(.skeleton)').first().click();
await page.waitForSelector('.tv-composer', { timeout: 10000 });

const badMembers = [];
const badOv = [];
const badPop = [];
let minMembers = 99;
let prev = '';
for (let w = 360; w <= 1440; w += STEP) {
  await page.setViewportSize({ width: w, height: 900 });
  const m = await page.evaluate(() => {
    const send = document.querySelector('.tv-composer .send-btn');
    const sr = send ? send.getBoundingClientRect() : null;
    const btns = [...document.querySelectorAll('.tv-composer .composer-controls button')]
      .filter((el) => el.getBoundingClientRect().width > 0);
    const members = sr ? btns.filter((el) => Math.abs(el.getBoundingClientRect().y - sr.y) < 6).length : 0;
    return { members, ov: document.documentElement.scrollWidth - document.documentElement.clientWidth };
  });
  minMembers = Math.min(minMembers, m.members);
  if (m.members < 3) badMembers.push(w);
  if (m.ov > 0) badOv.push(w);
  const state = (m.members < 3 ? 'M' : '') + (m.ov > 0 ? 'O' : '');
  if (state !== prev) { if (SHOT && state) console.log('band', w, state); prev = state; }
}
// 弹层夹取（含上轮最差档）+ 十九轮🔴3：先开弹层【再单跳大 resize】
// （旧脚本 L50-52 先改视口再开弹层——结构上测不出"开着弹层缩窗"这个 bug）
for (const w of [368, 390, 414, 460, 480, 490, 492, 520]) {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.waitForTimeout(120);
  await page.locator('.tv-composer .ctl-model').click();
  await page.waitForTimeout(150);
  await page.setViewportSize({ width: w, height: 900 });
  await page.waitForTimeout(250);
  const m = await page.evaluate(() => {
    const pop = document.querySelector('.tv-composer .ctl-pop');
    if (!pop) return { missing: true };
    const r = pop.getBoundingClientRect();
    return { L: Math.round(r.left), R: Math.round(r.right), vw: document.documentElement.clientWidth, ov: document.documentElement.scrollWidth - document.documentElement.clientWidth };
  });
  if (m.missing || m.R > m.vw || m.L < 0 || m.ov > 0) badPop.push(w + '(L=' + m.L + ',R=' + m.R + ')');
  await page.keyboard.press('Escape');
}
await browser.close();

const ok = !badMembers.length && !badOv.length && !badPop.length;
console.log('minRowMembers =', minMembers === 99 ? '-' : minMembers);
console.log('成员<3 档数:', badMembers.length, badMembers.length ? '(' + badMembers[0] + '..' + badMembers[badMembers.length - 1] + ')' : '');
console.log('溢出档数:', badOv.length, badOv.length ? '(' + badOv[0] + '..' + badOv[badOv.length - 1] + ')' : '');
console.log('弹层越界:', badPop.length ? badPop.join(', ') : '无');
console.log(ok ? 'RESULT: PASS' : 'RESULT: FAIL');
process.exit(ok ? 0 : 1);
