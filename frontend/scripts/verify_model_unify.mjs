import { applyLanAuth } from './_lanauth.mjs';
// 第 48/49 班 UI 验证（playwright-core + 系统 Edge）
// 十二轮 🔴4 重写：字面 check(...,true) 清零；选择器一律容器作用域；
// 等真实渲染而非固定 sleep；任何异常落成 BAD 而不是崩；
// 断言覆盖：输入框宽、按钮单行不整宽（逐档）、弹层互斥、预设三元组、
// 两页同源、Escape/点外、模型切换还原、零报错。
// 跑完【必须人工看一遍截图】（docs/collab/screens/49-*.png）。
import { chromium } from 'playwright-core';
import { mkdirSync, readFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const BASE = 'http://127.0.0.1:5173';
const SHOT = process.env.SHOT_DIR ?? path.join(os.tmpdir(), 'agent-shell-shots');  // 十四轮②：默认写系统临时目录——仓库证据图只能显式传参覆盖，杜绝静默改写
mkdirSync(SHOT, { recursive: true });

const results = [];
const check = (name, ok, detail = '') => {
  results.push({ name, ok: !!ok, detail: String(detail) });
  console.log((ok ? 'OK  ' : 'BAD ') + name + (detail ? '  | ' + detail : ''));
};

const browser = await chromium.launch({ channel: 'msedge', headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
await applyLanAuth(page);
const consoleErrors = [];
const pageErrors = [];
page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text().slice(0, 120)); });
page.on('pageerror', (e) => pageErrors.push(String(e).slice(0, 120)));

