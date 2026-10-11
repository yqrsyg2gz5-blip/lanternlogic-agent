import { applyLanAuth } from './_lanauth.mjs';
// 施工单 §2 前端整改锚点：560–660 档 usage badge 被 .ctl-pop 盖住
//   → 用户点「用量」却命中弹层里的「本地（Ollama）」→ pick() 真发
//     POST /api/v1/settings/model ⇒ 静默改写用户 config.json。
//
// 判据（逐档 560/580/600/620/640/660，视口高 700——与审计读数同一几何）：
//   ① 弹层打开时，badge【整块 5 点】（中心/上缘/下缘/左缘/右缘中点）的
//      elementFromPoint 必须全部落在 badge 子树内
//      （修前：中心命中 BUTTON.ctl-item「本地（Ollama）」；
//        500–540 档只死【上缘 8px】——落在 .ctl-pop 内边距上，无 handler ⇒ 点了没反应，
//        故只采中心会漏判，6⑥ 那组用同一 5 点判据）
//   ② 用【真实鼠标坐标】点 badge 中心 → 0 次模型写请求 + 恰 1 层可见弹层
//      且该层是「用量」（修前：1 次 POST + 0 层可见——模型被切走）
//   ③ Playwright 的 locator.click('.usage-badge')（actionability：拒绝被遮挡元素）
//      不得超时/被拒（修前：必然被 .ctl-pop 拦截 → 报 intercepted）
//   ④ 三方向（权限 / 模型 / 用量）× 双向（A→B / B→A）切换后仍【恰 1 层】
//      且是后来的那一层（修前：model→usage 这一步做不到）
//   ⑤ 弹层【高度】敏感性：600 宽 × 高 {540,620,700,780,900} 逐档同判据
//      （专治"把 badge 挪 N px"式假修——弹层高度与视口高无关，位移解在别的
//       高度会重新相交；层级解与高度无关）
//   ⑥ 审计证据里另两处：500/520/540（上缘 8px 死区）与 320/360（权限弹层盖 badge）
//   ⑦ 反向关切：抬上去的 badge 是否反过来挡住弹层项——断言无任何 .ctl-item
//      的命中中心被盖，且「本地（Ollama）」仍能非 force 点中
//
// 安全（施工纪律 ⑧）：本脚本**绝不** force click；所有写请求
//   （POST/PUT/PATCH/DELETE /api/v1/settings**）一律 page.route 拦截 + 假响应，
//   请求本体只落进内存数组计数——后端与 config.json 全程零写入。
// 用法：cd frontend && node scripts/verify_usage_badge.mjs
//   DIAG=1 打印逐档几何（.ctl-pop ∩ .usage-badge）
import { chromium } from 'playwright-core';

const BASE = 'http://127.0.0.1:5173';
const WIDTHS = [560, 580, 600, 620, 640, 660];
const VH = 700;              // 审计读数的几何（badge y596..624 ⇔ 视口高 700）
const SETTLE = 500;          // ≥430ms：.tv-composer 有 transition: margin-right 0.18s
const DIAG = process.env.DIAG === '1';

const results = [];
const check = (name, ok, detail = '') => {
  results.push({ name, ok: !!ok, detail: String(detail) });
  console.log((ok ? 'OK  ' : 'BAD ') + name + (detail ? '  | ' + detail : ''));
};

const browser = await chromium.launch({ channel: 'msedge', headless: true });
const page = await browser.newPage({ viewport: { width: 900, height: VH } });
await applyLanAuth(page);
const pageErrors = [];

// ★ 写请求闸门：一律假响应，绝不落到后端（config.json 零风险）
const writes = [];
let expectedWrites = 0;   // 仅 ⑤ 显式点"本地（Ollama）"项时允许出现（证明该项仍可点中）
await page.route('**/api/v1/settings**', async (route) => {
  const req = route.request();
  if (['POST', 'PUT', 'PATCH', 'DELETE'].includes(req.method())) {
    writes.push({ url: req.url(), body: req.postData() ?? '' });
    await route.fulfill({ status: 200, contentType: 'application/json', body: '{"ok":true}' });
    return;
  }
  await route.continue();
});
page.on('pageerror', (e) => pageErrors.push(String(e).slice(0, 120)));

// ── 工具 ────────────────────────────────────────────────────────────────
const layerState = () =>
  page.evaluate(() => {
    const vis = (el) => {
      const r = el.getBoundingClientRect();
      const s = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
    };
    const pops = [...document.querySelectorAll('.ctl-pop')].filter(vis);
    const usage = [...document.querySelectorAll('.usage-pop')].filter(vis);
    return {
      pops: pops.length,
      usage: usage.length,
      total: pops.length + usage.length,
      popFirst: pops.length ? (pops[0].querySelector('.ctl-item')?.textContent ?? '').slice(0, 10) : '',
    };
  });

