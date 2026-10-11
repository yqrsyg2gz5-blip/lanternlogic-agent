/**
 * 极小的语法高亮（★ 2026-10-06）。
 *
 * 为什么不用现成库：用户对体积敏感（highlight.js ~100KB+ ✗）。这里用**几十行**覆盖
 * 最常见的几类 token（注释 / 字符串 / 数字 / 关键字 / 函数名），够"一眼看出结构" ✓，
 * 复杂语言特性（模板字符串嵌套、正则字面量…）**故意不做** ✗ —— 高亮错了比不高亮更难看。
 *
 * 纯 JS（带 JSDoc 类型）+ 无依赖 ⇒ 后端测试可以直接用 node 跑它做**行为验证** ✓
 * （见 backend/tests/test_syntax_highlight.py）。
 */

const KEYWORDS = {
  python: new Set(('def class return if elif else for while try except finally with as import from ' +
    'pass break continue lambda yield global nonlocal assert raise del in is not and or None True False ' +
    'async await').split(' ')),
  bash: new Set(('if then else elif fi for while do done case esac function return export local ' +
    'echo cd mkdir rm cp mv cat grep sed awk curl git npm npx node python pytest powershell').split(' ')),
  json: new Set('true false null'.split(' ')),
  js: new Set(('const let var function return if else for while try catch finally import from export ' +
    'default class new await async typeof instanceof null undefined true false this of in delete ' +
    'interface type enum extends implements public private readonly').split(' ')),
};
KEYWORDS.javascript = KEYWORDS.js;
KEYWORDS.ts = KEYWORDS.js;
KEYWORDS.typescript = KEYWORDS.js;
KEYWORDS.tsx = KEYWORDS.js;
KEYWORDS.jsx = KEYWORDS.js;
KEYWORDS.sh = KEYWORDS.bash;
KEYWORDS.shell = KEYWORDS.bash;
KEYWORDS.powershell = KEYWORDS.bash;
KEYWORDS.py = KEYWORDS.python;

/** 注释起始符（按语言）。 */
function commentStart(lang) {
  if (lang === 'python' || lang === 'py') return '#';
  if (lang === 'bash' || lang === 'sh' || lang === 'shell' || lang === 'powershell') return '#';
  return '//';
}

/**
 * 把一段代码切成 token。
 * @param {string} code
 * @param {string} lang 语言（不认识的语言 ⇒ 原样返回一个 token，不高亮）
 * @returns {{t: string, k: string}[]} k ∈ '' | 'com' | 'str' | 'num' | 'kw' | 'fn'
 */
export function tokenize(code, lang) {
  const l = String(lang || '').toLowerCase();
  const kw = KEYWORDS[l];
  const cstart = commentStart(l);
  if (!kw) return [{ t: String(code), k: '' }];

  const out = [];
  const src = String(code);
  let i = 0;
  const push = (t, k) => { if (t) out.push({ t, k: k || '' }); };

  while (i < src.length) {
    const rest = src.slice(i);
    // 注释（到行尾）
    if (rest.startsWith(cstart) && !(rest.startsWith('#') && cstart !== '#')) {
      const nl = src.indexOf('\n', i);
      const end = nl === -1 ? src.length : nl;
      push(src.slice(i, end), 'com');
      i = end;
      continue;
    }
    // 字符串：单/双引号（不处理转义嵌套，够用 ✓）
    const ch = src[i];
    if (ch === '"' || ch === "'" || ch === '`') {
      let j = i + 1;
      while (j < src.length && src[j] !== ch) {
        if (src[j] === '\\') j++;
        j++;
      }
      j = Math.min(j + 1, src.length);
      push(src.slice(i, j), 'str');
      i = j;
      continue;
    }
    // 数字
    if (/[0-9]/.test(ch)) {
      let j = i;
      while (j < src.length && /[0-9a-fA-FxX._]/.test(src[j])) j++;
      push(src.slice(i, j), 'num');
      i = j;
      continue;
    }
    // 标识符 / 关键字 / 函数名
    if (/[A-Za-z_$@]/.test(ch)) {
      let j = i;
      while (j < src.length && /[A-Za-z0-9_$.-]/.test(src[j])) j++;
      const word = src.slice(i, j);
      const after = src.slice(j).match(/^\s*\(/);
      const bare = word.replace(/^[@$]/, '');
      push(word, kw.has(bare) ? 'kw' : (after ? 'fn' : ''));
      i = j;
      continue;
    }
    push(ch, '');
    i++;
  }
  return out;
}

export default tokenize;