try {
  // ═══ ① 逐档：按钮单行、不整宽、零横向溢出（十二轮 🔴1 回归断言）═══
  await page.goto(BASE, { waitUntil: 'domcontentloaded' });
  for (const w of [360, 480, 600, 720, 760, 761, 900, 1440]) {
    await page.setViewportSize({ width: w, height: 900 });
    await page.waitForTimeout(120);
    const m = await page.evaluate(() => {
      const ta = document.querySelector('main textarea');
      const box = ta ? ta.parentElement : null;
      const btn = document.querySelector('main .btn-primary');
      const pick = document.querySelector('main .ctl-model');
      const br = btn ? btn.getBoundingClientRect() : null;
      const pr = pick ? pick.getBoundingClientRect() : null;
      return {
        boxW: box ? Math.round(box.getBoundingClientRect().width) : 0,
        btnW: br ? Math.round(br.width) : 0,
        sameLine: br && pr ? Math.abs((br.y + br.height / 2) - (pr.y + pr.height / 2)) < 2 : false,  // 十四轮③：比中心（顶部差会把'居中但高度不同'误判）
        overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      };
    });
    check(w + 'px：单行 + 按钮非整宽 + 溢出0',
      m.boxW > 0 && m.btnW > 40 && m.btnW < 260 && m.sameLine && m.overflow <= 0,
      'box=' + m.boxW + ' btn=' + m.btnW + ' 同行=' + m.sameLine + ' 溢出=' + m.overflow);
  }

  // ═══ ② 输入框宽度（保持 780）═══
  const boxW = await page.evaluate(() => {
    const ta = document.querySelector('main textarea');
    return ta ? Math.round(ta.parentElement.getBoundingClientRect().width) : 0;
  });
  check('首页输入框宽度 = 780', boxW === 780, '实测 ' + boxW + 'px');

  // ═══ ③ 预设三元组（🔴3：provider 显式且正确，静态断言）═══
  // ★ A5（2026-10-04）：预设表合并成一份后是 8 项（设置页原来就 8 项，
  //   切换器只有 6 ⇒ Claude/Mock 切不回来）。逐项核 provider 显式写。
  const src = readFileSync(new URL('../src/modelPresets.ts', import.meta.url), 'utf-8');
  const want = { mimo: 'mimo', deepseek: 'deepseek', qwen: 'qwen', glm: 'glm', kimi: 'kimi',
                 anthropic: 'anthropic', ollama: 'ollama', mock: 'mock' };
  const tripleOK = Object.entries(want).every(([k, prov]) =>
    new RegExp(k + ':\\s*\\{\\s*provider:\\s*\'' + prov + '\'').test(src));
  check('8 个预设 provider 显式且正确（ollama≠qwen）', tripleOK, '');

  // ═══ ④ 首页弹层：8 预设 + 友好名 ═══
  const heroBtn = page.locator('main .ctl-model').first();
  await heroBtn.waitFor({ state: 'visible', timeout: 5000 });
  await page.setViewportSize({ width: 1440, height: 900 });
  const beforeX = (await heroBtn.boundingBox())?.x ?? -1;
  await heroBtn.click();
  const pop = page.locator('main .ctl-pop').first();
  await pop.waitFor({ state: 'visible', timeout: 3000 });
  const items = pop.locator('.ctl-item');
  const n = await items.count();
  const firstText = (await items.first().textContent()) ?? '';
  check('弹层 8 预设（两页同源）', n === 8, '实测 ' + n);
  check('友好名在前（MiMo（小米）…）', firstText.includes('MiMo（小米）'), firstText.slice(0, 24));
  await page.screenshot({ path: SHOT + '/49-首页-模型弹层.png' });

  // ═══ ⑤ Escape / 点外关闭 ═══
  await page.keyboard.press('Escape');
  await page.waitForTimeout(150);
  check('Escape 关闭', !(await pop.isVisible().catch(() => false)));
  await heroBtn.click();
  await pop.waitFor({ state: 'visible' });
  await page.mouse.click(720, 200);
  await page.waitForTimeout(150);
  check('点外部关闭', !(await pop.isVisible().catch(() => false)));

  // ═══ ⑥ 切换 + 位置不跳 + 还原 ═══
  // 十三轮：拦截 setModel 请求（fulfill 假响应）——验证 UI 行为而不真写 config.json；
  // 一旦脚本崩在两步之间，用户的模型也不会被留在别的档位上。
  await page.route('**/api/v1/settings/model*', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true }) }));
  await heroBtn.click();
  await pop.waitFor({ state: 'visible' });
  await pop.locator('.ctl-item', { hasText: '智谱 GLM' }).click();
  await page.waitForFunction(() => (document.querySelector('main .ctl-model')?.textContent ?? '').includes('智谱 GLM'));
  const afterX = (await page.locator('main .ctl-model').first().boundingBox())?.x ?? -2;
  check('切换成功（智谱 GLM）', ((await heroBtn.textContent()) ?? '').includes('智谱 GLM') === true);
  check('按钮 x 不跳（min-width 类）', Math.abs(afterX - beforeX) < 1, beforeX + '→' + afterX);
  await heroBtn.click();
  await pop.waitFor({ state: 'visible' });
  await pop.locator('.ctl-item', { hasText: 'MiMo（小米）' }).click();
  await page.waitForFunction(() => (document.querySelector('main .ctl-model')?.textContent ?? '').includes('MiMo（小米）'));
  check('已还原 MiMo（小米）', ((await heroBtn.textContent()) ?? '').includes('MiMo（小米）') === true);
  await page.unroute('**/api/v1/settings/model*');

  // ═══ ⑦ 任务页：等真实渲染 + 容器作用域（十二轮 🔴4 核心修正）═══
  // ★ 同类变体补齐（2026-10-04 A3 批）：等**非骨架**项——首屏 .taskitem.skeleton
  //   会在真数据到达时被 detach，点它必超时（verify_usage_badge.mjs 早已记录该坑）。
  const task = page.locator('.taskitem:not(.skeleton)').first();
  await task.waitFor({ state: 'visible', timeout: 15000 }).catch(() => undefined);
  if (await task.count()) {
    await task.click();
    await page.waitForSelector('.tv-composer', { timeout: 10000 });
    const scoped = page.locator('.tv-composer .ctl-model');
    check('任务页切换器在 composer 内（容器作用域命中）', (await scoped.count()) === 1);
    await scoped.click();
    await page.waitForFunction(() => {
      const el = document.querySelector('.tv-composer .ctl-pop');
      if (!el) return false;
      const r = el.getBoundingClientRect();
      return r.width > 0 && r.height > 0;
    }, { timeout: 5000 });
    const tvState = await page.evaluate(() => {
      const pop = document.querySelector('.tv-composer .ctl-pop');
      const r = pop.getBoundingClientRect();
      return { open: r.width > 0, items: [...pop.querySelectorAll('.ctl-item')].map((x) => x.textContent ?? '') };
    });
    check('任务页同 8 预设（友好名）',
      tvState.open && tvState.items.length === 8 && tvState.items[0].includes('MiMo（小米）'),
      'open=' + tvState.open + ' items=' + tvState.items.length);
    await page.screenshot({ path: SHOT + '/49-任务页-模型弹层.png' });

    // ═══ ⑧ 弹层互斥（十二轮 🔴2 回归断言）═══
    await page.locator('.tv-composer .ctl-mode').click();
    await page.waitForTimeout(250);
    const visiblePops = await page.evaluate(() =>
      [...document.querySelectorAll('.ctl-pop')].filter((el) => {
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0;
      }).length);
    check('互斥：开权限时模型弹层自动关（可见弹层=1）', visiblePops === 1, '可见=' + visiblePops);
    await page.locator('.tv-composer .ctl-model').click();
    await page.waitForTimeout(250);
    const visiblePops2 = await page.evaluate(() =>
      [...document.querySelectorAll('.ctl-pop')].filter((el) => {
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0;
      }).length);
    check('互斥反向：开模型时权限弹层关（可见弹层=1）', visiblePops2 === 1, '可见=' + visiblePops2);
    await page.keyboard.press('Escape');
  } else {
    check('任务页一致性（无任务）', false, '列表为空——本条必须人工补测');
  }
} catch (e) {
  check('脚本自身崩溃（十二轮 🔴4：不允许）', false, String(e).slice(0, 160));
}

