// ★ 真·端到端：用【手机实际会打开的那个地址】验证「扫码进去就能用」
//
// 与 verify_lan_qr 的分工：
//   verify_lan_qr      验"二维码里编的东西对不对"（编→解码闭环）
//   本脚本           验"打开那个地址之后，到底能不能用"（真局域网 + 真密码闸门）
//
// 做法：直接访问 http://<本机局域网IP>:8642/?token=<真实访问密码> —— 这就是手机扫码
// 之后落地的那条路（后端绑 0.0.0.0 + 全路径密码闸门 + 前端从 URL 收密码）。
// 断言：① 不弹密码框 ② 任务列表真的加载出来（说明 API 调用带着密码成功了）
//       ③ 地址栏里的密码被抹掉 ④ 无页面错误
//
// 用法：cd frontend && node scripts/verify_lan_real.mjs
import { chromium } from 'playwright-core';
import { readFileSync } from 'node:fs';

const cfg = JSON.parse(readFileSync(new URL('../../config.json', import.meta.url), 'utf8'));
const HOST = cfg?.server?.host ?? '';
const PORT = cfg?.server?.port ?? 8642;
const TOKEN = cfg?.server?.access_token ?? '';

const mask = (s) => (s ? `${String(s).slice(0, 4)}…（${String(s).length} 位）` : '(空)');
const results = [];
const check = (name, ok, detail = '') => {
  results.push({ name, ok: !!ok });
  console.log((ok ? 'OK  ' : 'BAD ') + name + (detail ? '  | ' + detail : ''));
};

if (HOST !== '0.0.0.0' || !TOKEN) {
  console.log(`SKIP：当前没开手机直连（host=${HOST || '(空)'}, token=${mask(TOKEN)}）`);
  console.log('     先在设置→常规→手机直连 点「一键开通」并重启后端，再跑本脚本。');
  process.exit(0);
}

// 取本机局域网 IP（与后端 /lan/status 报的同一个）
const ip = await (async () => {
  try {
    const r = await fetch(`http://127.0.0.1:${PORT}/api/v1/lan/status?token=${encodeURIComponent(TOKEN)}`);
    return (await r.json()).ip;
  } catch { return '127.0.0.1'; }
})();
const url = `http://${ip}:${PORT}/?token=${encodeURIComponent(TOKEN)}`;
console.log(`目标地址：http://${ip}:${PORT}/?token=${mask(TOKEN)}   （密码已打码，不打印明文）\n`);

const browser = await chromium.launch({ channel: 'msedge', headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const errors = [];
page.on('pageerror', (e) => errors.push(String(e).slice(0, 160)));

await page.goto(url, { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(2500);   // 让首屏 API 调用跑完

check('① 页面真的打开了（不是空白/错误页）',
  (await page.locator('.app').count()) > 0, `body 长度 ${(await page.content()).length}`);
check('② ★ 没弹密码框（扫码不用手打 32 位密码）',
  (await page.locator('.token-mask').count()) === 0,
  `token-mask 出现 ${await page.locator('.token-mask').count()} 次`);
check('③ ★ 任务列表加载出来了（说明 API 调用带着密码成功了）',
  (await page.locator('.taskitem, .hero, .tv-composer').count()) > 0,
  `任务项 ${await page.locator('.taskitem').count()} 个`);

const stored = await page.evaluate(() => localStorage.getItem('authToken'));
check('④ 密码已存进本机（后续调用自动带上）', stored === TOKEN, mask(stored));
check('⑤ 地址栏里的密码已被抹掉', !page.url().includes('token='), page.url());
check('⑥ 无页面错误', errors.length === 0, errors.join(' | '));

const bad = results.filter((r) => !r.ok).length;
console.log(`\n结果：${results.length - bad}/${results.length} 通过`);
await browser.close();
process.exit(bad === 0 ? 0 : 1);
