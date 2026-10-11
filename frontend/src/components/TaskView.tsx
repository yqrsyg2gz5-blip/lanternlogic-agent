import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import {
  Mic, Radio, Square, ArrowUp, ArrowDown, Paperclip, Volume2, VolumeX, Search,
  ListTodo, Package, Hand, Shield, Zap, Check, X, Circle, Play,
} from 'lucide-react';
import { api } from '../api';
import type {
  ActionPayload,
  EventEnvelope,
  MessageDeltaPayload,
  MessagePayload,
  ObservationPayload,
  PlanPayload,
  StatusPayload,
  TaskSummary,
} from '../types';
import { EventItem } from './EventItem';
import { TaskDigest } from './TaskDigest';
import { buildCorpus, isDuplicateOfEarlier } from '../lib/dedupe';
import { startWavRecording } from '../lib/recwav';
import { ArtifactPanel } from './ArtifactPanel';
import { ModelPicker } from './ModelPicker';
import { Hero } from './Hero';

interface Props {
  taskId: string;
  onTasksChanged(): void;
}

/** ★ 2026-10-08：朗读后端的中文名（只在"退回提示"里用 ✓ 与设置页那三个 id 对齐 ✓）
 *  为什么不在提示里直接写 id ✗：用户看到的是"你选的 melotts 用不了"—— 那是**行话** ✓
 *  （用户根本不知道 melotts 是什么 ✓ 他点的是界面上那行「MeloTTS（本地，MIT 可商用）」✓）*/
const TTS_LABEL: Record<string, string> = {
  edge: 'Edge（在线）',
  melotts: 'MeloTTS（本地）',
  pyttsx3: '系统自带语音',
};

/**
 * 任务视图（Task View）—— 全产品最核心的界面（规划书 §2.3）
 * 左：事件流（实时滚动）  右：计划面板  底：输入框
 * 数据源 = 事件流（getEvents 拉历史 + subscribeEvents 实时），seq 防重复（契约）。
 */
