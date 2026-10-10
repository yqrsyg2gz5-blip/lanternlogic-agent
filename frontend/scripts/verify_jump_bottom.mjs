import { applyLanAuth } from './_lanauth.mjs';
// 探针：验证「回到底部」按钮的位置（二十六轮第 7 批 问题3）
//
// 修前：left:50% + translateX(-50%) —— 相对【.tv-body】居中，
//       而 .tv-body = 消息流 + 右侧面板(.tv-aside, 默认打开 300px)
//       ⇒ 按钮实际偏左，既不在对话区中间、也不在右边（用户："中不中偏不偏"）
// 修后：贴【对话区(.stream)】右边 18px，并用 --aside-w 让开右侧面板
//
// 判据（在同一个页面里跑两次：现行 CSS / 注入旧规则模拟修复前）：
//   ① 按钮右缘距 .stream 右缘 ≈ 18px（修后应成立）
//   ② 按钮完全落在 .stream 内
//   ③ 修后【不再】相对 .tv-body 居中（说明它跟的是对话区，不是整个 body）
//
// 用法：cd frontend && node scripts/_probe_jump_bottom.mjs
import { chromium } from 'playwright-core';

const BASE = 'http://127.0.0.1:5173';
const VW = 1440, VH = 900;

const browser = await chromium.launch({ channel: 'msedge', headless: true });
const page = await browser.newPage({ viewport: { width: VW, height: VH } });
await applyLanAuth(page);
const errors = [];
page.on('pageerror', (e) => errors.push(String(e).slice(0, 120)));

// 进任务页（侧栏首屏有 skeleton 占位，必须排除）
await page.goto(BASE, { waitUntil: 'domcontentloaded' });
const items = page.locator('.taskitem:not(.skeleton)');
await items.first().waitFor({ state: 'visible', timeout: 15000 });
const n = await items.count();
let entered = false;
for (let i = 0; i < Math.min(n, 6); i++) {
  await items.nth(i).click();
  await page.waitForSelector('.tv-composer', { timeout: 10000 });
  await page.waitForTimeout(500);
  const scrollable = await page.evaluate(() => {
    const s = document.querySelector('.stream');
    return !!s && s.scrollHeight > s.clientHeight + 40;
  });
  if (scrollable) { entered = true; break; }
}
if (!entered) {
  console.log('SKIP：前几个任务的内容都不够长，滚不动 ⇒ 触发不了"回到底部"按钮');
  await browser.close();
  process.exit(0);
}

const measure = async (label) => {
  await page.evaluate(() => {
    const s = document.querySelector('.stream');
    s.scrollTop = 0;
    s.dispatchEvent(new Event('scroll'));
  });
  await page.waitForTimeout(400);
  const r = await page.evaluate(() => {
    const b = document.querySelector('.jump-bottom');
    if (!b) return { present: false };
    const br = b.getBoundingClientRect();
    const sr = document.querySelector('.stream').getBoundingClientRect();
    const bodyr = document.querySelector('.tv-body').getBoundingClientRect();
    const a = document.querySelector('.tv-aside');
    const ar = a ? a.getBoundingClientRect() : null;
    const mid = (x) => (x.left + x.right) / 2;
    return {
      present: true,
      gapToStreamRight: Math.round(sr.right - br.right),
      insideStream: br.left >= sr.left - 1 && br.right <= sr.right + 1,
      centeredOnBody: Math.abs(mid(br) - mid(bodyr)) < 3,
      centeredOnStream: Math.abs(mid(br) - mid(sr)) < 3,
      asideW: ar ? Math.round(ar.width) : 0,
      btnRight: Math.round(br.right),
      streamRight: Math.round(sr.right),
      bodyRight: Math.round(bodyr.right),
    };
  });
  console.log(`[${label}]`, JSON.stringify(r));
  return r;
};

const fixed = await measure('现行 CSS（修复后）');

// 注入旧规则，模拟"修复前"，在同一页面里做对照（不改仓库文件）
await page.addStyleTag({
  content: '.jump-bottom{left:50% !important;right:auto !important;transform:translateX(-50%) !important;}',
});
const old = await measure('注入旧规则（模拟修复前）');

console.log('\n=== 判定 ===');
const checks = [
  ['① 修后：按钮右缘距对话区右缘 ≈ 18px', fixed.present && Math.abs(fixed.gapToStreamRight - 18) <= 2, `实际 ${fixed.gapToStreamRight}px`],
  ['② 修后：按钮完全落在对话区内', !!fixed.insideStream, JSON.stringify({ l: fixed.btnRight - 34, r: fixed.btnRight, streamRight: fixed.streamRight })],
  ['③ 修后：不再相对整个 body 居中（跟的是对话区）', fixed.centeredOnBody === false, `centeredOnBody=${fixed.centeredOnBody}`],
  ['④ 对照组：旧规则下确实相对 body 居中（证明这个 bug 真实存在）', old.centeredOnBody === true, `centeredOnBody=${old.centeredOnBody}`],
  ['⑤ 对照组：旧规则下按钮明显偏左（距对话区右缘 > 100px）', old.gapToStreamRight > 100, `实际 ${old.gapToStreamRight}px`],
  ['⑥ 无页面错误', errors.length === 0, errors.join(' | ')],
];
let bad = 0;
for (const [name, ok, detail] of checks) {
  if (!ok) bad++;
  console.log(`${ok ? 'OK  ' : 'BAD '} ${name}   ${detail}`);
}
console.log(`\n面板宽度对照：修后 gap=${fixed.gapToStreamRight}px（aside=${fixed.asideW}px）｜旧规则 gap=${old.gapToStreamRight}px`);
console.log(`结果：${checks.length - bad}/${checks.length} 通过`);
await browser.close();
process.exit(bad === 0 ? 0 : 1);
