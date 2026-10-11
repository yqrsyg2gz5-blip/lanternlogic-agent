// ★★ A-3（2026-10-07）：**群聊页手机档**回归检查 —— 直接在用户那个入口（8642 的 dist）上量。
//
// 为什么单独有这个脚本：
//   交接书里记着"手机端量了两次都卡在**进团队页**那一步" ✗ —— 窄档下导航收进汉堡菜单「☰」，
//   点它老卡。**正解**（交接书自己也写了）：**先宽屏打开 → 进群 → 再把视口改成 390×844** ✓
//   这样就绕开了汉堡菜单 ✓ 本脚本就是照这个顺序写的 ✓ 跑起来 10 秒出结果 ✓
//
// 用法：cd frontend && node scripts/verify_team_mobile.mjs
//   （需要后端在 8642 上跑着；会用**真实群**，只读，不改任何数据 ✓）
//
// 量的是"照着 390px 实看改的三处"有没有被改回去：
//   ① 窄档**收成一列**（群列表让位、聊天占满）✓ 没选群时列表占满 ✓
//   ② 「还差 N 个决定」那块**常驻**在滚动区**外面**（不然一进群就被自动滚到底顶出屏幕 ✗）
//   ③ 那条命令在窄档**不被切成两三个字**（它原来被压到 13~41px，而命令本身 265px ✗）
//
// ★ ②③ 需要"有人在等审批"才会出现 ⇒ 脚本**临时拦一下那个只读接口**喂三条假数据 ✓
//   （只换数据、代码一行不改 ✓ 全仓没有一个真待批时也能量 ✓）
import { chromium } from 'playwright-core';
import { applyLanAuth } from './_lanauth.mjs';

const BASE = process.env.DSH_APP_BASE || 'http://127.0.0.1:8642';
const WIDE = { width: 1440, height: 950 };
const PHONE = { width: 390, height: 844 };   // 用户说的"手机那么宽" ✓

const results = [];
const check = (name, ok, extra = '') => {
  results.push({ name, ok });
  console.log(`${ok ? 'OK ' : 'BAD'}  ${name}${extra ? '  | ' + extra : ''}`);
};

const browser = await chromium.launch({ channel: 'msedge', headless: true });
const page = await (await browser.newContext({ viewport: WIDE })).newPage();
await applyLanAuth(page);
const errors = [];
page.on('pageerror', (e) => errors.push(String(e).slice(0, 160)));