export function TaskView({ taskId, onTasksChanged }: Props) {
  const [task, setTask] = useState<TaskSummary | null>(null);
  const [events, setEvents] = useState<EventEnvelope[]>([]);
  const [input, setInput] = useState('');
  const streamRef = useRef<HTMLDivElement>(null);
  const lastSeqRef = useRef(0);

  useEffect(() => {
    let alive = true;

    void api.getTask(taskId).then((t) => {
      if (alive) setTask(t);
    });

    void api.getEvents(taskId).then((list) => {
      if (!alive) return;
      setEvents(list);
      lastSeqRef.current = list.reduce((m, e) => Math.max(m, e.seq), 0);
    });

    const unsub = api.subscribeEvents(taskId, (e) => {
      if (e.seq <= lastSeqRef.current) return; // 契约：seq 严格递增，防重复
      lastSeqRef.current = e.seq;
      setEvents((prev) => [...prev, e]);
      if (e.type === 'status') {
        onTasksChanged(); // 侧边栏状态点同步
        void api.getTask(taskId).then((t) => {
          if (alive) setTask(t);
        });
      }
      // 交付即呈现：任务完成（status state=done）时切到产物标签并立即刷新清单
      // （第 40 班痛点：报告躺在工作区文件里，用户不知道去哪找）
      if (e.type === 'status' && (e.payload as { state?: string }).state === 'done') {
        setAsideTab('artifacts');
        setArtifactPing((n) => n + 1);
      }
    });

    return () => {
      alive = false;
      unsub();
    };
  }, [taskId, onTasksChanged]);

  // 智能滚动：只在用户已贴底时自动跟随；用户上翻看历史时不打扰（防"窜"）
  const stickBottomRef = useRef(true);
  const [showJumpBottom, setShowJumpBottom] = useState(false);

  const onStreamScroll = () => {
    const el = streamRef.current;
    if (!el) return;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    stickBottomRef.current = nearBottom;
    setShowJumpBottom(!nearBottom);
  };

  useEffect(() => {
    if (stickBottomRef.current) {
      streamRef.current?.scrollTo({ top: streamRef.current.scrollHeight });
    }
  }, [events.length]);

  const plan = [...events]
    .reverse()
    .find((e) => e.type === 'plan')?.payload as PlanPayload | undefined;
  const lastStatus = [...events]
    .reverse()
    .find((e) => e.type === 'status')?.payload as StatusPayload | undefined;
  const running = task?.status === 'running';

  // waiting_approval 有两种来源，必须区分开（此前被混为一谈，用户曾因此卡死十几分钟）：
  //   ① 命令待审批 —— 后端在 status payload 里带 call_id（契约 add-only 字段）
  //   ② 人工接管暂停 —— 不带 call_id（旧后端没有该字段，用 detail 文案兜底）
  const isWaiting = lastStatus?.state === 'waiting_approval';
  const waitingApproval =
    !!isWaiting && (!!lastStatus?.call_id || /等待审批/.test(lastStatus?.detail ?? ''));
  const takeOverPaused = !!isWaiting && !waitingApproval;

  const lastActionEvent = [...events].reverse().find((e) => e.type === 'action');
  const lastActionPayload = lastActionEvent?.payload as ActionPayload | undefined;

  // ★ 2026-10-06（用户提的）：**token / 耗时小字** —— 一眼看出这次花了多少、跑了多久 ✓
  //   写成**函数**（不是常量）：`usage` 状态在后面才声明，常量会在声明前求值 ⇒ 报错 ✗（本班踩过）
  const metaLine = (): string => {
    const parts: string[] = [];
    if (usage && usage.calls) {
      parts.push(`${usage.calls} 次调用`, `共 ${usage.total_tokens.toLocaleString()} tok`);
    }
    if (events.length >= 2) {
      const t0 = Date.parse(String(events[0].ts ?? ''));
      const t1 = Date.parse(String(events[events.length - 1].ts ?? ''));
      if (Number.isFinite(t0) && Number.isFinite(t1) && t1 >= t0) {
        const s = Math.round((t1 - t0) / 1000);
        parts.push(`用时 ${s >= 60 ? `${Math.floor(s / 60)} 分 ${s % 60} 秒` : `${s} 秒`}`);
      }
    }
    return parts.join(' · ');
  };
  // 待审批的 call_id：优先取 status 里的（精确），旧后端回退到"最近一条 action"
  const pendingCallId = waitingApproval
    ? lastStatus?.call_id ?? lastActionPayload?.call_id
    : undefined;
  const pendingCommand = pendingCallId
    ? String((lastActionPayload?.params as Record<string, unknown> | undefined)?.command ?? '')
    : '';

  // 产物区：任务交付后拉一次工作区文件清单（html 可直接预览）
  const [speaking, setSpeaking] = useState(false);
  // 自动朗读总开关（第 41 班）：开 = 每条回复交付完自动全文朗读（逐句流水线，开口快）；关 = 安静。状态记忆。
  // 全应用同一时刻只允许一个朗读声音（切对话不打架，第 41 班）：模块级单例
let activeAudio: HTMLAudioElement | null = null;
const [autoRead, setAutoRead] = useState<boolean>(() => localStorage.getItem('autoRead') === '1');
  const lastSpokenRef = useRef('');
  const speakAbortRef = useRef<AbortController | null>(null);
  // 语音两模式 + 收音增强（第 41 班，用户建议）：
  //   🎤 草稿模式 = 说话→文字进输入框供检查后手动发送；🎙 会议模式 = 持续转写进 meeting_notes.md。
  //   收音增强 = WebAudio GainNode 把麦克风信号放大（默认 2.5 倍，远一点也能收清；代价是底噪同步放大）。
  const [meetingOn, setMeetingOn] = useState(false);
  const meetingRef = useRef<{ stream: MediaStream; timer: number | null; secs: number; stopped: boolean } | null>(null);
  // ★★ 2026-10-07（用户："录完不显示" ✗）：会议**每一段的结果都要有回音** ✓
  //   此前转写结果只进 meeting_notes.md ✓ 界面上**一个字都没有** ✗
  //   ⇒ 用户没法判断"到底录进去没有"（而他这次遇到的恰恰是**根本没录进去** ✗✓）。
  const [meetingHint, setMeetingHint] = useState('');
  const meetingSegsRef = useRef(0);
  // ★ 2026-10-07：总结指令**只发一次** ✓（用户那个任务里发了 3 遍 ✗ 见 toggleMeeting 的注释 ✓）
  const meetingSummarySentRef = useRef(false);
  const [draftOn, setDraftOn] = useState(false);
  // 磁盘任务预警（第 41 班，用户建议）：沙箱开着 + 任务涉及本机磁盘 → 发送前先提示
  const [diskWarn, setDiskWarn] = useState<string | null>(null);
  const [draftHint, setDraftHint] = useState('');
  // ★★ 2026-10-08：朗读"退回"的提示（你点 A 它念 B 时冒出来 ✓ 见 speakText ✓）
  //   两样分开存：`ttsNote` 是给人看的一句话 ✓ `ttsWhy` 是后端给的原因（挂 title ✓）
  const [ttsNote, setTtsNote] = useState('');
  const [ttsWhy, setTtsWhy] = useState('');
  const draftRef = useRef<{ stream: MediaStream; ctx: AudioContext | null; rec: MediaRecorder; chunks: Blob[] } | null>(null);
  const [gainOn, setGainOn] = useState(true);
  const micCtxRef = useRef<AudioContext | null>(null);
  // ★ 浏览器侧 WAV 录音句柄（首选路径；见 lib/recwav.ts 顶部说明）
  const wavRef = useRef<import('../lib/recwav').WavRecorder | null>(null);

  /** 拿麦克风流：增强开 = 经 GainNode 放大（远场收音），关 = 原声。ctx 记在 ref，停止时关闭防泄漏。 */
  const getMicStream = async (): Promise<MediaStream> => {
    const raw = await navigator.mediaDevices.getUserMedia({ audio: true });
    if (!gainOn) return raw;
    const ctx = new AudioContext();
    await ctx.resume();
    const src = ctx.createMediaStreamSource(raw);
    const gain = ctx.createGain();
    gain.gain.value = 2.5;
    const dst = ctx.createMediaStreamDestination();
    src.connect(gain);
    gain.connect(dst);
    micCtxRef.current = ctx;
    return dst.stream;
  };

  const closeMicCtx = (): void => {
    void micCtxRef.current?.close().catch(() => undefined);
    micCtxRef.current = null;
  };

  /** 把录到的音频交给后端转写并填进输入框（WAV 与 webm 两条路共用这一段）。 */
  const transcribeIntoDraft = async (b: Blob): Promise<void> => {
    console.log('[voice] 上传：', b.type, b.size, '字节');   // 排障用（一眼看出走的是 wav 还是 webm）
    if (b.size < 2048) {
      setDraftHint('（录音太短没听清，说完一句再停）');
      return;
    }
    try {
      const r = await api.voiceTranscribe(b);
      if (r.text) {
        setInput((prev) => (prev ? `${prev} ${r.text}` : r.text));
        setDraftHint('');
      } else {
        setDraftHint('（没听清，再试一次）');
      }
    } catch (e) {
      setDraftHint(`（转写失败：${e instanceof Error ? e.message : String(e)}）`);
    }
  };

  const stopDraft = (): Promise<void> => {
    // ★ 顺序不能反：WAV 那条路**不写 draftRef**（它是独立句柄），
    //   若先判 `if (!draftRef.current) return`，走 WAV 时就会直接返回 ——
    //   不停止、不转写，界面看着"点了没反应"（本班实测踩到，浏览器验证抓出来的）。
    if (wavRef.current) {
      const w = wavRef.current;
      wavRef.current = null;
      setDraftOn(false);
      setDraftHint('转写中…');
      return w.stop().then(transcribeIntoDraft);
    }
    const st = draftRef.current;
    if (!st) return Promise.resolve();
    draftRef.current = null;
    setDraftOn(false);
    setDraftHint('转写中…');
    const blob = new Promise<Blob>((resolve) => {
      st.rec.onstop = () => resolve(new Blob(st.chunks, { type: 'audio/webm' }));
      st.rec.state !== 'inactive' && st.rec.stop();
    });
    st.stream.getTracks().forEach((t) => t.stop());
    closeMicCtx();
    return blob.then(transcribeIntoDraft);
  };

  const toggleDraft = async (): Promise<void> => {
    if (draftRef.current || wavRef.current) {
      await stopDraft();
      return;
    }
    // ★ 首选：浏览器侧直接录 WAV（后端转码依赖 ffmpeg，而干净机器上没有它 —— 用户实测踩到）
    try {
      wavRef.current = await startWavRecording({ gain: gainOn ? 2.5 : 1 });
      console.log('[voice] 开始录音：浏览器内置 WAV 路');
      setDraftOn(true);
      setDraftHint('');
      return;
    } catch (e) {
      // ★ 兜底不许"静默换路"：换路本身没问题，但原因必须说出来 ——
      //   否则一路退到 webm、再被网关拒（format must be wav/mp3），用户只看到"转写失败"
      console.warn('[recwav] 浏览器内置 WAV 录音不可用，改用 MediaRecorder 备用路径：', e);
      setDraftHint(`（内置录音不可用，改用备用录音：${e instanceof Error ? e.message : String(e)}）`);
      wavRef.current = null;      // 落到下面的 MediaRecorder（老路，后端再想办法转码）
    }
    try {
      const stream = await getMicStream();
      const rec = new MediaRecorder(stream);
      const chunks: Blob[] = [];
      rec.ondataavailable = (e) => e.data.size > 0 && chunks.push(e.data);
      rec.start();
      draftRef.current = { stream, ctx: micCtxRef.current, rec, chunks };
      setDraftOn(true);
    } catch {
      setDraftHint('（麦克风不可用：请检查系统麦克风权限）');
    }
  };

  // —— 朗读（第 41 班流式化）：逐句流水线（后端按句合成返回清单），第一句合成完就开口，不等全文 ——
  const stopSpeaking = (): void => {
    speakAbortRef.current?.abort();
    speakAbortRef.current = null;
    if (activeAudio) {
      activeAudio.pause();
      activeAudio = null;
    }
    setSpeaking(false);
  };

  const speakText = async (text: string): Promise<boolean> => {
    if (!text.trim() || !task || !aliveRef.current) return false;
    stopSpeaking();
    setTtsNote('');          // ★ 2026-10-08：每读一次先清掉上一条提示 ✓（免得它一直挂着 ✓）
    setSpeaking(true);
    const ac = new AbortController();
    speakAbortRef.current = ac;
    try {
      // 真流式：NDJSON 逐行收 URL——第一行到达（约 1-2s）即开播，后续句边合成边排队
      // ★★ 2026-10-07：这一段原来是自己 `fetch` + 自己解 NDJSON ✗ ——
      //   而**后端回的是相对地址**（`/api/v1/tasks/../tts-audio/x.mp3`）✗，
      //   `<audio>` 播放时**带不了访问密码** ⇒ 局域网模式下 401 ⇒ **一点声音都没有** ✗✗
      //   （本班真点按钮 + 真抓包量出来的：`/tts` 200 而音频那枪 401 ✓）
      //   ⇒ 现在整条收流交给 api 层 ✓ 它回的地址**已经补好密码** ✓（见 api.ttsSpeakStream ✓）
      const urls: string[] = [];
      let streamDone = false;
      let resume: (() => void) | null = null;
      let player: HTMLAudioElement | null = null;
      let idx = 0;

      const tryNext = (): void => {
        if (!player || !aliveRef.current) return; // 切走后 URL 到达也不复活
        if (idx < urls.length) {
          player.src = urls[idx++];
          void player.play().catch(() => setSpeaking(false));
        } else if (streamDone) {
          setSpeaking(false);
        } else {
          resume = tryNext; // 等下一段 URL 到达再续播
        }
      };

      const startPlayer = (): void => {
        if (!aliveRef.current) return; // 切走了：URL 到了也不出声
        player = new Audio(urls[idx++]);
        document.body.appendChild(player); // 挂进 DOM：可审计/可调试（无渲染）
        // 清掉切换残留的已暂停旧音频元素（防 body 里堆积）
        document.querySelectorAll('audio').forEach((a) => { if (a !== player) a.remove(); });
        activeAudio = player;
        player.onended = tryNext;
        player.onerror = () => setSpeaking(false);
        void player.play().catch(() => setSpeaking(false));
      };

      await api.ttsSpeakStream(task.id, text, ac.signal, (url) => {
        urls.push(url);
        if (!player) startPlayer();
        else if (resume) {
          const f = resume as () => void;
          resume = null;
          f();
        }
      }, (n) => {
        // ★★ 2026-10-08：**策略性换引擎**（长文本 ⇒ 自动换快的 ✓）—— 与下面那条**不是一回事** ✓
        //   这句要说的是「这段太长，我替你换了快的」✓ 不是「你选的用不了」✗
        if (n.policy) { setTtsNote(`ⓘ ${n.policy}`); setTtsWhy(''); return; }
        // ★★ 2026-10-08：**你点的是 A，念的是 B —— 必须当场说** ✗
        //   实测（用户当场问出来的）：点 MeloTTS（没装）会**静默**改用系统语音 ✓
        //   两个 wav 字节数一模一样 ✓ 而界面上一个字都没有 ✓ ⇒ 用户以为 MeloTTS 装好了 ✓
        setTtsNote(`⚠️ 这条用的是「${TTS_LABEL[n.used] ?? n.used}」念的`
                   + `—— 你选的「${TTS_LABEL[n.asked] ?? n.asked}」用不了。`
                   + `（怎么装见设置 → 语音合成）`);
        setTtsWhy(n.why);   // 完整原因（含安装指引）挂 title ✓ 想看再悬停 ✓
      });
      streamDone = true;
      if (resume) {
        const f = resume as () => void;
        resume = null;
        f();
      }
      if (!player) setSpeaking(false); // 一个 URL 都没收到
      return true;
    } catch {
      setSpeaking(false);
      return false;
    }
  };

  const lastAssistantText = (): string => {    const lastAssistant = [...events].reverse().find((e) => e.type === 'message' && (e.payload as { role?: string }).role === 'assistant');
    return lastAssistant ? String((lastAssistant.payload as { text?: string }).text ?? '') : '';
  };

  const aliveRef = useRef(true);
  // 严格模式（dev 双挂载）会把 cleanup 里置的 false 带进重挂载——重挂载时重新武装
  useEffect(() => {
    aliveRef.current = true;
  }, []);
  // 卸载/切对话：收干净——停朗读、停会议录音（已转写的段都在 meeting_notes.md 里，不丢）、停草稿
  useEffect(() => () => {
    aliveRef.current = false;
    speakAbortRef.current?.abort();
    speakAbortRef.current = null;
    if (activeAudio) {
      activeAudio.pause();
      activeAudio = null;
    }
    const m = meetingRef.current;
    if (m) {
      m.stopped = true;
      if (m.timer) window.clearInterval(m.timer);
      m.stream.getTracks().forEach((t) => t.stop());
      meetingRef.current = null;
    }
    const d = draftRef.current;
    if (d) {
      try { d.rec.state !== 'inactive' && d.rec.stop(); } catch { /* 已停 */ }
      d.stream.getTracks().forEach((t) => t.stop());
      closeMicCtx();
      draftRef.current = null;
    }
  }, []);

  // 自动朗读：任务空闲且开关心开 → 新回复自动全文朗读（lastSpokenRef 防重复）
  useEffect(() => {
    if (!autoRead || running) return;
    const text = lastAssistantText();
    if (text && text !== lastSpokenRef.current) {
      lastSpokenRef.current = text;
      void speakText(text);
    }
  }, [events, autoRead, running]);

  // 会议纪要 v1.1（第 41 班）：接力录音——每段是**完整的** webm（带文件头），
  // 12s 一段自动衔接；直接 timeslice 切片的第 2 段起会缺头导致转码/转写全挂（真机踩过）。
  // 时长不限：段数无上限，停止后自动让 Agent 总结。

  const meetingUpload = async (blob: Blob): Promise<void> => {
    try {
      const r = await api.meetingChunk(taskId, blob);
      /* ★★ 2026-10-07（用户："录完不显示、也不生成纪要" ✗✗ —— 本班真录一段量出来的 ✓）：
         后端**转写失败时也回 201** ✓ 只是 body 里 `ok:false` + `error` ✓
         （HTTP 层没报错 ⇒ `request()` 不会抛 ✗）而这里原来**把返回值直接丢掉** ✗
         ⇒ 用户界面上**一个字的反馈都没有** ✗✓ —— 正是最难查的那种"静默失败"✓。
         ⇒ 现在**分清三种结果都说话**：转写到了什么 ✓ / 这段没声音 ✓ / 这段失败了（带后端原话）✓ */
      if (r.ok && r.text) {
        setMeetingHint(`已记录 ${++meetingSegsRef.current} 段：${r.text.slice(0, 40)}`);
      } else if (r.ok) {
        setMeetingHint('这一段没听到人说话（未记录，录音继续）');
      } else {
        setMeetingHint(`这一段没转成文字：${(r as { error?: string }).error ?? '原因不明'}（录音继续）`);
      }
    } catch (e) {
      // 网络/鉴权这一层真抛了（与上面的"后端说不行"不是一回事 ✓）
      setMeetingHint(`这一段上传失败：${e instanceof Error ? e.message : String(e)}（录音继续）`);
      console.warn('[会议] 本段上传失败', e);
    }
  };

  /** 录**一段**（12 秒）。
   *
   *  ★★ 2026-10-07（"会议录入不进去"的根因 ✗✗ —— 本班量出来的 ✓）：
   *    这里原来用 `MediaRecorder` ⇒ 产出的是 **webm** ✗，而后端要 WAV/MP3：
   *      · 后端 `_to_wav()` 要靠 **ffmpeg** 转码 ✗ —— 本机（干净机器）**没装** ✗
   *      · 转不了就**静默退回原文件** ⇒ 本地 ASR 的 soundfile 打不开 ⇒
   *        `LibsndfileError: Format not recognised`（后端原话 ✓ 实测 0.0 秒就返回 ✓）
   *    ⇒ 而"语音输入（草稿）"那条路**早就修过同一个坑**了 ✓ ——
   *      改在**浏览器里直接录 16k 单声道 WAV** ✓（见 lib/recwav.ts 顶部：任何机器上都不依赖 ffmpeg ✓）
   *      唯独**会议这条路没跟上** ✗（同一个 bug 修了一条路、漏了另一条 ✓）。
   *    ⇒ 现在会议也走 WAV ✓ 首段失败才退回 webm ✓ 且**如实说**在走备用路 ✓（不静默换路 ✓）。*/
  const meetingRecordSegment = async (stream: MediaStream, state: NonNullable<typeof meetingRef.current>): Promise<void> => {
    if (state.stopped) return;
    const blob = await (async (): Promise<Blob> => {
      try {
        // ★ gain 传 1：`getMicStream()` 已经按"收音增强"放大过一道了 ✓ 这里再放就叠成两倍 ✗
        const w = await startWavRecording({ stream, gain: 1 });
        await new Promise((r) => setTimeout(r, 12000));      // 12s 一段
        return await w.stop();
      } catch (e) {
        console.warn('[会议] 浏览器内置 WAV 录音不可用，退回 MediaRecorder（webm）备用路径：', e);
        setMeetingHint(`内置录音不可用，改用备用录音（${e instanceof Error ? e.message : String(e)}）`);
        return await new Promise<Blob>((resolve) => {
          const rec = new MediaRecorder(stream);
          const chunks: Blob[] = [];
          rec.ondataavailable = (ev) => ev.data.size > 0 && chunks.push(ev.data);
          rec.onstop = () => resolve(new Blob(chunks, { type: 'audio/webm' }));
          rec.start();
          setTimeout(() => rec.state !== 'inactive' && rec.stop(), 12000);
        });
      }
    })();
    if (state.stopped) return; // 停止后不再上传尾段
    void meetingUpload(blob);
    state.secs += 12;
    if (!state.stopped) void meetingRecordSegment(stream, state); // 接力下一段
  };

  const toggleMeeting = async (): Promise<void> => {
    if (meetingRef.current) {
      // 停止：置停止标记（当前段自然收尾不乱入），随后发总结指令
      const state = meetingRef.current;
      state.stopped = true;
      if (state.timer) window.clearInterval(state.timer);
      state.stream.getTracks().forEach((t) => t.stop());
      closeMicCtx();
      meetingRef.current = null;
      setMeetingOn(false);
      // ★ 2026-10-07：停下来的那一刻要给个交代 ✓（录进去几段 ✓ 在干什么 ✓）
      //   ——用户这次的困惑正是"停下来之后什么都没有"✗
      setMeetingHint(meetingSegsRef.current > 0
        ? `已记录 ${meetingSegsRef.current} 段，正在让 Agent 整理纪要…`
        : '这次一段都没转成文字 —— 纪要会如实说明缺素材（可以改用打字把内容发给我）');
      /* ★★ 2026-10-07（用户实测报的 ✗✗）：
         "会议纪要"这个任务**失败了** ✓ 看事件流发现两件事：
           ① 这条指令**写死假设工作区里有 `meeting_notes.md`** ✗
              而它**只有录了音**才会有（`meetingUpload` 才写它 ✓）
              ⇒ 用户"打字开的会"（比如只输入"会议纪要"四个字 ✓）
                根本没有这个文件 ✗ ⇒ Agent 老实回答"没有素材、我不编造"✓
                但指令不认这个答案 ⇒ 反复失败 ✗
           ② 这条指令**会被重复发** ✗（用户那个任务里发了 3 遍 ✓
              因为停止会议可能在多处被触发 ✓ 切页面/卸载也会 ✓）
         ⇒ 修法：
           ① 指令改成**两种情况都认** ✓（有转写就读转写 ✓ 没有就把对话本身当会议内容 ✓）
             并且明说"**没素材就如实说、不许编造**"✓（与项目一贯口径一致 ✓）
           ② 加一道**只发一次**的闸 ✓（同一场会议只发一条总结指令 ✓）*/
      if (meetingSummarySentRef.current) return;      // ② 只发一次 ✓
      meetingSummarySentRef.current = true;
      void api.sendMessage(
        taskId,
        '会议结束了。请整理一份会议纪要，按「议题 / 结论 / 待办（含负责人与截止时间）」组织，以 task_done 交付。'
        + '素材来源按实际情况取其一：'
        + '① 如果工作区里有 meeting_notes.md（那是刚才录音的转写 ✓）——读它整理；'
        + '② 如果没有那个文件（比如这场会是打字开的 ✓）——**就把我们上面的对话内容本身当作会议内容**整理。'
        + '⚠️ 两种都没有、确实无从整理时，**如实说明缺少素材**并以 failed 交付，**不要编造会议内容**。');
      return;
    }
    try {
      const stream = await getMicStream();
      const state = { stream, timer: null as number | null, secs: 0, stopped: false };
      meetingRef.current = state;
      setMeetingOn(true);
      setMeetingHint('正在录第 1 段（每 12 秒一段，转写结果会显示在这里）');
      meetingSegsRef.current = 0;
      state.timer = window.setInterval(() => {
        if (meetingRef.current === state) {
          state.secs += 1;
        }
      }, 1000);
      void meetingRecordSegment(stream, state);
    } catch (e) {
      // ★ 2026-10-07：原来是**一声不吭地退回去** ✗ —— 用户只看到"点了没反应" ✗
      setMeetingOn(false);
      setMeetingHint(`麦克风打不开，会议没法录：${e instanceof Error ? e.message : String(e)}`);
    }
  };
  const [asideTab, setAsideTab] = useState<'plan' | 'artifacts'>('plan');
  const [artifactPing, setArtifactPing] = useState(0); // task_done 时 +1：让产物面板立即刷新（不等 6s 轮询）
  const [artCount, setArtCount] = useState(0); // 产物数量（ArtifactPanel 实时上报——产物区轮询比本页 files 状态新）
  const [asideOpen, setAsideOpen] = useState(true);
  // 第六轮复验第 9 条：与 CSS 的 ≤760 .tv-aside{display:none} 对齐——
  // 开关据此禁用，避免"点了没反应"
  const [asideVisible, setAsideVisible] = useState(() =>
    typeof window === 'undefined' ? true : window.innerWidth > 760);
  useEffect(() => {
    const on = () => setAsideVisible(window.innerWidth > 760);
    on();  // 挂载时对齐一次
    window.addEventListener('resize', on);
    return () => window.removeEventListener('resize', on);
  }, []);
  const [usage, setUsage] = useState<{ total_tokens: number; calls: number } | null>(null);
  const [composerH, setComposerH] = useState(38);
  const [accessMode, setAccessMode] = useState<'full' | 'auto_edit' | 'confirm'>('auto_edit');
  const [modeOpen, setModeOpen] = useState(false);
  const [modelName, setModelName] = useState('');
  const [sandboxOn, setSandboxOn] = useState(false);
  // 身份选择（第 41 班）：正常聊天中以某专家身份执行（人设后端注入）
  const [identity, setIdentity] = useState<string>(() => localStorage.getItem('identity') ?? '');
  const [roleList, setRoleList] = useState<{ role: string; dept: string; summary: string }[]>([]);
  // ★ 2026-10-07（第 8 项）：这句话该派谁（只在命中关键词时才有值 ✓ 没把握就是 null ✓）
  const [roleTip, setRoleTip] = useState<{ role: string; why: string } | null>(null);
  // ★ 看这句话该派谁 ✓ —— 只在**没选身份**时才问 ✓（已经选了的人不需要被推荐 ✓ 别打扰他 ✓）
  //   按"最后一条用户消息"的变化去问 ✓ 不是每渲染一次都问 ✓
  const lastUserText = useMemo(() => {
    const last = [...events].reverse().find(
      (e) => e.type === 'message' && (e.payload as { role?: string }).role === 'user');
    return last ? String((last.payload as { text?: string }).text ?? '') : '';
  }, [events]);
  useEffect(() => {
    if (identity || !lastUserText.trim() || !task) {
      setRoleTip(null);
      return;
    }
    let alive = true;
    void api.suggestRoles(lastUserText).then((r) => {
      if (!alive) return;
      const first = r.suggestions?.[0];
      // ★ 没把握（空数组）⇒ **一个字都不显示** ✓（乱推比不推更烦人 ✓）
      setRoleTip(first ? { role: first.role, why: first.why } : null);
    }).catch(() => undefined);
    return () => { alive = false; };
  }, [identity, lastUserText, task]);
  const [sandboxHint, setSandboxHint] = useState('');
  useEffect(() => {
    void api.getAccessMode().then((r) => setAccessMode(r.mode as 'full' | 'auto_edit' | 'confirm'));
    void api.teamRoles().then((d) => setRoleList(d.roles ?? [])).catch(() => undefined);
    void api.getSettings().then((s) => {
      const m = (s as { model?: { model_name?: string } }).model;
      setModelName(m?.model_name ?? '');
      setSandboxOn((s as { executor?: { sandbox?: string } }).executor?.sandbox === 'docker');
    });
  }, [taskId]);
  const [usageOpen, setUsageOpen] = useState(false);
  const [usageFull, setUsageFull] = useState<null | { input_tokens: number; output_tokens: number; total_tokens: number; cached_tokens?: number; calls: number; tasks_with_usage: number; task_count: number; by_model: Record<string, { input: number; output: number; calls: number }>; by_task?: { task_id: string; label: string; model: string; calls: number; input_tokens: number; output_tokens: number; total_tokens: number }[] }>(null);

  // 拖动对话框上沿调整高度（像 ZCode 那样可拖大拖小）
  const onResizeDown = (e: React.MouseEvent) => {
    e.preventDefault();
    const startY = e.clientY;
    const startH = composerH;
    const onMove = (ev: MouseEvent) => {
      const h = Math.min(320, Math.max(32, startH + (startY - ev.clientY)));
      setComposerH(h);
    };
    const onUp = () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  };
  // 全局用量：每 10s 刷新（右下角常驻统计）
  useEffect(() => {
    const load = () => void api.getUsage().then((u) => { setUsage({ total_tokens: u.total_tokens, calls: u.calls }); setUsageFull(u); });
    load();
    const t = setInterval(load, 10000);
    return () => clearInterval(t);
  }, [taskId]);
  const [skills, setSkills] = useState<{ name: string; description: string }[]>([]);
  const [slashOpen, setSlashOpen] = useState(false);
  const [approving, setApproving] = useState(false);
  const [approveError, setApproveError] = useState<string | null>(null);

  // §5.4（审计）：权限下拉弹层此前只能靠再点一次按钮关闭——Escape 与点击
  // 外部均无效。统一在这里挂全局监听（🟡补用量弹层）。
  // 第 48 班：模型弹层收进 ModelPicker 组件自管（同机制），此处不再管它。
  useEffect(() => {
    // 二十六轮第 5 批第 7-2：slash 菜单 Escape 不关（需清空输入才隐）——
    // 纳入同一全局 Escape 通道（slashOpen 时也不短路 return）
    if (!modeOpen && !usageOpen && !slashOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { setModeOpen(false); setUsageOpen(false); setSlashOpen(false); }
    };
    const onDown = (e: MouseEvent) => {
      const el = e.target as HTMLElement | null;
      if (el && el.closest('.ctl-wrap')) return; // 弹层内部/按钮自身的点击交给各自的 onClick
      if (el && el.closest('.usage-badge')) return;
      setModeOpen(false);
      setUsageOpen(false);
    };
    window.addEventListener('keydown', onKey);
    window.addEventListener('mousedown', onDown);
    return () => {
      window.removeEventListener('keydown', onKey);
      window.removeEventListener('mousedown', onDown);
    };
  }, [modeOpen, usageOpen, slashOpen]);

  // 流式增量：最后一条 message 之后累积的 delta（随完整 message 事件收尾清零，不做时间线节点）
  let streamingText = '';
  let lastMsgSeq = 0;
  for (const e of events) {
    if (e.type === 'message') lastMsgSeq = e.seq;
    if (e.type === 'message_delta' && e.seq > lastMsgSeq) {
      streamingText += (e.payload as MessageDeltaPayload).delta;
    }
  }
  // ★ 必须 memo：不 memo 的话每次渲染都是新数组 ⇒ 依赖它的 effect（搜索高亮）每次渲染都重跑，
  //   于是"Enter 切下一个命中"刚设好就被重置回第 1 个（本班实测踩到，且这是个白白重算的坑）。
  const timeline = useMemo(() => events.filter((e) => e.type !== 'message_delta'), [events]);
  // ★ 应用层去重：算出"哪些助手回复其实是把上面已经说过的内容又抄了一遍"。
  //   语料 = **工具结果** + **之前自己的回复**（拼接型重复靠后者抓到）。
  //   ★ 刻意**不把 task_done 的交付语**放进语料：交付时应用会把
  //     `task_done(message=答案)` 发成正式回复 ⇒ 答案文本必然等于那条参数
  //     ⇒ 一放进去"每一条正常回答"都会被判重复（2026-10-05 自查抓到：
  //     连"详细自我介绍"都被算成覆盖率 1.0，差点把用户真想看的内容收掉）。
  //   纯函数、只在事件变化时算一次；结果是**每个事件稳定的布尔**，memo 不受影响。
  // ★ 快捷键：**按住右 Ctrl** 说话、松开自动转写（与首页同一个用法）。
  //   不用 Ctrl+空格：中文 Windows 上那是切换输入法的系统键，会被 IME 抢走（用户实测）。
  //   这里没走 lib/useVoiceDraft 的 hook，是因为本组件的录音 ref 还要给"会议记录"复用
  //   （硬抽会牵动会议功能）—— 但两条路都必须是 **WAV**（见 recwav 顶部说明）。
  useEffect(() => {
    let holding = false;
    const isStart = (e: KeyboardEvent): boolean =>
      e.code === 'ControlRight' || (e.ctrlKey && (e.code === 'Space' || e.key === ' '));
    const isEnd = (e: KeyboardEvent): boolean =>
      e.code === 'ControlRight' || e.code === 'Space' || e.key === ' ';
    const down = (e: KeyboardEvent): void => {
      if (!isStart(e) || e.repeat || holding) return;
      e.preventDefault();
      holding = true;
      if (!draftRef.current && !wavRef.current) void toggleDraft();
    };
    const up = (e: KeyboardEvent): void => {
      if (!holding || !isEnd(e)) return;
      holding = false;
      if (draftRef.current || wavRef.current) void toggleDraft();
    };
    window.addEventListener('keydown', down);
    window.addEventListener('keyup', up);
    return () => {
      window.removeEventListener('keydown', down);
      window.removeEventListener('keyup', up);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ★ 对话内搜索：高亮用 **DOM 文本节点**包 <mark>（Markdown 渲染出来的内容也能命中），
  //   每次搜索词变化先把上一次的 <mark> 还原成纯文本，避免越搜越乱。
  const [searchOpen, setSearchOpen] = useState(false);
  const [search, setSearch] = useState('');
  const [hitCount, setHitCount] = useState(0);
  const [hit, setHit] = useState(0);

  // ★ 搜索高亮**不再直接改 DOM**（2026-10-05 用户实测崩溃）：
  //   旧实现用 TreeWalker + replaceWith 换文本节点，React 仍记着旧节点 ⇒ 任何一次重渲染
  //   （例如点"允许一次"）都会抛 `insertBefore ... is not a child of this node`，整页被卸载。
  //   现在高亮由 EventItem/Markdown 在**渲染树**里切 <mark>（见 lib/rehypeHighlight.ts），
  //   这里只负责**读** DOM：数命中数、滚动到当前命中。
  useLayoutEffect(() => {
    const q = search.trim();
    if (!q) { setHitCount(0); setHit(0); return; }
    const marks = document.querySelectorAll('mark.search-hit');
    setHitCount(marks.length);
    // 保留当前位置但夹住范围（不硬重置为 0：换词后从头看更自然，但别把"下一个"的进度抹掉）
    setHit((h) => (marks.length ? Math.min(h, marks.length - 1) : 0));
  }, [search, timeline]);

  // 跳到当前命中（并把它标成"当前"）—— 这里只是切换 class 与滚动，**没有增删节点**
  useLayoutEffect(() => {
    const marks = document.querySelectorAll<HTMLElement>('mark.search-hit');
    marks.forEach((m, i) => m.classList.toggle('search-hit-on', i === hit));
    marks[hit]?.scrollIntoView({ block: 'center', behavior: 'smooth' });
  }, [hit, hitCount]);

  const stepHit = useCallback((delta: number) => {
    setHit((cur) => {
      const total = document.querySelectorAll('mark.search-hit').length;
      if (!total) return 0;
      return (cur + delta + total) % total;
    });
  }, []);

  // Ctrl+F（⌘F）打开搜索框并聚焦 —— 与浏览器自带的"页内查找"抢一下，避免它去翻整页 DOM
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'f') {
        e.preventDefault();
        setSearchOpen(true);
      }
      if (e.key === 'Escape') { setSearchOpen(false); setSearch(''); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);


  //   用 useCallback 固定引用 —— EventItem 是 memo 的，回调每次变会让整条时间线重渲染。
  const editResend = useCallback((seq: number, text: string) => {
    setDraftHint('正在从你改的那一句重跑…');
    void api.sendMessage(taskId, text, undefined, seq)
      .then(() => { stickBottomRef.current = true; })
      .catch((e) => setDraftHint(`改一句重发失败：${e}`));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [taskId]);

  const dupIds = useMemo(() => {
    const dup = new Set<string>();
    const seen: string[] = [];
    for (const e of events) {
      if (e.type === 'observation') {
        const r = (e.payload as ObservationPayload).result;
        if (r) seen.push(r);
      } else if (e.type === 'message' && (e.payload as MessagePayload).role === 'assistant') {
        const text = (e.payload as MessagePayload).text ?? '';
        if (isDuplicateOfEarlier(text, [buildCorpus(seen)])) dup.add(e.id);
        seen.push(text);            // 自己的回复也进语料（拼接型重复就是靠这一条抓到的）
      }
    }
    return dup;
  }, [events]);

  // ⚠️ 附件相关的 hooks 必须在**任何 return 之前**（React Hooks 规则）——
  // 2026-09-30：一开始我把它们写在 `if (!task) return` 后面，触发
  // "Rendered more hooks than during the previous render"，整个输入区渲染不出来。
  const [pending, setPending] = useState<{ name: string; size: number }[]>([]);
  const [uploadErr, setUploadErr] = useState<string | null>(null);
  const [dragOn, setDragOn] = useState(false);
  const fileRef = useRef<HTMLInputElement | null>(null);

  if (!task) return <Hero onCreated={onTasksChanged} />;

  /** 附件上传（第 28 班）：文件存进任务工作区，发送时把文件名写进消息 */
  const upload = async (files: FileList | null): Promise<void> => {
    if (!files || files.length === 0) return;
    setUploadErr(null);
    for (const f of Array.from(files)) {
      try {
        const r = await api.uploadFile(task.id, f);
        setPending((p) => [...p, { name: r.name, size: r.size }]);
      } catch (e) {
        setUploadErr(e instanceof Error ? e.message : String(e));
      }
    }
  };

  const send = () => {
    const v = input.trim();
    const names = pending.map((p) => p.name);
    if (!v && names.length === 0) return;
    // 沙箱开着 + 任务涉及本机磁盘 → 先提示（用户建议：别让 Agent 白忙一圈）
    if (sandboxOn && /(C盘|D盘|[cCdD]:\|磁盘|硬盘|桌面文件|整理.*(桌面|下载)|Disk)/i.test(v)) {
      setDiskWarn(v);
      return;
    }
    setInput('');
    setPending([]);
    // 附件已经在工作区里了；把文件名写进消息，模型才知道它们存在
    const text = names.length
      ? `${v}${v ? '\n\n' : ''}（附件已放入工作区：${names.join('、')} —— 可用 image_read / file_read 查看）`
      : v;
    // P1-10：接管中必须走 /resume（交还），不能走 /messages。
    // 接管是"打断"——history 末尾会留下悬空的 tool_calls，后端 /resume 会先补一条
    // 中断回执再重新拉起；而 /messages 直接开新一轮 → 上游必 400。
    // （旧实现只有 /takeover 没有交还，人工接管成了单向陷阱。）
    if (takeOverPaused) {
      void api.resumeTask(task.id, text);
    } else {
      void api.sendMessage(task.id, text);
    }
  };

  const decide = async (decision: 'once' | 'always' | 'deny') => {
    if (!pendingCallId) return;
    setApproving(true);
    setApproveError(null);
    try {
      await api.approveTask(task.id, pendingCallId, decision);
      onTasksChanged();
    } catch (e) {
      setApproveError(e instanceof Error ? e.message : String(e));
    } finally {
      setApproving(false);
    }
  };

  return (
    <main className="taskview">
      <header className="tv-head">
        <div>
          <div className="tv-title">{task.title}</div>
          <div className="tv-sub mono">
            {task.id} · {lastStatus?.state ?? task.status}
            {/* ★ 2026-10-06：token / 耗时小字（用户提的）—— 不用点开算，一眼看到花了多少、跑了多久 ✓ */}
            {metaLine() && <span className="tv-meta" title="用量与耗时（来自本任务的用量事件）"> · {metaLine()}</span>}
            {/* A3（审计台账 P1）：沙箱关着 = 宿主模式，命令真的在这台电脑上跑。
                此前界面上**没有任何常驻标识**——"自动编辑"四个字看不出是本机执行。
                这里是常驻显式提示（不必打开权限弹层/设置页就能看见）。
                判据/回滚实验见 backend/tests/test_host_mode_notice.py。 */}
            {!sandboxOn && (
              <span
                className="host-exec-tag"
                title="沙箱已关闭：Agent 的 shell 命令直接在你的真实电脑上执行——删除、改写、安装都真实发生、不可撤销。到 设置 → 执行环境 可开启沙箱。"
              >⚠ 本机执行</span>
            )}
            {lastStatus?.state === 'failed' && (
              <button
                className="retry-btn"
                aria-label="重试失败的任务"
                title="用最后一条消息重试"
                onClick={() => {
                  const lastUser = [...timeline].reverse().find((e) => e.type === 'message' && (e.payload as { role?: string }).role === 'user');
                  const text = String((lastUser?.payload as { text?: string } | undefined)?.text || task.title);
                  void api.sendMessage(task.id, text).catch(() => undefined);
                }}
              >↻ 重试</button>
            )}
          </div>
        </div>
        <div className="tv-actions">
          {/* ★ 对话内搜索（Phase 3 ⑦）：长任务里"我记得它说过 X"是最常见的一次操作。
              只在**消息气泡**里找（工具卡片不参与），命中用 DOM 文本节点包 <mark> —— 
              这样 Markdown 渲染出来的内容也能高亮，且不用把搜索词透传进 memo 的 EventItem。 */}
          <button
            className={`icon-btn ${searchOpen ? 'icon-btn-on' : ''}`}
            title="在对话里搜索（Ctrl+F / Esc 关闭，Enter 下一个，Shift+Enter 上一个）"
            onClick={() => {
              setSearchOpen((v) => !v);
              if (searchOpen) { setSearch(''); }
            }}
          >
            <Search size={15} />
          </button>
          {lastStatus?.state === 'idle' && (
            <button
              className={`icon-btn ${autoRead ? 'icon-btn-on' : ''}`}
              title={autoRead ? '自动朗读：开（新回复自动念出来）——点击关闭' : '自动朗读：关——点击开启（并朗读当前回复）'}
              onClick={async () => {
                const next = !autoRead;
                setAutoRead(next);
                localStorage.setItem('autoRead', next ? '1' : '0');
                if (next) {
                  const text = lastAssistantText();
                  if (text) void speakText(text);
                } else {
                  stopSpeaking();
                }
              }}
            >
              {autoRead ? <Volume2 size={15} /> : <VolumeX size={15} />}
            </button>
          )}
          {lastStatus?.state === 'idle' && !autoRead && (
            <button
              className="icon-btn"
              title="朗读这条回复" aria-label="朗读这条回复"
              disabled={speaking}
              onClick={async () => {
                const text = lastAssistantText();
                if (text) void speakText(text);
              }}
            >
              {speaking ? '…' : '▶'}
            </button>
          )}
          {running && task.id && (
            <button
              className="icon-btn"
              title="人工接管（暂停 Agent，自己操作浏览器）" aria-label="人工接管（暂停 Agent，自己操作浏览器）"
              onClick={async () => {
                await api.takeoverTask(task.id);
                onTasksChanged();
              }}
            >
              <Hand size={15} />
            </button>
          )}
          {(running || isWaiting) && task.id && (
            <button className="icon-btn icon-btn-danger" title="取消任务" aria-label="取消任务" onClick={() => void api.cancelTask(task.id)}>
              <ArrowDown size={15} />
            </button>
          )}
          {/* 第六轮复验第 9 条：≤760 右栏是 display:none——开关按可见性 disabled，
              不再出现"点了没反应"的空操作（此前 title 照切但布局不变） */}
          <button
            className="icon-btn"
            title={asideVisible ? (asideOpen ? '收起右栏' : '展开右栏') : '右栏在窄屏下自动隐藏（拉宽窗口恢复）'}
            aria-label={asideVisible ? (asideOpen ? '收起右栏' : '展开右栏') : '右栏在窄屏不可用'}
            disabled={!asideVisible}
            style={!asideVisible ? { opacity: 0.35, cursor: 'default' } : undefined}
            onClick={() => setAsideOpen((v) => !v)}
          >
            {asideOpen ? '▸' : '◂'}
          </button>
        </div>
      </header>

      <div className="tv-body">
        {searchOpen && (
          <div className="find-bar">
            <Search size={13} />
            <input
              className="find-input"
              autoFocus
              value={search}
              placeholder="在对话里找…（Enter 下一个 / Shift+Enter 上一个）"
              onChange={(e) => setSearch(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') { e.preventDefault(); stepHit(e.shiftKey ? -1 : 1); }
                if (e.key === 'Escape') { setSearchOpen(false); setSearch(''); }
              }}
            />
            <span className="find-count">{search.trim() ? (hitCount ? `${hit + 1} / ${hitCount}` : '没找到') : ''}</span>
            <button className="btn-mini" disabled={!hitCount} onClick={() => stepHit(-1)} title="上一个">↑</button>
            <button className="btn-mini" disabled={!hitCount} onClick={() => stepHit(1)} title="下一个">↓</button>
            <button className="btn-mini" onClick={() => { setSearchOpen(false); setSearch(''); }} title="关闭">✕</button>
          </div>
        )}
        <div className="stream" ref={streamRef} onScroll={onStreamScroll}>
          {/* 2026-10-05 回退：**不折叠正常的一问一答**（用户明确："正常的一回一答不用折叠"）。
              去重只作用在"重复的那条回复"上（见下方 duplicate 标记）。 */}
          {timeline.map((e) => (
            <EventItem key={e.id} event={e} taskId={taskId} duplicate={dupIds.has(e.id)}
                       onEditResend={running ? undefined : editResend}
                       highlight={search.trim() || undefined} />
          ))}
          {streamingText && (
            <div className="msg-assistant">
              <div className="avatar">◍</div>
              <div className="bubble bubble-assistant streaming">
                <div className="msg-who">
                  Agent<span className="msg-live">生成中…</span>
                </div>
                {streamingText}
                <span className="stream-cursor">▍</span>
              </div>
            </div>
          )}
          {/* ★ 折叠式全过程小结：钉在对话流最底下 —— "不用往上翻就知道这一趟干了啥" */}
          <TaskDigest events={events} running={running} />
        </div>

        {showJumpBottom && (
          <button
            className="jump-bottom"
            title="回到底部"
            onClick={() => {
              streamRef.current?.scrollTo({ top: streamRef.current.scrollHeight, behavior: 'smooth' });
              stickBottomRef.current = true;
              setShowJumpBottom(false);
            }}
          >
            ↓
          </button>
        )}

        {asideOpen && (
          <aside className="tv-aside">
            <div className="aside-tabs">
              <button
                className={`aside-tab ${asideTab === 'plan' ? 'aside-tab-on' : ''}`}
                onClick={() => setAsideTab('plan')}
              >
                <ListTodo size={13} style={{ display: 'inline', verticalAlign: -2, marginRight: 4 }} />计划
              </button>
              <button
                className={`aside-tab ${asideTab === 'artifacts' ? 'aside-tab-on' : ''}`}
                onClick={() => setAsideTab('artifacts')}
              >
                <Package size={13} style={{ display: 'inline', verticalAlign: -2, marginRight: 4 }} />产物{artCount > 0 ? ' ' + artCount : ''}
              </button>
            </div>
            <div className="aside-body">
              {asideTab === 'plan' &&
                (plan ? (
                  <>
                    {plan.steps.map((s) => (
                      <div key={s.no} className={`plan-step step-${s.status}`}>
                        <span>
                          {s.status === 'done'
                            ? <Check size={12} style={{ display: 'inline', verticalAlign: -1 }} />
                            : s.status === 'in_progress'
                              ? <Play size={11} style={{ display: 'inline', verticalAlign: -1 }} />
                              : s.status === 'failed'
                                ? <X size={12} style={{ display: 'inline', verticalAlign: -1 }} />
                                : <Circle size={10} style={{ display: 'inline', verticalAlign: -1 }} />}
                        </span>
                        <span>
                          {s.no}. {s.text}
                        </span>
                      </div>
                    ))}
                    {plan.reflection && <div className="plan-reflection">{plan.reflection}</div>}
                  </>
                ) : (
                  <div className="aside-empty">还没有计划——任务开始后会显示步骤</div>
                ))}
              {asideTab === 'artifacts' && (
                <ArtifactPanel taskId={taskId} embedded ping={artifactPing} onCount={setArtCount} />
              )}
            </div>
          </aside>
        )}
      </div>

      {waitingApproval && (
        <div className="approval-bar">
          <div className="approval-main">
            <span className="approval-title">注意：命令等待你的批准</span>
            {pendingCommand && <code className="mono approval-cmd">{pendingCommand}</code>}
            {!pendingCallId && <span className="approval-hint">（未取到 call_id，可刷新页面后重试）</span>}
          </div>
          {/* A3（审计台账 P1）：审批是"最后一次拦得住"的地方——必须说清它批的是什么。
              沙箱关着 ⇒ 这条命令落在真实电脑上（不是容器里）。
              ★ 独立成行（不是塞进 .approval-main）：那一行已被 title+命令占满且
              父栏 overflow:hidden，塞进去会被裁掉——告知被裁就等于没有告知。 */}
          {!sandboxOn && (
            <div className="approval-host-warn">
              ⚠ 本机执行：沙箱已关闭——批准后这条命令会真实作用于你的电脑，改动不可撤销
            </div>
          )}
          <div className="approval-actions">
            <button
              className="btn-primary"
              disabled={approving || !pendingCallId}
              onClick={() => void decide('once')}
            >
              允许一次
            </button>
            <button
              className="btn-warn"
              disabled={approving || !pendingCallId}
              onClick={() => void decide('always')}
            >
              总是允许
            </button>
            <button
              className="btn-danger"
              disabled={approving || !pendingCallId}
              onClick={() => void decide('deny')}
            >
              拒绝
            </button>
          </div>
          {approveError && <div className="approval-error">审批失败：{approveError}</div>}
        </div>
      )}

      {diskWarn && (
        <div className="approval-bar">
          <div className="approval-main">
            <span className="approval-title">这个任务要看你的本机磁盘，但沙箱已开启</span>
            <span className="approval-hint">沙箱模式下 Agent 在隔离容器里，看不到 C 盘/D 盘/桌面——它跑了也白跑。想让它分析磁盘：到 设置 → 执行环境 → 把沙箱切到"关"并重启后端。纯写代码/处理工作区文件的任务不受影响。</span>
          </div>
          <div className="approval-actions">
            <button className="btn-warn" onClick={() => { const v = diskWarn; setDiskWarn(null); if (v) { setInput(v); } }}>
              去关闭沙箱后再说
            </button>
            <button className="btn-primary" onClick={() => { const v = diskWarn; setDiskWarn(null); if (v) { setInput(v); setTimeout(() => send(), 50); } }}>
              仍然继续（不推荐）
            </button>
          </div>
        </div>
      )}
      {sandboxHint && <div className="art-note" style={{ padding: '4px 10px' }}>{sandboxHint}</div>}
      {/* 二十六轮第4批第8-1【设计决定】：模型/权限弹层为 popover 语义（非模态），
          打开时 Tab 允许穿到背景任务列表——键盘用户失去上下文是已知取舍，
          非缺陷；若未来改模态需补 focus trap（参照 SettingsPanel/TeamView 的 trapTab）。 */}
      <footer
        className={`tv-composer ${dragOn ? 'tv-drag' : ''}`}
        style={asideOpen ? { marginRight: 'var(--aside-w, 300px)', transition: 'margin-right 0.18s ease' } : { transition: 'margin-right 0.18s ease' }}
        onDragOver={(e) => {
          e.preventDefault();
          setDragOn(true);
        }}
        onDragLeave={() => setDragOn(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragOn(false);
          void upload(e.dataTransfer.files);
        }}
      >
        <div className="composer-resize" onMouseDown={onResizeDown} title="拖动调整对话框高度" />
        {pending.length > 0 && (
          <div className="attach-chips">
            {pending.map((a) => (
              <span key={a.name} className="chip">
                <Paperclip size={11} style={{ display: 'inline', verticalAlign: -1, marginRight: 2 }} />{a.name}
                <button
                  className="chip-x"
                  title="移除"
                  onClick={() => setPending((p) => p.filter((x) => x.name !== a.name))}
                >
                  ×
                </button>
              </span>
            ))}
          </div>
        )}
        {uploadErr && <div className="attach-err">附件上传失败：{uploadErr}</div>}
        <input
          ref={fileRef}
          type="file"
          multiple
          style={{ display: 'none' }}
          onChange={(e) => {
            void upload(e.target.files);
            e.target.value = '';
          }}
        />
        <textarea
          value={input}
          style={{ height: composerH }}
          placeholder={
            waitingApproval
              ? '有命令待审批——请点上方按钮（在这里打字不会批准命令）'
              : takeOverPaused
              ? '人工接管中——操作完成后在这里描述你做了什么，Agent 会带着这个上下文继续…'
              : running
              ? '运行中——可追加指令，下轮迭代可见'
              : '追加任务 / 追问…（Enter 发送 · 按住右 Ctrl 说话）'
          }
          onChange={(e) => {
              const v = e.target.value;
              setInput(v);
              if (v === '/') {
                void api.listSkills().then((ss) => {
                  setSkills(ss);
                  setSlashOpen(true);
                });
              } else if (!v.startsWith('/')) {
                setSlashOpen(false);
              }
            }}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              send();
            }
          }}
        />
        {slashOpen && (
          <div className="slash-menu">
            {skills.map((s) => (
              <button
                key={s.name}
                className="slash-item"
                onClick={() => {
                  setInput(`/${s.name} `);
                  setSlashOpen(false);
                }}
              >
                <b>/{s.name}</b>
                <span className="slash-desc">{s.description}</span>
              </button>
            ))}
          </div>
        )}
        <div className="composer-controls">
          <button className="ctl-btn" title="添加附件" onClick={() => fileRef.current?.click()}>
            ＋
          </button>

          <div className="ctl-wrap">
            {/* 十七轮 🔴3：状态词/盾标/箭头拆成独立元素——窄档截断只裁状态词
                （css ellipsis），盾标窄档隐藏、▾ 恒可见（此前 rtl 反排把 ▾ 裁丢）；
                身份下拉加 title（十七轮 🟠4：窄档截断后长角色名可悬停看全） */}
            <button className="ctl-btn ctl-mode" onClick={() => { setUsageOpen(false); setModeOpen((v) => !v); }} title={sandboxOn ? '自动编辑·沙箱：文件操作自动；危险命令弹审批；容器沙箱隔离' : undefined}>
              {sandboxOn && <span className="ctl-shield">🛡 </span>}
              <span className="ctl-mode-label">{accessMode === 'full' ? '完全访问' : accessMode === 'confirm' ? '变更前确认' : sandboxOn ? '自动编辑·沙箱' : '自动编辑'}</span>
              <span className="ctl-caret">▾</span>
            </button>
            {/* ★★ 2026-10-07「按角色限权」（用户点名要的："测试只能看/跑 ✗ 不能改 ✓"）：
                身份选择器上**选之前就标出来**哪些角色是只读的 ✓
                ——选完才发现"怎么改不了" = 用户以为界面坏了 ✗（本项目栽过多次这种"事后才知道"✓） */}
            <select
              className="task-search"
              style={{ width: 'auto', padding: '5px 8px', fontSize: 11.5 }}
              value={identity}
              title="Agent 身份：选择后立即以该专家身份执行（切换回默认助手立即恢复；窄档下长角色名可悬停查看）。带 🔒 的角色是「只能看/只能跑」：不能改已有文件、不能删/移、不能装包 —— 但仍然可以读、可以跑测试、可以新建文件。"
              onChange={(e) => {
                const v = e.target.value;
                setIdentity(v);
                void api.setTaskIdentity(task.id, v).catch(() => setDraftHint('身份切换失败，请重试'));
              }}
            >
              <option value="">🧑‍💻 默认助手（不受限）</option>
              {roleList.map((r) => (
                <option key={r.role} value={r.role}>
                  {(r as { can_write?: boolean }).can_write === false ? '🔒 ' : ''}{r.role}（{r.dept}）
                </option>
              ))}
            </select>
            {identity && roleList.find((r) => r.role === identity && (r as { can_write?: boolean }).can_write === false) && (
              <span className="art-note" style={{ padding: '0 6px' }} title="这是角色限权：不管权限模式调成什么，这个身份都不能改已有的东西">
                🔒 只读角色
              </span>
            )}
            {/* ★★ 2026-10-07（第 8 项）**按任务类型推荐角色** ✓ ——
                24 个角色摆在那儿，新用户**不知道该派谁** ✓（"我这句话该用哪个身份？"卡住 ✓）
                ★ 只在**命中关键词**时才显示 ✓ 匹配不上**一个字都不显示** ✓
                  （乱推比不推更烦人 ✓ 用户会开始无视这块提示 ✓ 那就等于没有 ✓）
                ★ 每条都写出**为什么** ✓（"因为你提到了「测试」"✓ 用户才知道该不该听 ✓）*/}
            {!identity && roleTip && (
              <span className="art-note" style={{ padding: '0 6px' }} title={roleTip.why}>
                💡 建议用「{roleTip.role}」
                <button
                  className="btn-mini" style={{ marginLeft: 6, padding: '0 6px' }}
                  title={`${roleTip.why} —— 点一下就切过去`}
                  onClick={() => {
                    setIdentity(roleTip.role);
                    void api.setTaskIdentity(task.id, roleTip.role).catch(() => setDraftHint('身份切换失败，请重试'));
                  }}
                >用这个</button>
              </span>
            )}
            {modeOpen && (
              <div className="ctl-pop">
                <button className={'ctl-item' + (accessMode === 'full' ? ' ctl-on' : '')} onClick={() => { void api.setAccessMode('full').then(() => { setAccessMode('full'); setModeOpen(false); }); }}>
                  <Zap size={12} style={{ display: 'inline', verticalAlign: -2, marginRight: 4 }} />完全访问<span>所有命令直接执行，不弹审批</span>
                </button>
                <button className={'ctl-item' + (accessMode === 'auto_edit' ? ' ctl-on' : '')} onClick={() => { void api.setAccessMode('auto_edit').then(() => { setAccessMode('auto_edit'); setModeOpen(false); }); }}>
                  <Shield size={12} style={{ display: 'inline', verticalAlign: -2, marginRight: 4 }} />自动编辑<span>文件操作自动；危险命令弹审批</span>
                </button>
                <button className={'ctl-item' + (accessMode === 'confirm' ? ' ctl-on' : '')} onClick={() => { void api.setAccessMode('confirm').then(() => { setAccessMode('confirm'); setModeOpen(false); }); }}>
                  变更前确认<span>所有命令都要先批准</span>
                </button>
                <div style={{ borderTop: '1px solid var(--border)', margin: '4px 0' }} />
                <button className="ctl-item" onClick={async () => {
                  const next = !sandboxOn;
                  const msg = next
                    ? '开启沙箱 = 给 Agent 一个隔离的虚拟工作间：它的操作弄不坏你的电脑，但也看不到你电脑上的任何文件（C盘/D盘/桌面不可见）。磁盘分析、文件整理类任务将无法执行。确定开启？'
                    : '关闭沙箱 = Agent 直接在你的真实电脑上工作：可以访问授权的磁盘和文件夹，危险命令仍会先请求批准。确定关闭？';
                  if (!confirm(msg)) return;
                  // ★ 2026-10-07：裸 fetch → api.*（少一处"得自己记着带访问密码"的地方 ✓）
                  await api.setExecutor({ sandbox: next ? 'docker' : 'off' });
                  setSandboxOn(next);
                  setSandboxHint(next ? '沙箱已开启（重启后端后对新任务完全生效）' : '沙箱已关闭（重启后端后对新任务生效）');
                  setModeOpen(false);
                }}>
                  {sandboxOn ? <Shield size={12} style={{ display: 'inline', verticalAlign: -2, marginRight: 4 }} /> : null}
                  {sandboxOn ? '关闭沙箱' : '开启沙箱'}<span>{sandboxOn ? '恢复真实电脑访问（可分析磁盘）' : '隔离执行，弄不坏电脑但看不到本机文件'}</span>
                </button>
              </div>
            )}
          </div>

          {/* 第 48 班：模型切换器与首页统一为同款组件（此前弹层显示原始模型 ID、
              首页显示友好名——用户反馈"为什么是不一样的"）。onOpen 时顺手关掉
              权限弹层，保持"同时只开一个弹层"的原行为。 */}
          <ModelPicker
            current={modelName}
            onChanged={setModelName}
            onOpen={() => { setModeOpen(false); setUsageOpen(false); }}
          />

          {!draftOn && (meetingHint || draftHint) && (
            <div className="art-note" style={{ padding: '2px 10px' }}>
              {meetingOn && meetingHint ? `会议 · ${meetingHint}` : (meetingHint || draftHint)}
            </div>
          )}

          {/* ★ 2026-10-08：朗读退回了别的引擎 ⇒ 在这儿说一句 ✓（`title` 里是完整原因 ✓）*/}
          {ttsNote && (
            <div className="art-note" style={{ padding: '2px 10px', color: '#f0a05a' }} title={ttsWhy}>
              {ttsNote}
            </div>
          )}

          <button
            className={`voice-btn ${draftOn ? 'voice-on' : ''}`}
            aria-label="语音输入"
            onClick={() => void toggleDraft()}
            title={draftOn ? '停止并填入输入框（发送前可检查修改）' : '语音输入：点一下开始、再点一下停止；也可以按住右 Ctrl 说话，松开自动转写'}
          >
            {draftOn ? <Square size={13} /> : <Mic size={14} />}
          </button>

          {meetingOn && (
            <div className="meeting-bar">
              <span className="meeting-dot" />
              <span>会议记录中</span>
            </div>
          )}

          {/* 十七轮 🔴1：meeting+cluster 捆成【尾组】（不可拆）——此前簇独自换行时
              所在行只有 gain+send（审查判为独占）。尾组保证：send 所在行
              至少有 meeting+gain+send 三个按钮（成员数 ≥3 达标——十九轮口径更正：旧 ≥2 判据在修复前也通过=假断言）。 */}
          <span className="tail-group">
            <button
              className={`voice-btn ${meetingOn ? 'voice-on' : ''}`}
              onClick={() => void toggleMeeting()}
              title={meetingOn ? '停止会议记录并生成纪要' : '开始会议记录（分片转写，结束后可让 Agent 生成纪要）'}
            >
              {meetingOn ? <Square size={13} /> : <Radio size={14} />}
            </button>

            {/* 十六轮④：gain+send 捆成不可拆簇——此前 gain 留上行、send 独自换行
                （独占带的真根因）；捆绑后要么同排、要么一起换行 */}
            <span className="send-cluster">
              <button
                className={`gain-btn ${gainOn ? 'gain-on' : ''}`}
                onClick={() => setGainOn((v) => !v)}
                title={gainOn ? '收音增强已开（麦克风信号放大 2.5 倍，远一点也能收清；底噪会同步放大）——点击改用原声' : '当前为原声——点击开启收音增强（远场收音）'}
              >
                {gainOn ? <Volume2 size={13} /> : <VolumeX size={13} />}
              </button>

              <button className="send-btn" disabled={!input.trim() && pending.length === 0} onClick={send} title={takeOverPaused ? '交还并继续' : '发送'} aria-label="发送消息">
                <ArrowUp size={16} />
              </button>
            </span>
          </span>
        </div>
      </footer>

      {usage && usageFull && (() => {
        const cachedTok = usageFull.cached_tokens ?? 0;
        return (
        <>
        <div className={`usage-badge ${usageOpen ? 'usage-open' : ''}`} onClick={() => { setModeOpen(false); setUsageOpen((v) => !v); }}>
          {!usageOpen && <span>Σ {usage.total_tokens.toLocaleString()} tok</span>}
          {usageOpen && (
            <div className="usage-pop">
              <div className="usage-pop-head">累计用量统计</div>
              <div className="usage-row"><span>输入</span><b>{usageFull.input_tokens.toLocaleString()} tok</b></div>
              <div className="usage-row"><span>输出</span><b>{usageFull.output_tokens.toLocaleString()} tok</b></div>
              {(cachedTok) > 0 && (
                <div className="usage-row" title="前缀缓存命中部分——服务商按折扣价计费，输入的大头在这里省下来了">
                  <span>└ 缓存命中</span>
                  <b className="ok">{cachedTok.toLocaleString()} tok（{Math.round(cachedTok / Math.max(1, usageFull.input_tokens) * 100)}%）</b>
                </div>
              )}
              <div className="usage-row"><span>总计</span><b>{usageFull.total_tokens.toLocaleString()} tok</b></div>
              <div className="usage-row"><span>LLM 调用</span><b>{usageFull.calls.toLocaleString()} 次</b></div>
              <div className="usage-row"><span>覆盖任务</span><b>{usageFull.tasks_with_usage} / {usageFull.task_count}</b></div>
              {Object.keys(usageFull.by_model).length > 0 && (
                <>
                  <div className="usage-pop-head" style={{ marginTop: 6 }}>按模型</div>
                  {Object.entries(usageFull.by_model).map(([m, v]) => (
                    <div key={m} className="usage-row">
                      <span className="mono" style={{ fontSize: 10.5 }}>{m}</span>
                      <b>{(v.input + v.output).toLocaleString()} tok · {v.calls} 次</b>
                    </div>
                  ))}
                </>
              )}
              {usageFull.by_task && usageFull.by_task.length > 0 && (
                <>
                  <div className="usage-pop-head" style={{ marginTop: 6 }}>Top 消耗任务</div>
                  {usageFull.by_task.slice(0, 8).map((t) => (
                    <div key={t.task_id} className="usage-row" title={t.label}>
                      <span style={{ fontSize: 10.5, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 150 }}>
                        {t.label}
                      </span>
                      <b style={{ whiteSpace: 'nowrap' }}>{t.total_tokens.toLocaleString()} tok</b>
                    </div>
                  ))}
                </>
              )}
            </div>
          )}
        </div>
        </>
        );
      })()}
    </main>
  );
}
