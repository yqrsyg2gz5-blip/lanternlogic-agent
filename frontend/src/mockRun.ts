/**
 * 剧本式 Mock 事件流 —— 模拟真实 agent loop 的节奏（Phase 1 的"假 Agent"）。
 * 完整走一遍：status → plan → message → knowledge → action/observation 交替 →
 * plan 更新（带 reflection）→ 最终 message（带附件）→ idle。
 * 行为规范：一次迭代一个工具、计划可更新等。
 */
import type { EventType, PlanStep } from './types';

export interface ScriptedRunOptions {
  taskId: string;
  input: string;
  followUp: boolean;
  emit: (type: EventType, payload: unknown) => void;
  isCancelled: () => boolean;
  onDone: (status: 'done' | 'cancelled' | 'failed') => void;
}

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));
const clip = (s: string, n: number) => (s.length > n ? s.slice(0, n) + '…' : s);

export function startScriptedRun(o: ScriptedRunOptions): () => void {
  void (async () => {
    const { emit, input } = o;
    try {
      emit('status', { state: 'running', detail: o.followUp ? '继续处理追问' : '任务开始' });

      const steps: PlanStep[] = [
        { no: 1, text: '理解需求并收集相关资料', status: 'in_progress' },
        { no: 2, text: `围绕「${clip(input, 24)}」起草产物`, status: 'pending' },
        { no: 3, text: '本地校验与修正', status: 'pending' },
        { no: 4, text: '汇总交付', status: 'pending' },
      ];
      emit('plan', { steps, current_step: 1 });
      await sleep(600);
      if (o.isCancelled()) return o.onDone('cancelled');

      emit('message', {
        role: 'assistant',
        text: '收到！我会按 4 步推进：先收集资料，再起草产物，校验后交付。每一步都会在事件流里展示给你。',
      });
      await sleep(700);
      if (o.isCancelled()) return o.onDone('cancelled');

      emit('knowledge', {
        title: '最佳实践：任务拆解',
        content:
          '把大任务拆成可独立验证的小步骤；一次迭代只做一个工具调用；中间产物随手落盘。（Knowledge 模块演示内容）',
      });
      await sleep(500);
      if (o.isCancelled()) return o.onDone('cancelled');

      /* 步骤 1：检索 */
      emit('action', {
        tool: 'info_search',
        params: { query: `${clip(input, 40)} 要点 最佳实践` },
        call_id: 'call_001',
      });
      await sleep(900);
      emit('observation', {
        call_id: 'call_001',
        ok: true,
        result:
          '找到 3 条相关资料：\n1. 任务拆解与进度可视化的方法论\n2. 本地文件沙箱与权限护栏的最佳实践\n3. 事件驱动 UI 渲染模式',
        duration_ms: 873,
      });

      emit('plan', {
        steps: setStep(steps, 1, 'done', 2),
        current_step: 2,
        reflection: '资料已就绪，开始起草产物',
      });
      await sleep(400);
      if (o.isCancelled()) return o.onDone('cancelled');

      /* 步骤 2：写文件 */
      emit('action', {
        tool: 'file_write',
        params: {
          path: 'output/结果.md',
          content: `# 关于「${clip(input, 30)}」的整理\n\n（Mock 生成的产物正文…）\n\n- 要点一\n- 要点二\n- 要点三\n`,
        },
        call_id: 'call_002',
      });
      await sleep(800);
      emit('observation', {
        call_id: 'call_002',
        ok: true,
        result: '已写入 output/结果.md（214 字节）',
        duration_ms: 121,
      });

      emit('plan', {
        steps: setStep(steps, 2, 'done', 3),
        current_step: 3,
        reflection: '初稿完成，开始校验',
      });
      await sleep(300);
      if (o.isCancelled()) return o.onDone('cancelled');

      /* 步骤 3：校验 */
      emit('action', {
        tool: 'shell_exec',
        params: { command: 'python check.py output/结果.md' },
        call_id: 'call_003',
      });
      await sleep(700);
      emit('observation', {
        call_id: 'call_003',
        ok: true,
        result: '校验通过：0 个错误，0 个警告',
        duration_ms: 356,
      });

      emit('plan', {
        steps: setStep(steps, 3, 'done', 4),
        current_step: 4,
        reflection: '校验通过，准备交付',
      });
      await sleep(300);
      if (o.isCancelled()) return o.onDone('cancelled');

      /* 步骤 4：交付 */
      emit('message', {
        role: 'assistant',
        text: '任务完成 ✅ 产物已生成：\n- output/结果.md —— 主产物\n- output/摘要.txt —— 摘要\n全程每一步都通过事件流展示，点击左侧工具卡片可看参数与结果。',
        attachments: ['output/结果.md', 'output/摘要.txt'],
      });
      return o.onDone('done');
    } catch (err) {
      o.emit('error', { message: String(err), code: 'mock_run_failed' });
      return o.onDone('failed');
    }
  })();

  return () => {
    /* 取消走 isCancelled 轮询，无需额外清理 */
  };
}

function setStep(
  steps: PlanStep[],
  no: number,
  status: PlanStep['status'],
  nextInProgress: number,
): PlanStep[] {
  return steps.map((s) => ({
    ...s,
    status: s.no === no ? status : s.no === nextInProgress ? 'in_progress' : s.status,
  }));
}