await page.goto(BASE, { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(1500);

// ── 宽屏下进团队 → 群聊（这一步绝碰不到汉堡菜单 ✓）──
await page.locator('button, a').filter({ hasText: /^团队$/ }).first().click();
await page.waitForTimeout(800);
const chatTab = page.locator('button, div').filter({ hasText: /^群聊$/ }).first();
if (await chatTab.count()) { await chatTab.click(); await page.waitForTimeout(800); }
check('① 宽屏下进得了群聊页', await page.locator('.team-grouplist').count() > 0);

// ── 缩到手机宽：**没选群**时列表应当占满（不再只占 42%）──
await page.setViewportSize(PHONE);
await page.waitForTimeout(1200);
const noGroup = await page.evaluate(() => {
  const w = (s) => Math.round(document.querySelector(s)?.getBoundingClientRect().width ?? 0);
  const d = (s) => getComputedStyle(document.querySelector(s)).display;
  return { list: w('.team-grouplist'), col: w('.team-chat-col'),
           colDisplay: d('.team-chat-col'), docW: document.documentElement.scrollWidth };
});
check('② 手机宽下没有横向滚动', noGroup.docW <= PHONE.width + 1, `docScrollW=${noGroup.docW}`);
check('③ 没选群时：列表占满、聊天列收起', noGroup.list > 250 && noGroup.colDisplay === 'none',
  `list=${noGroup.list} col=${noGroup.colDisplay}`);

// ── 选一个群：应当**收成一列**（列表让位）──
const gname = await page.locator('.team-group-name').first().innerText().catch(() => '');
await page.locator('.team-group-name').first().click();
await page.waitForTimeout(2000);

// 只拦"待批清单"这一个只读接口，喂三条假数据 ⇒ 让那块汇总真渲染出来（代码一行不改）
await page.route('**/api/v1/team/groups/*/approvals', (route) => route.fulfill({
  status: 200, contentType: 'application/json',
  body: JSON.stringify({
    ok: true, group_id: 'x', count: 3,
    approvals: [
      { task_id: 't1', call_id: 'c1', title: '整理下载目录里最大的十个文件并归档', command: 'Remove-Item D:\\旧备份\\2026 -Recurse -Force' },
      { task_id: 't2', call_id: 'c2', title: '写周报', command: 'python -m pytest tests/test_team.py -q' },
      { task_id: 't3', call_id: 'c3', title: '把这段录音转成文字', command: 'ffmpeg -i D:\\录音\\会议.m4a -ar 16000 out.wav' },
    ],
  }),
}));
await page.waitForTimeout(3200);

const phone = await page.evaluate(() => {
  const r = (el) => { const b = el.getBoundingClientRect(); return { x: Math.round(b.x), y: Math.round(b.y), w: Math.round(b.width), h: Math.round(b.height) }; };
  const out = { docW: document.documentElement.scrollWidth };
  const list = document.querySelector('.team-grouplist');
  const col = document.querySelector('.team-chat-col');
  out.listDisplay = getComputedStyle(list).display;
  out.colW = Math.round(col.getBoundingClientRect().width);
  out.backDisplay = getComputedStyle(document.querySelector('.team-back')).display;
  const b = [...document.querySelectorAll('b')].find((x) => x.textContent.includes('个决定等你'));
  if (b) {
    const card = b.closest('.card');
    out.card = r(card);
    // 卡片必须**落在可视区里**（常驻 ✓）—— 搬走前它在滚动区最上面，实测在可视区上方 573px ✗
    const feed = document.querySelector('.team-chat-col div[style*="overflow-y: auto"]');
    out.inView = card.getBoundingClientRect().top >= 0 && card.getBoundingClientRect().bottom <= innerHeight;
    out.feedTop = feed ? Math.round(feed.getBoundingClientRect().top) : null;
    const rows = [...card.querySelectorAll('div')].filter((d) => d.style.flexWrap === 'wrap' && d.querySelector('button'));
    out.cmds = rows.map((d) => {
      const mono = d.querySelector('.mono');
      return { visible: Math.round(mono.getBoundingClientRect().width), full: mono.scrollWidth,
               cut: mono.scrollWidth > mono.getBoundingClientRect().width + 2 };
    });
  }
  return out;
});

check(`④ 选中「${gname.trim()}」后收成一列（列表让位）`, phone.listDisplay === 'none' && phone.colW > 250,
  `list=${phone.listDisplay} col=${phone.colW}`);
check('⑤ 出现「← 群列表」（手机上一列时的回头路）', phone.backDisplay !== 'none', `display=${phone.backDisplay}`);
check('⑥ 那块「还差 N 个决定」**在可视区里**（不被自动滚走）',
  !!phone.card && phone.inView, phone.card ? `card.y=${phone.card.y} h=${phone.card.h}` : '没渲染出来');
check('⑦ 待批里的命令**没被切**（窄档换行显示全）',
  !!phone.cmds?.length && phone.cmds.every((c) => !c.cut),
  (phone.cmds ?? []).map((c) => `${c.visible}/${c.full}`).join(' '));
check('⑧ 手机宽下全程无页面错误', errors.length === 0, errors.slice(0, 2).join(' | '));

// ── 点「← 群列表」回得去 ──
await page.locator('.team-back').click();
await page.waitForTimeout(1000);
const back = await page.evaluate(() => ({
  hasGroup: !!document.querySelector('.team-chat-grid.has-group'),
  listW: Math.round(document.querySelector('.team-grouplist')?.getBoundingClientRect().width ?? 0),
}));
check('⑨ 点「← 群列表」回得到列表', !back.hasGroup && back.listW > 250, JSON.stringify(back));

await browser.close();
const pass = results.filter((r) => r.ok).length;
console.log(`\n结果：${pass}/${results.length} 通过（${BASE} @ ${PHONE.width}×${PHONE.height}）`);
process.exit(results.every((r) => r.ok) ? 0 : 1);