// 采样【整块 badge】而不只是中心：审计证据里 500–540 档的形态是
// 「badge 上缘 8px 落在 .ctl-pop 内边距（无 handler）→ 点了没反应」——
// 只看中心会漏掉它（该档中心恰好还在弹层下方）。故取 5 点：
// 中心 / 上缘中点 / 下缘中点 / 左缘中点 / 右缘中点，每点都必须归属 badge 子树。
const badgeDiag = () =>
  page.evaluate(() => {
    const b = document.querySelector('.usage-badge');
    if (!b) return { missing: true };
    const r = b.getBoundingClientRect();
    const cx = Math.round(r.left + r.width / 2);
    const cy = Math.round(r.top + r.height / 2);
    const pts = {
      center: [cx, cy],
      top: [cx, Math.round(r.top) + 1],
      bottom: [cx, Math.round(r.bottom) - 1],
      left: [Math.round(r.left) + 1, cy],
      right: [Math.round(r.right) - 1, cy],
    };
    const d = (el) =>
      el
        ? el.tagName +
          (el.className && typeof el.className === 'string' ? '.' + el.className.trim().split(/\s+/).join('.') : '') +
          '「' + (el.textContent ?? '').trim().slice(0, 12) + '」'
        : 'null';
    const hits = {};
    const descs = {};
    for (const [k, [x, y]] of Object.entries(pts)) {
      const el = document.elementFromPoint(x, y);
      hits[k] = !!(el && el.closest('.usage-badge'));
      if (!hits[k]) descs[k] = `${k}(${x},${y})→${d(el)}`;
    }
    const el = document.elementFromPoint(cx, cy);
    const pop = document.querySelector('.ctl-pop');
    const pr = pop ? pop.getBoundingClientRect() : null;
    return {
      cx,
      cy,
      hitIsBadge: hits.center,
      hitAllBadge: Object.values(hits).every(Boolean),
      missDesc: Object.values(descs).join(' ; '),
      hit: d(el),
      badgeRect: { t: Math.round(r.top), b: Math.round(r.bottom), l: Math.round(r.left), r: Math.round(r.right) },
      popRect: pr ? { t: Math.round(pr.top), b: Math.round(pr.bottom), l: Math.round(pr.left), r: Math.round(pr.right) } : null,
      overlap: pr
        ? Math.max(0, Math.min(r.bottom, pr.bottom) - Math.max(r.top, pr.top)) > 0 &&
          Math.max(0, Math.min(r.right, pr.right) - Math.max(r.left, pr.left)) > 0
        : false,
    };
  });

// 注意①：侧栏首屏会先渲染 .taskitem.skeleton（占位骨架）——点它会超时，
//         必须等真实列表项（:not(.skeleton)）。
// 注意②：≤760 档侧栏变抽屉（移出视口）→ 窄档下 .taskitem 点不到。
//         故【先在 1440 宽进入任务页，再缩到目标档】——这也是真实用户动作。
const enterTask = async () => {
  await page.setViewportSize({ width: 1440, height: VH });
  await page.goto(BASE, { waitUntil: 'domcontentloaded' });
  const real = page.locator('.taskitem:not(.skeleton)').first();
  await real.waitFor({ state: 'visible', timeout: 15000 });
  await real.click();
  await page.waitForSelector('.tv-composer', { timeout: 10000 });
  await page.waitForSelector('.usage-badge', { timeout: 8000 });
  await page.waitForTimeout(SETTLE);
};

const closeAll = async () => {
  await page.keyboard.press('Escape');
  await page.waitForTimeout(220);
};

const clickBadgeCenter = async () => {
  const d = await badgeDiag();
  await page.mouse.click(d.cx, d.cy);       // ★ 真实鼠标坐标点击，无 force
  await page.waitForTimeout(280);
  return d;
};

