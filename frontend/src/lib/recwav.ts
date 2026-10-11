/**
 * ★ 浏览器侧直接录成 **16k 单声道 WAV**（2026-10-05 用户报"语音输入根本不好使"的根治）。
 *
 * 为什么必须这么做（真实现场）：
 *   · `MediaRecorder` 默认产 **webm/opus**，而 ASR 网关只收 wav/mp3
 *   · 后端 `_to_wav()` 本该用 **ffmpeg** 转码 —— 但 ffmpeg **不在 PATH**（干净机器都不会有），
 *     转换失败后它**静默退回原文件** ⇒ 网关 400 `Param Incorrect` ⇒ 用户看到"转写失败"
 *   · 所以"录音→转写"这条路**不能依赖外部程序**：在浏览器里把 PCM 采下来、自己写 WAV 头，
 *     任何机器上都成立（也不用户装任何东西）。
 *
 * 实现用 `ScriptProcessorNode`（虽然被标记废弃，但所有浏览器都还在支持、且不需要额外的
 * worklet 模块文件）；采集 Float32 → 转 Int16 → 加 44 字节 WAV 头。
 */

export type WavRecorder = {
  /** 停止采集，返回 WAV Blob（16k / 单声道 / 16bit）。 */
  stop: () => Promise<Blob>;
  /** 已经录到的采样点数（"有没有声音"的粗略判据）。 */
  samples: () => number;
};

export async function startWavRecording(
  opts: { gain?: number; stream?: MediaStream } = {},
): Promise<WavRecorder> {
  // ★ 2026-10-07（会议录音复用同一条麦克风）：外部给了 stream 就用它 ✓
  //   而且**结束时不关它的轨道** ✗ —— 那条流是调用方的（会议还接着录下一段 ✓），
  //   谁开的谁关 ✓（本文件自己开的才自己关 ✓）。
  const own = !opts.stream;
  const raw = opts.stream ?? await navigator.mediaDevices.getUserMedia({ audio: true });
  const ctx = new AudioContext({ sampleRate: 16000 });   // 直接按 16k 采，省一次重采样
  await ctx.resume().catch(() => undefined);
  const src = ctx.createMediaStreamSource(raw);
  const gainNode = ctx.createGain();
  gainNode.gain.value = opts.gain ?? 1;                  // 远场收音时用 >1 放大
  const proc = ctx.createScriptProcessor(4096, 1, 1);
  const chunks: Float32Array[] = [];
  proc.onaudioprocess = (e) => {
    // 必须 copy：复用同一个 buffer，直接 push 会被下一次覆盖
    chunks.push(new Float32Array(e.inputBuffer.getChannelData(0)));
  };
  src.connect(gainNode);
  gainNode.connect(proc);
  proc.connect(ctx.destination);                          // 某些浏览器不接 destination 不触发回调

  const cleanup = (): void => {
    try { proc.disconnect(); } catch { /* ignore */ }
    try { gainNode.disconnect(); } catch { /* ignore */ }
    try { src.disconnect(); } catch { /* ignore */ }
    if (own) raw.getTracks().forEach((t) => t.stop());    // 只有自己开的流才关 ✓
    void ctx.close().catch(() => undefined);
  };

  return {
    samples: () => chunks.reduce((n, c) => n + c.length, 0),
    stop: async () => {
      cleanup();
      const total = chunks.reduce((n, c) => n + c.length, 0);
      const pcm = new Int16Array(total);
      let at = 0;
      for (const c of chunks) {
        for (let i = 0; i < c.length; i += 1) {
          const v = Math.max(-1, Math.min(1, c[i]));
          pcm[at] = v < 0 ? v * 0x8000 : v * 0x7fff;   // 转 16bit
          at += 1;
        }
      }
      return encodeWav(pcm, ctx.sampleRate || 16000);
    },
  };
}

/** Int16 PCM → WAV（44 字节标准头）。 */
export function encodeWav(pcm: Int16Array, sampleRate: number): Blob {
  const header = new ArrayBuffer(44);
  const dv = new DataView(header);
  const writeStr = (off: number, s: string): void => {
    for (let i = 0; i < s.length; i += 1) dv.setUint8(off + i, s.charCodeAt(i));
  };
  writeStr(0, 'RIFF');
  dv.setUint32(4, 36 + pcm.byteLength, true);
  writeStr(8, 'WAVE');
  writeStr(12, 'fmt ');
  dv.setUint32(16, 16, true);          // fmt 块长度
  dv.setUint16(20, 1, true);           // PCM
  dv.setUint16(22, 1, true);           // 单声道
  dv.setUint32(24, sampleRate, true);
  dv.setUint32(28, sampleRate * 2, true);   // 字节率 = 采样率 × 声道 × 位深/8
  dv.setUint16(32, 2, true);           // 块对齐
  dv.setUint16(34, 16, true);          // 位深
  writeStr(36, 'data');
  dv.setUint32(40, pcm.byteLength, true);
  return new Blob([header, pcm.buffer as ArrayBuffer], { type: 'audio/wav' });
}
