# 第 4 步 · 复核 B 的两个产物 + 定量比对
# ① 文档里那条命令**原样照跑** → music_with_clicks_literal.wav（照抄 SKILL.md 第 4 步 / 卡点与音频.md §3.2）
# ② 文档括号里说的「更严格的做法：按 beats.json 的每个时间点用 adelay 逐条摆 click」
#    → music_with_clicks.wav（并修正两处会让它不可用的地方，见输出里的 DIAG）
# 量测法：把成品减去「同一增益下的原曲」= 残差，残差里能数出每一响 click 的时刻与电平。
import json, os, subprocess
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FF = r'D:\MiniMax Design\current\resources\ffmpeg\ffmpeg.exe'
M = os.path.join(ROOT, 'assets', 'audio', 'music')
MUSIC = os.path.join(M, 'bgm.wav')
LIT = os.path.join(M, 'music_with_clicks_literal.wav')
STRICT = os.path.join(M, 'music_with_clicks.wav')
SR = 48000
MUSIC_DB = -5.0            # 卡点与音频.md §9.1 混音链：music -5 dB -> sidechaincompress

def run(argv, label):
    r = subprocess.run(argv, capture_output=True, text=True, encoding='utf-8', errors='replace')
    print(f'$ {label}\n  exit={r.returncode}')
    return r.returncode

def dec(p):
    raw = subprocess.run([FF, '-v', 'error', '-i', p, '-ac', '2', '-ar', str(SR), '-f', 'f32le', '-'],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.float32).reshape(-1, 2)

beats = json.load(open(os.path.join(ROOT, 'data', 'beats.json'), encoding='utf-8'))['beats']
N = len(beats)

# ---------- ① 文档原样命令（一字不改） ----------
lit_exit = run([FF, '-y', '-i', MUSIC, '-f', 'lavfi', '-i', 'sine=frequency=1500:duration=0.03',
                '-filter_complex', '[1:a]volume=0.6[click];[0:a][click]amix=inputs=2:normalize=0[out]',
                '-map', '[out]', LIT],
               'ffmpeg -y -i bgm.wav -f lavfi -i "sine=frequency=1500:duration=0.03" -filter_complex "[1:a]volume=0.6[click];[0:a][click]amix=inputs=2:normalize=0[out]" -map "[out]" music_with_clicks_literal.wav')

# ---------- ② 逐拍 adelay（严格做法）+ 两处必要修正 ----------
# 修正 A：音乐先 -5 dB（文档自己混音链的电平），否则 float 峰值 1.54 写 s16 会削波
# 修正 B：lavfi sine 的默认振幅是 0.125（实测 max_volume -18.1 dB），volume=0.6 → 0.075 ≈ -22.5 dBFS，
#         埋在音乐下面根本听不见；要当踩点提示音必须放大。
CLICK_VOL = 3.0
parts = [f'[1:a]asplit={N}' + ''.join(f'[c{i}]' for i in range(N))]
for i, b in enumerate(beats):
    parts.append(f'[c{i}]adelay=delays={round(b * 1000)}:all=1,volume={CLICK_VOL}[k{i}]')
graph = (f'[0:a]volume={MUSIC_DB}dB,aformat=channel_layouts=stereo[mus];' + ';'.join(parts) + ';' +
         '[mus]' + ''.join(f'[k{i}]' for i in range(N)) + f'amix=inputs={N + 1}:normalize=0[out]')
gfile = os.path.join(M, 'click-graph.txt')
open(gfile, 'w', encoding='utf-8').write(graph)
print(f'  filter graph: {os.path.relpath(gfile, ROOT)}  ({len(graph)} chars, {N} 响 click, click volume={CLICK_VOL})')
strict_exit = run([FF, '-y', '-i', MUSIC, '-f', 'lavfi', '-i', 'sine=frequency=1500:duration=0.03',
                   '-filter_complex_script', gfile, '-map', '[out]', '-c:a', 'pcm_f32le', STRICT],
                  'ffmpeg -y -i bgm.wav -f lavfi -i "sine=..." -filter_complex_script click-graph.txt -map "[out]" -c:a pcm_f32le music_with_clicks.wav')

# ---------- 量测 ----------
def click_positions(path, ref):
    """残差里数 click：正弦每半周期过一次零 → 按 50 ms 间隔合并碎片，一个「响」= 一组。"""
    y = dec(path)
    n = min(len(ref), len(y))
    r = np.abs(y[:n] - ref[:n]).max(axis=1)      # 残差（取两声道较大者）
    thr = 0.02
    spans, i = [], 0
    while i < n:
        if r[i] > thr:
            j = i
            while j < n and r[j] > thr: j += 1
            if spans and (i - spans[-1][1]) / SR < 0.05:      # 与上一组间隔 <50 ms → 同一响
                spans[-1][1] = j
                spans[-1][2] = max(spans[-1][2], float(r[i:j].max()))
            else:
                spans.append([i, j, float(r[i:j].max())])
            i = j
        else:
            i += 1
    return [(round(a / SR, 3), round(pk, 4)) for a, b, pk in spans]

music = dec(MUSIC)[: int(10 * SR)]
music_5db = music * (10 ** (MUSIC_DB / 20))

print('\n===== 产物 ① 文档原命令（一字不改）=====')
print(f'exit={lit_exit}  文件：music_with_clicks_literal.wav')
h1 = click_positions(LIT, music)   # 该命令没加 -5 dB，参考取原曲
print(f'  残差里能数出的 click 响数：**{len(h1)}**')
for t, pk in h1[:8]:
    print(f'    click @ {t:.3f}s  峰值 {pk:.4f}（{20 * np.log10(pk + 1e-9):.1f} dBFS）')
print('  → 判据「提示音必须每一响都落在鼓点上」**无从谈起**：这条命令只产生 1 响（t≈0），不是节拍音轨。')

print('\n===== 产物 ② 逐拍 adelay（严格做法）=====')
print(f'exit={strict_exit}  文件：music_with_clicks.wav')
h2 = click_positions(STRICT, music_5db)
print(f'  残差里能数出的 click 响数：**{len(h2)}**（设计 {N} 响）')
ok = 0
print('    #  设计拍点(s)  实测 click(s)  偏差(ms)  峰值  峰值dBFS')
for i, (t, pk) in enumerate(h2[:N + 3]):
    exp = beats[i] if i < N else None
    d = f'{(t - exp) * 1000:+.0f}' if exp is not None else '-'
    if exp is not None and abs(t - exp) < 0.005: ok += 1
    print(f'    {i:>3}  {exp if exp is not None else "-":>11}  {t:>13.3f}  {d:>8}  {pk:.4f}  {20 * np.log10(pk + 1e-9):.1f}')
print(f'  与设计拍点吻合（<5 ms）的 click：**{ok}/{N}**')
print(f'\nliteral_exit={lit_exit} strict_exit={strict_exit}')
