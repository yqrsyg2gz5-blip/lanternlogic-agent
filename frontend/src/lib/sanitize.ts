/**
 * ★ 清洗"模型把工具调用当文本吐出来"的脏内容（2026-10-05 用户任务里抓到的真样本）。
 *
 * 真实现象（task_20261005_25f7c5d9 事件 #13–#16）：
 *   模型把一次工具调用整段塞进了**正文**，于是界面上直接把这段 XML 当成"助手说的话"显示：
 *     <tool_call><function=update_plan><parameter=steps>[{"text": "梳理自我介绍要点（…
 *     要点已梳理完成，准备输出介绍</parameter></function></tool_call>
 *   紧接着真正的那次工具调用还因为"缺少必填参数 steps"失败了 —— 观感就是"乱套"。
 *
 * 处理原则：
 *   · 这类标记**不是给人看的内容**，一律剥掉；剥完还剩正文就照常显示
 *   · 剥完什么都不剩 ⇒ 这条消息不该显示（返回空串，调用方直接不渲染）
 *   · 只认"整块"标记，不做激进替换（免得把用户真想看的代码/文档里的尖括号也吃掉）
 */
const BLOCK_RES = [
  /<tool_call>[\s\S]*?<\/tool_call>/gi,          // 标准块
  /<tool_calls>[\s\S]*?<\/tool_calls>/gi,        // 复数变体
  /<function=[\s\S]*?<\/function>/gi,            // 只有 function 包裹
  /<tool_call>[\s\S]*$/i,                        // 未闭合（流式截断时常见）：吃到结尾
];

/** 剥掉内联工具调用标记；返回**给人看的正文**（可能是空串 = 这条不该显示）。 */
export function stripInlineToolCalls(text: string): string {
  if (!text) return '';
  let out = text;
  for (const re of BLOCK_RES) out = out.replace(re, '');
  // 只残留参数尾巴（模型把 </parameter></function> 也吃掉了的情况）
  out = out.replace(/<\/?(?:parameter|function|tool_call|tool_calls)[^>]*>/gi, '');
  return out.replace(/[ \t]+\n/g, '\n').replace(/\n{3,}/g, '\n\n').trim();
}

/** 这段内容是不是"整条都是工具调用标记"（剥完就空）？ */
export function isToolMarkupOnly(text: string): boolean {
  return !!text && !!text.trim() && stripInlineToolCalls(text) === '';
}
