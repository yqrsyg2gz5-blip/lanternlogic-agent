// 去重规则的**单元测试**（真跑 TS：用 esbuild 现场编译 src/lib/dedupe.ts，再断言）
//
// 为什么要有它：这条规则靠"浏览器点开某个真任务"是测不全的 ——
//   ① 用户实测的那类"拼接型重复"在我手头的任务里没找到样本（数据不在手上）
//   ② 规则一旦被改松/改紧，浏览器脚本未必能立刻发现（要看具体任务有没有那种重复）
// 所以这里用**构造的样本**把正反两面钉死：该收的必须收、不该收的必须不收。
//
// 用法：cd frontend && node scripts/test_dedupe.mjs
import { build } from 'esbuild';
import path from 'node:path';
import os from 'node:os';
import { writeFileSync, mkdtempSync } from 'node:fs';
import { pathToFileURL } from 'node:url';

const tmp = mkdtempSync(path.join(os.tmpdir(), 'dedupe-test-'));
const out = path.join(tmp, 'dedupe.mjs');
await build({
  entryPoints: [path.resolve('src/lib/dedupe.ts')],
  bundle: true, format: 'esm', outfile: out, logLevel: 'silent',
});
const { isDuplicateOfEarlier, buildCorpus, containment } = await import(pathToFileURL(out).href);

const results = [];
const check = (name, ok, detail = '') => {
  results.push({ name, ok: !!ok });
  console.log((ok ? 'OK  ' : 'BAD ') + name + (detail ? '  | ' + detail : ''));
};

// ── 真实样本 1：A3 探针那条"命令输出原样如下：```A3-LIVE-PROBE…```" ──
const obsA3 = 'A3-LIVE-PROBE（本机执行：沙箱已关闭——命令直接在你的真实电脑上运行，对文件与系统的改动已真实生效、不可撤销）';
const msgEcho = '命令输出原样如下：\n\n```\n' + obsA3 + '\n```';
check('① 工具输出的转述（前面多一句"命令输出原样如下："）应判为重复',
  isDuplicateOfEarlier(msgEcho, [buildCorpus([obsA3])]),
  `覆盖率 ${containment(msgEcho, obsA3).toFixed(2)}`);

// ── 真实样本 2（用户 2026-10-05 圈出来的那类）：把上面两条回复拼在一起 ──
const reply1 = '你好！我是执行智能体，可以帮你写代码、做调研、生成图片视频、整理文件、制作网页和报告等。有什么需要我做的吗？';
const reply2 = '我是 LanternLogic Agent 背后的执行智能体，一个在沙箱容器里干活的自主 AI：能写代码、跑命令、联网调研、生成图片视频、做网页报告等，产物全部落在工作区供你下载。我按编号计划推进任务、交付前真实自测，并如实说明验证边界。';
const merged = reply1 + reply2;
check('② 逐条比时的覆盖率只有一半（这正是它当初漏判的原因）',
  containment(merged, reply1) < 0.7 && containment(merged, reply2) < 0.7,
  `对第一条 ${containment(merged, reply1).toFixed(2)} / 对第二条 ${containment(merged, reply2).toFixed(2)}`);
check('③ 拼成语料后必须判为重复（本次修复的核心）',
  isDuplicateOfEarlier(merged, [buildCorpus([reply1, reply2])]),
  `覆盖率 ${containment(merged, buildCorpus([reply1, reply2])).toFixed(2)}`);

// ── 反向：不该收的必须不收 ──
const fresh = '我看了下你要的那个目录，里面一共 128 张图，按扩展名分成了 png/jpg/webp 三类，重复的挑出 3 组，清单已经写到 workspace/图片清单.md，你可以直接打开看。';
check('④ 真正的新内容不许判为重复',
  !isDuplicateOfEarlier(fresh, [buildCorpus([reply1, reply2, obsA3])]));
check('⑤ 短文本不参与判定（"好的""完成了"这种撞车太正常）',
  !isDuplicateOfEarlier('完成了，你看下。', [buildCorpus(['完成了，你看下。'])]));
check('⑥ 只是提到同一批关键词、内容不同 ⇒ 不判重复',
  !isDuplicateOfEarlier('我把那 128 张图按大小重排了一遍，最大的 12 张单独放到 workspace/大图/ 里了。',
                        [buildCorpus([fresh])]));
check('⑦ 完全相同的长回复 ⇒ 判重复',
  isDuplicateOfEarlier(fresh, [buildCorpus([fresh])]));

