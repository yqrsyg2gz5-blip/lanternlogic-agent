// Phase 3 ⑦ 原型验证（真浏览器）：?proto=chat 的对话界面原型
//
// 判据：
//   ① 带上 ?proto=chat 时进入原型；不带时**完全不出现**（不影响正常界面）
//   ② 一轮一组：用户气泡**右对齐**、助手回复在左（不是一锅日志流）
//   ③ 工具调用是可折叠卡片：默认收起 → 点开能看到参数与结果
//   ④ 失败的工具调用有明显的失败态（不能和成功长一样）
//   ⑤ 流式感：有"生成中"与光标；思考过程可折叠（默认展开、能收起）
//   ⑥ 来源做成胶囊；助手消息有复制按钮（点了有反馈）
//   ⑦ 底部输入区常驻 + "跳到最新" + 模型胶囊
//
// ★ 原型不调后端：这里断言"零网络请求"（除了页面本身），防止它偷偷依赖接口。
// 用法：cd frontend && node scripts/verify_chat_proto.mjs
import { chromium } from 'playwright-core';
import os from 'node:os';
import path from 'node:path';
import { mkdirSync } from 'node:fs';
import { applyLanAuth } from './_lanauth.mjs';

const BASE = 'http://127.0.0.1:5173';
const SHOTS = path.join(os.tmpdir(), 'agent-shell-shots', 'chat-proto');
mkdirSync(SHOTS, { recursive: true });
const results = [];
const check = (name, ok, detail = '') => {
  results.push({ name, ok: !!ok });
  console.log((ok ? 'OK  ' : 'BAD ') + name + (detail ? '  | ' + detail : ''));
};

const browser = await chromium.launch({ channel: 'msedge', headless: true });

// ── 不带参数：原型绝不该出现 ──
const plain = await browser.newPage({ viewport: { width: 1440, height: 900 } });
await applyLanAuth(plain);
await plain.goto(BASE, { waitUntil: 'domcontentloaded' });
await plain.waitForTimeout(800);
check('① 不带 ?proto=chat 时原型不出现（不影响正常界面）',
  await plain.locator('.cp-wrap').count() === 0);
await plain.close();

// ── 带参数：原型 ──
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
await applyLanAuth(page);
const errors = [];
const reqs = [];
page.on('pageerror', (e) => errors.push(String(e).slice(0, 140)));
page.on('request', (r) => { if (/\/api\/v1\/(tasks|capabilities|settings|models)/.test(r.url())) reqs.push(r.url()); });
await page.goto(`${BASE}/?proto=chat`, { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(900);
check('② 带 ?proto=chat 时进入原型', await page.locator('.cp-wrap').count() === 1);

// 用户气泡右对齐（用几何判断，不靠类名）
const geom = await page.evaluate(() => {
  const turn = document.querySelector('.cp-turn');
  const user = turn?.querySelector('.cp-bubble-user');
  const asst = turn?.querySelector('.cp-text');
  if (!user || !asst) return null;
  const t = turn.getBoundingClientRect();
  const u = user.getBoundingClientRect();
  const a = asst.getBoundingClientRect();
  return { turnL: t.left, turnR: t.right, userL: u.left, userR: u.right, asstL: a.left };
});
// 判据用**贴边**而不是中心：助手正文是整宽块，中心必然等于容器中心
check('③ 用户气泡贴右、助手正文贴左（几何验证）',
  !!geom && Math.abs(geom.userR - geom.turnR) < 3 && Math.abs(geom.asstL - geom.turnL) < 3
  && geom.userL > geom.turnL + 40,
  geom ? `用户右=${Math.round(geom.userR)} 容器右=${Math.round(geom.turnR)} 助手左=${Math.round(geom.asstL)} 容器左=${Math.round(geom.turnL)}` : '取不到几何');

// 工具卡片：默认收起 → 点开有内容
const card = page.locator('.cp-tool').first();
check('④ 工具调用是卡片（不是裸日志行）', await card.count() > 0);
check('⑤ 卡片默认收起（点开前看不到参数区）', await card.locator('.cp-pre').count() === 0);
await card.locator('.cp-tool-head').click();
await page.waitForTimeout(250);
const bodyText = (await card.locator('.cp-tool-body').innerText().catch(() => '')).replace(/\s+/g, ' ');
check('⑥ 点开后能看到参数与结果', /参数/.test(bodyText) && /结果/.test(bodyText) && bodyText.length > 20,
  bodyText.slice(0, 70));
check('⑦ 失败的工具调用有失败态', await page.locator('.cp-tool.cp-fail').count() > 0);

// 流式与思考
check('⑧ 有"生成中"与光标（流式感）',
  await page.locator('.cp-live').count() > 0 && await page.locator('.cp-cursor').count() > 0);
const stepCount = await page.locator('.cp-step').count();
check('⑨ 思考过程默认展开（能看到步骤）', stepCount >= 3, `${stepCount} 步`);
await page.locator('.cp-think-head').first().click();
await page.waitForTimeout(200);
check('⑩ 思考过程能收起（不刷屏）', await page.locator('.cp-step').count() === 0);

// 来源 + 复制
check('⑪ 交付来源做成胶囊', await page.locator('.cp-sources .cp-chip').count() > 0);
const copy = page.locator('.cp-copy').first();
await copy.click();
await page.waitForTimeout(250);
check('⑫ 复制按钮点了有反馈', /已复制/.test(await copy.innerText().catch(() => '')));

// 底部与跳转
check('⑬ 底部输入区常驻（含模型胶囊与发送键）',
  await page.locator('.cp-composer textarea').count() > 0
  && await page.locator('.cp-composer-row .cp-chip').count() > 0
  && await page.locator('.cp-send').count() > 0);
check('⑭ 有"跳到最新"', await page.locator('.cp-jump').count() > 0);

check('⑮ 原型不调任何业务接口（防偷偷依赖后端）', reqs.length === 0, reqs.slice(0, 2).join(' | '));
check('⑯ 无页面错误', errors.length === 0, errors.join(' | '));

await page.screenshot({ path: path.join(SHOTS, 'chat-proto-desktop.png'), fullPage: false });
await page.setViewportSize({ width: 390, height: 844 });
await page.waitForTimeout(400);
const mobileOverflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 2);
check('⑰ 窄屏（390px）不横向溢出', !mobileOverflow);
await page.screenshot({ path: path.join(SHOTS, 'chat-proto-mobile.png'), fullPage: false });

await browser.close();
const bad = results.filter((r) => !r.ok).length;
console.log(`\n结果：${results.length - bad}/${results.length} 通过`);
console.log(`截图：${SHOTS}`);
process.exit(bad === 0 ? 0 : 1);
