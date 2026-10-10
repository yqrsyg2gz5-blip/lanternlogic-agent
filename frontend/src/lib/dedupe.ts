/**
 * ★ 应用层去重：**回复如果就是把上面工具结果又抄了一遍，就别再占一屏**。
 *
 * 用户的诉求（原话）："底下这个命令输出原样如下…他就是长篇大论…是不是重复"。
 * 实测那个任务（task_20261004_98f9c932）同一句 61 字出现了 4 次：
 *   #7 工具输出 → #12 模型"原样贴回来" → #16 task_done 参数 → #18 交付语
 * 其中 #16/#18 是**应用层自己**造成的重复（同一句话既在卡片摘要里、又当最终回复发一遍），
 * 这个模块解决的就是这一类：**文本基本相同就折叠**，点开仍能看到全文（不丢数据）。
 *
 * 判定规则（保守，宁可漏判也不误收）：
 *   · 先把文本"归一"：去代码围栏、去空白、去标点 —— 只比较实质内容
 *   · 短文本（< 40 字）不参与判定：短句撞车太正常（"好的""完成了"）
 *   · 两条都够长时，若一条的 ≥85% 内容被另一条包含 ⇒ 判为重复
 */
const FENCE = /```[\s\S]*?```/g;
/** 引出语：**没有信息量**的那几句（"命令输出原样如下："之类）。
 *  不剥掉它们，一条"引子 + 原样贴结果"的回复覆盖率会掉到 ~0.5，真正的重复反而判不上
 *  （2026-10-05 实测：task_20261004_1cf61eaa 那条 115 字回复，工具输出只占 53%）。 */
const LEADIN = /(?:命令|执行|运行)?(?:输出|结果)?(?:原样)?(?:如下|如上|贴回|贴一遍|粘贴|附上)?[：:]\s*/g;

export function normalize(text: string): string {
  return text
    .replace(FENCE, (m) => m.replace(/```[a-zA-Z]*\n?/g, ''))   // 保留围栏里的内容
    .replace(/```/g, '')
    .replace(LEADIN, '')                    // 剥掉没有信息量的引出语
    .replace(/[^\S\u0000]+/g, '')           // 去空白，但**保留哨兵**（\u0000 不是空白）
    .replace(/[，。！？、；：""''（）【】《》,.!?;:'"()[\]<>·—…-]/g, '')
    .toLowerCase();
}

/** a 有多大比例被 b 覆盖（0~1）。
 *
 *  做法：把 a 按 16 字播种，在 b 里找种子，命中就向两边贪心扩展成"最长游程"，
 *  再把重叠的游程并起来，返回覆盖到的总字数。
 *
 *  ★ 为什么不是"12 字滑窗逐字符覆盖"（本班两次踩坑后的最终结论）：
 *    ① 重叠滑窗会把**接缝盖过去** ⇒ 跨条目的拼接被算成覆盖率 1.00（误收）
 *    ② 不重叠分块又**对偏移过敏** ⇒ 回复前面多一句"命令输出原样如下："就把所有块错开，
 *       真正的重复反而漏判（实测掉到 0.57）
 *    "最长游程"两头都占：偏移多少都行（种子落在哪就从哪扩），接缝则**必然打断**游程。
 */
export function matchedChars(a: string, b: string): number {
  const na = normalize(a);
  const nb = normalize(b);
  if (!na || !nb) return 0;
  if (nb.includes(na)) return na.length;
  const SEED = 16;
  if (na.length < SEED) return nb.includes(na) ? na.length : 0;
  const runs: Array<[number, number]> = [];          // a 上的 [起, 止)
  for (let i = 0; i + SEED <= na.length; i += SEED) {
    const seed = na.slice(i, i + SEED);
    let at = nb.indexOf(seed);
    while (at >= 0) {
      let s = i, e = i + SEED, sa = at, sb = at + SEED;
      while (s > 0 && sa > 0 && na[s - 1] === nb[sa - 1]) { s -= 1; sa -= 1; }
      while (e < na.length && sb < nb.length && na[e] === nb[sb]) { e += 1; sb += 1; }
      runs.push([s, e]);
      at = nb.indexOf(seed, at + 1);
    }
  }
  if (!runs.length) return 0;
  runs.sort((x, y) => x[0] - y[0]);
  let total = 0;
  let [cs, ce] = runs[0];
  for (const [s, e] of runs.slice(1)) {
    if (s <= ce) { ce = Math.max(ce, e); continue; }
    total += ce - cs; [cs, ce] = [s, e];
  }
  total += ce - cs;
  return total;
}

/** 覆盖率 = 连续匹配字数 / 归一后长度。 */
export function containment(a: string, b: string): number {
  const n = normalize(a).length;
  return n ? matchedChars(a, b) / n : 0;
}

export const DUP_MIN_LEN = 40;
/** 高覆盖：几乎整条都是之前出现过的 */
export const DUP_RATIO = 0.85;
/** 低覆盖 + 新增内容极少：也算重复（应对"前面加一句引子、再把结果整段贴一遍"） */
export const DUP_RATIO_LOW = 0.6;
export const DUP_UNMATCHED_MAX = 30;

/** 这段文本是不是"上面已经说过了"？candidates = 该任务里在它之前出现过的内容（工具结果/助手回复）。 */
export function isDuplicateOfEarlier(text: string, candidates: string[]): boolean {
  const n = normalize(text);
  if (n.length < DUP_MIN_LEN) return false;
  for (const c of candidates) {
    const matched = matchedChars(text, c);
    const cover = matched / n.length;
    if (cover >= DUP_RATIO) return true;
    // 大部分是旧内容、且这条回复没带来多少新字 ⇒ 也判重复
    if (cover >= DUP_RATIO_LOW && n.length - matched <= DUP_UNMATCHED_MAX) return true;
  }
  return false;
}

/** 把"之前说过的所有内容"拼成一个语料库，供匹配用。
 *
 *  ★ 为什么必须**拼接**而不是逐条比（2026-10-05 用户实测的第二类重复）：
 *    有一条回复是"把上面两条回复拼在一起"（"你好！我是…" + "我是 LanternLogic Agent 背后的…"），
 *    对**任何单独一条**它的覆盖率都只有 ~50% ⇒ 逐条比会全部漏判；
 *    拼成一份语料后，两半各自都能在语料里找到 ⇒ 覆盖率接近 100% ✓
 *  ★ 为什么要含**助手自己的历史回复**：那条拼接的原料就是之前两条回复，只拿工具结果当语料同样漏判。
 *  ★ 上限 20k 字符：超长任务里别让每次比对都扫一遍全部历史。
 *  ★ 条目之间用**哨兵字符**分隔（不是 \n）：归一化会删掉空白，用 \n 的话
 *    "A 条结尾 + B 条开头"会拼出一段两条里都没有的文字，产生跨条目假匹配
 *    （实测把"详细自我介绍"这种第一次出现的正经回答算成重复，差点收掉用户真想看的内容）。
 */
export const CORPUS_SEP = '\u0000';

export function buildCorpus(parts: string[], limit = 20000): string {
  const joined = parts.map((p) => (p ?? '').trim()).filter(Boolean).join(CORPUS_SEP);
  return joined.length > limit ? joined.slice(-limit) : joined;
}