// ── 真实样本 3：**交付通道**（2026-10-05 自查抓到的系统性误收）──
// 交付时应用会把 `task_done(message=答案)` 发成正式回复 ⇒ 答案文本**必然等于**那条工具参数。
// 所以语料里绝对不能含交付语，否则"每一条正常回答"都会被判重复。
const answer = '详细自我介绍如下：\n\n**一、身份**\n我是 LanternLogic Agent 背后的执行智能体——一个在你本机沙箱容器里干活的自主 AI，能写代码、跑命令、联网调研、生成图文视频，并把可验证的产物交付到工作区。\n\n**二、纪律**\n交付前真实自测，并如实说明验证边界。';
check('⑩ 答案文本 == task_done 交付语时，若语料含交付语 ⇒ 会被误收（这就是当初的系统性误判）',
  isDuplicateOfEarlier(answer, [buildCorpus([answer])]),
  `覆盖率 ${containment(answer, answer).toFixed(2)}`);
check('⑪ 语料**不含**交付语（只有工具结果+历史回复）时，正常答案不许被收',
  !isDuplicateOfEarlier(answer, [buildCorpus([obsA3, reply1, reply2])]));

// ── 真实样本 4：答案确实抄了**工具结果** ⇒ 必须收 ──
// 与真样本 #12 同形（前面只多一句短前缀）；包装文字一多，覆盖率自然掉下来 ⇒ 不收，这是对的
const copied = '命令结果：' + obsA3;
check('⑫ 答案里抄了工具结果（只多一句短前缀）⇒ 收（这一类是真重复）',
  isDuplicateOfEarlier(copied, [buildCorpus([obsA3])]),
  `覆盖率 ${containment(copied, obsA3).toFixed(2)}`);
check('⑫b 但如果答案里大半是自己的话、只引了一小段结果 ⇒ 不收',
  !isDuplicateOfEarlier('命令结果如下：' + obsA3 + '\n\n另外我补充三点观察，第一点关于目录结构，第二点关于命名，第三点关于后续计划。',
                        [buildCorpus([obsA3])]),
  `覆盖率 ${containment('命令结果如下：' + obsA3 + '\n\n另外我补充三点观察，第一点关于目录结构，第二点关于命名，第三点关于后续计划。', obsA3).toFixed(2)}`);

// ── 边界：引用旧内容 + 中间夹新内容 ──
const bigNew = '另外我补充一段全新的说明：这次我还顺手检查了目录权限、命名规范、以及三个边缘情况，结论都写在下面这份清单里，和上面那些内容没有重复。';
check('⑬ 引用两段旧话但中间夹了 60+ 字新内容 ⇒ 不收（新增够多就别收）',
  !isDuplicateOfEarlier(reply1.slice(0, 30) + bigNew + reply2.slice(-30), [buildCorpus([reply1.slice(0, 30), reply2.slice(-30)])]));
check('⑬b 夹的新内容只有十来个字 ⇒ 收（基本没带来新信息，这条留不太值）',
  isDuplicateOfEarlier(reply1.slice(0, 30) + '补充一句：已核对。' + reply2.slice(-30), [buildCorpus([reply1.slice(0, 30), reply2.slice(-30)])]));

// ── 引出语：没有信息量的那几句必须先剥掉（2026-10-05 实测掉到 0.53 的那条）──
const real115 = '命令输出原样如下：\n\n```\n' + obsA3 + '\n```\n\n（以上为命令的原始输出）';
check('⑭ 带引出语 + 围栏的"原样贴结果"⇒ 仍判重复（剥掉引出语后覆盖率应 ≥ 0.85）',
  isDuplicateOfEarlier(real115, [buildCorpus([obsA3])]),
  `覆盖率 ${containment(real115, obsA3).toFixed(2)}`);

// ── 语料上限：超长历史不许把比对拖垮 ──
const huge = Array.from({ length: 400 }, (_, i) => `第 ${i} 段历史内容，用来把语料撑大。`).join('\n');
const capped = buildCorpus([huge]);
check('⑧ 语料有上限（超长历史截断）', capped.length <= 20000, `语料长度 ${capped.length}`);
const t0 = Date.now();
isDuplicateOfEarlier(fresh, [capped]);
check('⑨ 20k 语料上的判定够快（< 150ms）', Date.now() - t0 < 150, `${Date.now() - t0}ms`);

const bad = results.filter((r) => !r.ok).length;
console.log(`\n结果：${results.length - bad}/${results.length} 通过`);
process.exit(bad === 0 ? 0 : 1);
