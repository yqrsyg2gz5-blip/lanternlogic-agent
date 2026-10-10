// 共享助手：局域网直连已开启时，给 Playwright 页面注入访问密码。
//
// 为什么需要：开了手机直连之后，后端的密码闸门对 **所有 /api/*** 生效（安全上必须如此 ——
// Host 头是攻击者可控的，按 Host 判"是不是本机"等于白送后门）。于是这些验证脚本走
// vite 代理（5173 → 8642）时也会 401，页面弹出密码浮层，脚本全部超时失效。
//
// 做法：读 config.json，**只有** host=0.0.0.0 且有 access_token 时才注入；
// 没开局域网时返回 null、什么都不做（日常开发零影响）。
//
// 用法（每个脚本里 newPage 之后加一行）：
//     await applyLanAuth(page);
import { readFileSync } from 'node:fs';

export function lanToken() {
  try {
    const cfg = JSON.parse(readFileSync(new URL('../../config.json', import.meta.url), 'utf8'));
    if (cfg?.server?.host === '0.0.0.0' && cfg?.server?.access_token) {
      return String(cfg.server.access_token);
    }
  } catch {
    /* 读不到就当作"没开局域网" */
  }
  return null;
}

export async function applyLanAuth(page) {
  const t = lanToken();
  if (!t) return null;
  await page.addInitScript((tok) => {
    try { localStorage.setItem('authToken', tok); } catch { /* 隐私模式等 */ }
  }, t);
  return t;
}
