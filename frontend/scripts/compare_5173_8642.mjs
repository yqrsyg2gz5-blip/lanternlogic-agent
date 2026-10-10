// 对比 5173（Vite dev server）与 8642（后端托管的构建产物）到底差在哪。
// 用法：cd frontend && node scripts/compare_5173_8642.mjs
const P = { '5173 (dev)': 'http://127.0.0.1:5173', '8642 (你的应用)': 'http://127.0.0.1:8642' };
const MARKERS = [
  ['模式下拉含 relay（接力）', null],          // 用打包/源码里的串判断
];

const rows = [];
for (const [label, base] of Object.entries(P)) {
  let status = '连不上';
  let entry = '';
  let css = '';
  let js = '';
  try {
    const r = await fetch(base, { signal: AbortSignal.timeout(6000) });
    status = 'HTTP ' + r.status;
    const html = await r.text();
    // dev 的入口是 /src/main.tsx；构建产物是 /assets/index-xxx.js
    js = (html.match(/src="([^"]+\.(js|tsx))"/) || [])[1] || '';
    css = (html.match(/href="([^"]+\.css)"/) || [])[1] || '';
    const mod = js.includes('/src/') ? '源码模块（实时编译）' : '打包产物（要 npm run build）';
    entry = `${mod}：${js}`;
  } catch (e) {
    entry = String(e).slice(0, 60);
  }
  rows.push({ label, base, status, entry, css, js });
}

console.log('=== 两个入口 ===');
for (const r of rows) {
  console.log(`\n${r.label}  ${r.base}`);
  console.log(`  状态：${r.status}`);
  console.log(`  入口：${r.entry}`);
  console.log(`  样式：${r.css || '（无）'}`);
}

// 用 API 侧确认两边打的是同一份数据（同一个后端）
const tok = process.env.DSH_LAN_TOKEN || '';
const H = { 'X-Auth-Token': tok };
for (const [label, base] of Object.entries(P)) {
  try {
    const j = await (await fetch(`${base}/api/v1/tasks`, { headers: H, signal: AbortSignal.timeout(6000) })).json();
    console.log(`\n${label} 的 /api/v1/tasks：${Array.isArray(j) ? j.length + ' 个任务' : JSON.stringify(j).slice(0, 80)}`);
  } catch (e) {
    console.log(`\n${label} 的 /api/v1/tasks：失败 ${String(e).slice(0, 60)}`);
  }
}