// 二十五轮 🟠5①：设置页白屏断言——【立论更正】。二十二轮的立论
// "pageerror=0 测不出白屏"不成立：验证员实测 P0① 旧写法（inert effect
// 误插进 Escape useEffect 回调体）在 page.route 反事实复现时有 3 条
// pageerror——pageerror=0 本来能抓到那次白屏。rootLen 断言的真实价值
// 是覆盖【pageerror 抓不到的另一半盲区】：渲染成功（零报错）但内容为空/
// 空壳（如条件渲染整体短路、portal 挂载失败）。两者互补，互不替代。
// 本轮补三条"内容为空"断言：可见文本量、分区标题文本、可交互控件数。
// 二十六轮第2批 4-1：本段原在 try/catch 之外——CF-B（设置入口消失）时
// L173 click 超时直接崩：无汇总行、已判定的 18 条全部丢失。整段纳入
// try/catch；rootLen 长度判据（35751 与正常态一模一样，判别力≈0）换成
// 【实义判据】：断言侧栏含已知分区标题文本。
try {
  await page.locator('.settings-fab', { hasText: '设置' }).click();
  await page.waitForTimeout(500);
  const setPage = await page.evaluate(() => ({
    rootLen: document.getElementById('root') ? document.getElementById('root').innerHTML.length : 0,
    navItems: document.querySelectorAll('.nav-item').length,
    dialog: !!document.querySelector('[role="dialog"]'),
    visibleTextLen: (document.querySelector('[role="dialog"]')?.innerText ?? '').length,
    navTexts: [...document.querySelectorAll('.nav-item')].map((el) => (el.innerText ?? '').trim()).filter(Boolean),
    controls: document.querySelectorAll('[role="dialog"] button, [role="dialog"] input, [role="dialog"] select').length,
  }));
  const NAV_TITLES = ['常规', '模型设置', '视频生成', '执行环境', '技能', '项目', '自动化', '使用统计', '关于'];
  const knownTitles = NAV_TITLES.filter((t) => setPage.navTexts.includes(t));
  check('设置页非白屏（侧栏含已知分区标题：常规/模型设置/关于）',
    knownTitles.includes('常规') && knownTitles.includes('模型设置') && knownTitles.includes('关于'),
    `命中的分区标题=${knownTitles.join('/')}（rootLen 仅参考=${setPage.rootLen}）`);
  check('设置页 9 个分区在位', setPage.navItems === 9, 'nav-item=' + setPage.navItems);
  check('设置页 dialog/aria-modal 在位', setPage.dialog === true);
  check('设置页可见文本量 > 200（空壳盲区）', setPage.visibleTextLen > 200, 'innerText=' + setPage.visibleTextLen);
  check('设置页分区标题文本可见（渲染成功但内容为空的盲区）', setPage.navTexts.length >= 8, '有文本的分区=' + setPage.navTexts.length);
  check('设置页可交互控件 ≥ 15（空壳盲区）', setPage.controls >= 15, 'controls=' + setPage.controls);
  await page.keyboard.press('Escape');
  await page.waitForTimeout(200);
} catch (e) {
  check('设置页断言段崩溃（二十六轮 4-1：必须落 BAD 不许崩）', false, String(e).slice(0, 160));
}

check('pageerror = 0', pageErrors.length === 0, pageErrors.join('; '));
check('console error = 0', consoleErrors.length === 0, consoleErrors.join('; '));
await page.setViewportSize({ width: 1440, height: 900 });
await page.goto(BASE, { waitUntil: 'domcontentloaded' });
await page.screenshot({ path: SHOT + '/49-首页.png' });
await browser.close();

const bad = results.filter((r) => !r.ok).length;
console.log('\n结果：' + (results.length - bad) + '/' + results.length + ' 通过');
process.exit(bad ? 1 : 0);
