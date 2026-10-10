/**
 * ★ 语音输入的唯一实现（2026-10-05 用户报"语音输入根本不好使"的根治）。
 *
 * 为什么要有这个 hook：此前**首页输入框（Hero）**和**任务内输入框（TaskView）各写了一份**，
 * 我修了后者、前者照旧 —— 而用户日常用的正是首页那个"在输入框下方说话"。
 * 一份实现、两处调用，才不会再出现"修了一处、另一处照旧"。
 *
 * 这一段同时兜住三个坑：
 *   ① 录音格式：浏览器 `MediaRecorder` 默认产 webm，而云端 ASR 网关**只收 wav/mp3**；
 *      后端本该用 ffmpeg 转码，但 ffmpeg 不在 PATH（干净机器都没有）⇒ 必须**浏览器侧直接录 WAV**
 *   ② 上传文件名：以前写死 `draft.webm`，后端照后缀就把 wav 当 webm 拒掉 ⇒ 按真实类型命名
 *   ③ 快捷键：用户要求"空格键或什么快捷方式" ⇒ **按住 Ctrl+空格 说话、松开自动转写**
 *      （Ctrl+Space 不会和输入框里的空格打字冲突；macOS 的 ⌘Space 被系统占用，所以用 Ctrl）
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../api';
import { startWavRecording, type WavRecorder } from './recwav';

type Fallback = { stream: MediaStream; rec: MediaRecorder; chunks: Blob[] };

export type VoiceDraft = {
  /** 正在录音 */
  on: boolean;
  /** 正在转写 */
  busy: boolean;
  /** 给用户看的一行提示（"" = 不显示） */
  hint: string;
  setHint: (s: string) => void;
  /** 点一下开始 / 再点一下停止并转写 */
  toggle: () => void;
};

export function useVoiceDraft(opts: {
  /** 转写结果往哪儿填（调用方决定拼在草稿前还是后） */
  onText: (text: string) => void;
  /** 收音增强倍数（远场用 >1） */
  gain?: number;
  /** 快捷键是否生效（两个输入框可能同时挂载，用它避免两边一起录） */
  hotkeyEnabled?: boolean;
}): VoiceDraft {
  const { onText, gain = 2.5, hotkeyEnabled = true } = opts;
  const [on, setOn] = useState(false);
  const [busy, setBusy] = useState(false);
  const [hint, setHint] = useState('');
  const wavRef = useRef<WavRecorder | null>(null);
  const fallbackRef = useRef<Fallback | null>(null);
  const onTextRef = useRef(onText);
  onTextRef.current = onText;              // 回调每次渲染都新，但录音期间只认最新那个

  const stop = useCallback(async (): Promise<void> => {
    const wav = wavRef.current;
    const fb = fallbackRef.current;
    if (!wav && !fb) return;
    wavRef.current = null;
    fallbackRef.current = null;
    setOn(false);
    setBusy(true);
    setHint('转写中…');
    let blob: Blob;
    try {
      if (wav) {
        blob = await wav.stop();
      } else if (fb) {
        blob = await new Promise<Blob>((resolve) => {
          fb.rec.onstop = () => resolve(new Blob(fb.chunks, { type: 'audio/webm' }));
          if (fb.rec.state !== 'inactive') fb.rec.stop();
          else resolve(new Blob(fb.chunks, { type: 'audio/webm' }));
        });
        fb.stream.getTracks().forEach((t) => t.stop());
      } else {
        return;
      }
      if (blob.size < 2048) {
        setHint('（录音太短没听清，说完一整句再停）');
        return;
      }
      const r = await api.voiceTranscribe(blob);
      if (r.text) {
        onTextRef.current(r.text);
        setHint('');
      } else {
        setHint('（没听清，再试一次）');
      }
    } catch (e) {
      setHint(`（转写失败：${e instanceof Error ? e.message : String(e)}）`);
    } finally {
      setBusy(false);
    }
  }, []);

  const start = useCallback(async (): Promise<void> => {
    if (wavRef.current || fallbackRef.current) return;
    setHint('');
    try {
      // 首选：浏览器侧直接录 WAV（不依赖 ffmpeg）
      wavRef.current = await startWavRecording({ gain });
      setOn(true);
      return;
    } catch (e) {
      console.warn('[voice] 内置 WAV 录音不可用，改用 MediaRecorder 备用路径：', e);
      wavRef.current = null;
    }
    try {
      // 备用：老路（后端有 ffmpeg 时也能成）
      const raw = await navigator.mediaDevices.getUserMedia({ audio: true });
      const ctx = new AudioContext();
      await ctx.resume().catch(() => undefined);
      const src = ctx.createMediaStreamSource(raw);
      const g = ctx.createGain();
      g.gain.value = gain;
      const dst = ctx.createMediaStreamDestination();
      src.connect(g);
      g.connect(dst);
      const rec = new MediaRecorder(dst.stream);
      const chunks: Blob[] = [];
      rec.ondataavailable = (ev) => { if (ev.data.size > 0) chunks.push(ev.data); };
      rec.start();
      fallbackRef.current = { stream: raw, rec, chunks };
      setOn(true);
    } catch {
      setHint('（麦克风不可用：请检查系统麦克风权限）');
    }
  }, [gain]);

  const toggle = useCallback((): void => {
    if (wavRef.current || fallbackRef.current) void stop();
    else void start();
  }, [start, stop]);

  // ★ 快捷键：**按住右 Ctrl 说话**，松开自动停止并转写。
  //
  //   为什么不用 Ctrl+空格（第一版）：中文 Windows 上 **Ctrl+空格 是切换输入法的系统键**，
  //   会被 IME 抢走 —— 用户实测"不好用"。右 Ctrl 不参与打字、不被 IME 占用、也不弹系统菜单，
  //   是 push-to-talk 的常规选择（Ctrl+空格 仍然保留为备选，能用就用）。
  useEffect(() => {
    if (!hotkeyEnabled) return undefined;
    let holding = false;
    const isStart = (e: KeyboardEvent): boolean =>
      e.code === 'ControlRight' || (e.ctrlKey && (e.code === 'Space' || e.key === ' '));
    const isEnd = (e: KeyboardEvent): boolean =>
      e.code === 'ControlRight' || e.code === 'Space' || e.key === ' ';
    const down = (e: KeyboardEvent): void => {
      if (!isStart(e) || e.repeat || holding) return;
      e.preventDefault();                 // 别让空格落进输入框
      holding = true;
      void start();
    };
    const up = (e: KeyboardEvent): void => {
      if (!holding || !isEnd(e)) return;
      holding = false;
      void stop();
    };
    window.addEventListener('keydown', down);
    window.addEventListener('keyup', up);
    return () => {
      window.removeEventListener('keydown', down);
      window.removeEventListener('keyup', up);
    };
  }, [hotkeyEnabled, start, stop]);

  return { on, busy, hint, setHint, toggle };
}
