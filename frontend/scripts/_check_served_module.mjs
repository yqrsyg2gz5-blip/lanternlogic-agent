// 一次性小工具：确认 dev server（Vite）到底在给哪份 TeamView 模块。
// 用途：本会话遇到过两次"源码改好了、浏览器跑的是旧模块"，这时先看这里再决定要不要重启 dev server。
// 用法：cd frontend && node scripts/_check_served_module.mjs
const target = process.argv[2] || '/src/components/TeamView.tsx';
const needles = (process.argv[3] || 'value="relay",接力').split(',');

const r = await fetch(`http://127.0.0.1:5173${target}`);
const t = await r.text();
console.log(`HTTP ${r.status}  模块 ${target}  长度 ${t.length}`);
for (const n of needles) console.log(`  含 ${JSON.stringify(n)}：${t.includes(n)}`);
const i = t.indexOf('option value=');
if (i >= 0) console.log('  片段：' + t.slice(i, i + 300).replace(/\n/g, ' '));