try {
  await enterTask();

  // ═══ ① 逐档：弹层打开时 badge 中心归属 + 真实点击后果 ═══
  for (const w of WIDTHS) {
    await page.setViewportSize({ width: w, height: VH });
    await page.waitForTimeout(SETTLE);
    await page.locator('.tv-composer .ctl-model').click();      // 打开模型弹层（复现审计场景）
    await page.waitForFunction(() => !!document.querySelector('.tv-composer .ctl-pop'), { timeout: 4000 });
    await page.waitForTimeout(SETTLE);

    const d = await badgeDiag();
    if (DIAG) {
      console.log(
        `  [diag ${w}] badge=${JSON.stringify(d.badgeRect)} pop=${JSON.stringify(d.popRect)} ` +
          `重叠=${d.overlap} 中心=(${d.cx},${d.cy}) 命中=${d.hit}`
      );
    }
    check(`${w} 档：弹层打开时 badge 整块 5 点 elementFromPoint 全归 badge（含上缘）`, d.hitAllBadge,
      d.hitAllBadge ? `中心命中 ${d.hit}` : `漏点 ${d.missDesc}（中心命中 ${d.hit}）`);

    // Playwright actionability：被遮挡的 badge 会被拒（修前必红在这一条）
    let refused = '';
    try {
      await page.locator('.usage-badge').click({ timeout: 2500 });
    } catch (e) {
      refused = String(e).split('\n').find((l) => /intercept|not receive|timeout/i.test(l)) ?? String(e).slice(0, 100);
    }
    check(`${w} 档：Playwright 正常点击 .usage-badge 未被拒（无 force）`, !refused, refused || '未被拒');
    await closeAll();
    await page.waitForTimeout(120);

    // 重新开模型弹层，走【真实鼠标坐标】点击——还原用户实际动作
    await page.locator('.tv-composer .ctl-model').click();
    await page.waitForTimeout(320);
    const n0 = writes.length;
    const d2 = await clickBadgeCenter();
    const post = writes.slice(n0);
    const L = await layerState();
    check(`${w} 档：点 badge 中心 0 次模型写请求（config.json 零风险）`, post.length === 0,
      post.length ? `★发出了 ${post.length} 次：${post.map((x) => x.url.split('/api')[1] + ' ' + x.body).join(' ; ')}` : '0 次');
    check(`${w} 档：点 badge 中心 → 恰 1 层且是「用量」`, L.total === 1 && L.usage === 1, JSON.stringify(L));
    if (DIAG) console.log(`  [diag ${w}] 点击后 层=${JSON.stringify(L)} 命中=${d2.hit}`);
    await closeAll();
    await enterTask();                                          // ★ 每档复位 UI 状态（防跨档串味）
  }

  // ═══ ② 三方向 × 双向：恰 1 层 ═══
  await page.setViewportSize({ width: 600, height: VH });
  await page.waitForTimeout(SETTLE);
  const openModel = async () => { await page.locator('.tv-composer .ctl-model').click(); await page.waitForTimeout(300); };
  const openMode = async () => { await page.locator('.tv-composer .ctl-mode').click(); await page.waitForTimeout(300); };
  const openUsage = async () => { await clickBadgeCenter(); };
  const TRIG = { model: openModel, mode: openMode, usage: openUsage };
  const ORDER = [
    ['model', 'mode'], ['model', 'usage'], ['mode', 'model'],
    ['mode', 'usage'], ['usage', 'model'], ['usage', 'mode'],
  ];
  for (const [a, b] of ORDER) {
    await closeAll();
    const n0 = writes.length;
    await TRIG[a]();
    const La = await layerState();
    await TRIG[b]();
    const Lb = await layerState();
    const want = b === 'usage' ? Lb.usage === 1 : Lb.pops === 1;
    check(`互斥 ${a}→${b}：先开后恰 1 层、切换后仍恰 1 层且是 ${b}`,
      La.total === 1 && Lb.total === 1 && want,
      `先开=${JSON.stringify(La)} 切换后=${JSON.stringify(Lb)}`);
    check(`互斥 ${a}→${b}：全程 0 次模型写请求`, writes.length - n0 === 0,
      writes.length - n0 ? JSON.stringify(writes.slice(n0)) : '0 次');
  }
  await closeAll();

  // ═══ ④ 弹层【高度】敏感性：换视口高（弹层底缘与 badge 的相对位置随之变）═══
  // 这一维专治"固定位移"式假修：任何"把 badge 挪 N px 让开弹层"的改法在这里必红
  // （弹层高度 = 6 预设 + hint，与视口高无关，但 badge 是 fixed bottom:76 ⇒
  //  视口高每变 1px，两者相交量就变 1px）。
  for (const h of [540, 620, 700, 780, 900]) {
    await enterTask();
    await page.setViewportSize({ width: 600, height: h });
    await page.waitForTimeout(SETTLE);
    await page.locator('.tv-composer .ctl-model').click();
    await page.waitForTimeout(320);
    const n0 = writes.length;
    const d = await badgeDiag();
    await clickBadgeCenter();
    const L = await layerState();
    check(`600 宽 × 高 ${h}：badge 整块 5 点归自身 + 点击切到用量（不随弹层/badge 相对高度失效）`,
      d.hitAllBadge && L.total === 1 && L.usage === 1 && writes.length - n0 === 0,
      `badge=${JSON.stringify(d.badgeRect)} pop=${JSON.stringify(d.popRect)} 命中=${d.hit} 漏点=${d.missDesc} → ${JSON.stringify(L)}`);
    await closeAll();
  }

  // ═══ ⑤ 反向关切：抬上去的 badge 会不会反过来挡住弹层项？═══
  // 本修的代价是 badge 与 .ctl-pop 右下角【仍几何交叠】（只改了层级）。故必须证明：
  //   ① 没有任何 .ctl-item 的【命中中心】被 badge 盖住（Playwright 非 force 点击按中心命中）
  //   ② 弹层里的「本地（Ollama）」仍点得中 → 恰 1 次模型写（被拦截计数，不落盘）
  for (const w of [560, 600, 660]) {
    await enterTask();
    await page.setViewportSize({ width: w, height: VH });
    await page.waitForTimeout(SETTLE);
    await page.locator('.tv-composer .ctl-model').click();
    await page.waitForTimeout(320);
    const cover = await page.evaluate(() => {
      const b = document.querySelector('.usage-badge').getBoundingClientRect();
      const items = [...document.querySelectorAll('.tv-composer .ctl-pop .ctl-item')];
      const covered = items
        .filter((el) => {
          const r = el.getBoundingClientRect();
          const cx = r.left + r.width / 2;
          const cy = r.top + r.height / 2;
          return cx >= b.left && cx <= b.right && cy >= b.top && cy <= b.bottom;
        })
        .map((el) => (el.textContent ?? '').slice(0, 8));
      return { n: items.length, covered };
    });
    check(`${w} 档：badge 未盖住任何弹层项的命中中心（共 ${cover.n} 项）`,
      cover.covered.length === 0, '被盖中心=' + JSON.stringify(cover.covered));
    const n0 = writes.length;
    let err = '';
    try {
      await page.locator('.tv-composer .ctl-pop .ctl-item', { hasText: '本地（Ollama）' }).click({ timeout: 3000 });
    } catch (e) {
      err = String(e).split('\n')[0].slice(0, 90);
    }
    const post = writes.slice(n0);
    expectedWrites += post.length;   // 这一次是【故意】点模型项：允许计数
    check(`${w} 档：弹层项「本地（Ollama）」仍可点中（非 force，写请求被拦截）`,
      !err && post.length === 1 && post[0].body.includes('qwen3.5:9b'),
      err || `${post.length} 次 ${post[0] ? post[0].body.slice(0, 60) : ''}`);
    await page.waitForTimeout(200);
  }

  // ═══ ⑥ 审计证据里的另两档：500–540「badge 上缘 8px 落在 .ctl-pop 内边距
  //        （无 handler）→ 点了没反应」、320/360「权限弹层也盖住 badge 中点」═══
  for (const [w, trig] of [[500, 'model'], [520, 'model'], [540, 'model'], [320, 'mode'], [360, 'mode']]) {
    await enterTask();
    await page.setViewportSize({ width: w, height: VH });
    await page.waitForTimeout(SETTLE);
    await page.locator(`.tv-composer .ctl-${trig}`).click();
    await page.waitForTimeout(320);
    const n0 = writes.length;
    const d = await badgeDiag();
    await clickBadgeCenter();
    const L = await layerState();
    check(`${w} 档（先开${trig === 'model' ? '模型' : '权限'}弹层）：badge 整块 5 点归自身 + 点击切到用量`,
      d.hitAllBadge && L.total === 1 && L.usage === 1 && writes.length - n0 === 0,
      `badge=${JSON.stringify(d.badgeRect)} pop=${JSON.stringify(d.popRect)} 命中=${d.hit} 漏点=${d.missDesc} → ${JSON.stringify(L)}`);
    await closeAll();
  }

  // ═══ ③ 关闭态：badge 可点开用量（入口没丢）═══
  const Lclosed = await layerState();
  const d3 = await clickBadgeCenter();
  const Lopen = await layerState();
  check('无弹层时：badge 整块 5 点归自身且点击可展开用量', d3.hitAllBadge && Lclosed.total === 0 && Lopen.usage === 1,
    `点前=${JSON.stringify(Lclosed)} 点后=${JSON.stringify(Lopen)}`);
  await closeAll();
} catch (e) {
  check('脚本自身崩溃（不允许）', false, String(e).slice(0, 200));
}

check('pageerror = 0', pageErrors.length === 0, pageErrors.join('; '));
check('★ badge 点击路径 0 次模型写（全脚本写请求 = 仅 ⑤ 故意点项的那几次，且全被拦截）',
  writes.length === expectedWrites,
  `总写=${writes.length} 期望=${expectedWrites}` + (writes.length === expectedWrites ? '（全部被 page.route 拦截，未落盘）' : ' ★出现意外写入：' + JSON.stringify(writes)));
await browser.close();

const bad = results.filter((r) => !r.ok);
console.log('\n结果：' + (results.length - bad.length) + '/' + results.length + ' 通过');
if (bad.length) console.log('红例：\n' + bad.map((r) => '  - ' + r.name + '  | ' + r.detail).join('\n'));
process.exit(bad.length ? 1 : 0);
