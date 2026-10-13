---
name: 卡点视频
description: 把中文脚本做成卡点（beat-synced）短视频的完整操作手册——节拍检测（机器初稿 + 强制人工复核）→ edge-tts 词级时间戳 → 单一 TIMING 时间源 → HTML/Canvas 逐帧画 → Playwright 逐帧截图 → ffmpeg 混音合成 → 渲完自检与返工判定。用户要「卡点视频 / 混剪 / 分镜动效 / 带字幕的短视频」时用本技能。先照 `references/时长档位表.md` 按目标秒数选档位与场景数，别自己拍脑袋定。
---

# 卡点视频制作技能

> **这是操作手册，不是调研报告。** 每一步写成「做什么 → 输入 → 输出 → 验收标准」。
> 完成条件（照抄参考项目 SKILL.md 措辞）：**只有全部可测量检查通过才算完成；报告测出来的数字，不要报告印象。**
> 材料里标「未验证 / 推断」的，本文一律保留原标记，**没有改写成肯定句**。

---

## 0. 三条前置铁律（违反任何一条，后面全白做）

1. **ffmpeg 只用 `/d/MiniMax Design/current/resources/ffmpeg\ffmpeg.exe`（6.1.1，本机实测 `xfade` + `drawtext` 都通过）**，ffprobe 用同目录 `ffprobe.exe`。
   - ⛔ `D:\Program Files\EVCapture\ffmpeg.exe`：2019 构建，**缺 `xfade`**，且**不支持 `-/filter_complex <文件>` 语法**。做转场或混音会直接失败。
   - ⛔ `D:\JianyingPro\...\ffmpeg.exe`：**缺 `drawtext`**，烧不了字幕。
   - 备选：`D:\Software\GPT-SoVITS\...\runtime\ffmpeg.exe`（4.3.2，xfade / drawtext 都有）。
2. **视频类任务必须「本机执行」。** shell 沙箱若开着，命令跑在 Docker 容器里——**容器内没有 ffmpeg、没有网络、装不了库**。
3. **节拍检测只给初稿，绝不允许自动通过。** 本机实测该环节会判错：120 BPM 点轨被识别成 **181.82 BPM**（八度错误），100 BPM 音乐床被判 **93.40 BPM** 且漂移 **106 ms**。第 4 步的复核是硬门。

---

## 1. 开工前 60 秒自检

> ⛔ **绝对不要"扫全盘"找东西**（2026-10-13 真机事故：这条让用户风扇转了一小时）
>
> ```bash
> find / -maxdepth 6 -name tools -type d        # ✗✗ 禁止！从根目录扫 C 盘 + D 盘
> find / -maxdepth 6 -type d -name 卡点视频     # ✗✗ 同上
> grep -r / ; ls -R / ; dir /s                  # ✗✗ 同理
> ```
>
> **实测代价**：两个这样的 `find` 从 `/` 起扫，**各吃满一个 CPU 核、烧掉 63 分钟和 35 分钟
> CPU 时间、持续疯狂读磁盘 ⇒ 用户的风扇一直转**；而且它们**永远跑不完**（父进程都退出了
> 它们还在扫）✗
>
> **要用的东西都在已知位置**，直接去那儿拿，别搜：
>
> | 要找什么 | 直接在哪 |
> |---|---|
> | **技能自带的脚本** | **本技能包的 `tools/` 目录**（清单见 `references/脚本位置与跑法.md` —— 就在你的任务工作区能看到的那个 `tools/`）|
> | **正确的 ffmpeg** | `/d/MiniMax Design/current/resources/ffmpeg`（PATH 上那个是残版；跑 `python tools/ffmpeg_path.py` 会告诉你用哪个）|
> | **Python 解释器** | `/d/AI/agent-shell/backend/.venv/Scripts/python.exe`（开发仓，装了 scipy/edge-tts）或当前工作区里能跑通的那个 |
> | **工作区里的文件** | **只在你自己的工作目录里找**：`ls` / `find . -maxdepth 3`（**`.` 开头，不是 `/`**）|
>
> **真要搜，也必须限定在自己的工作区**：`find . -maxdepth 3 -name xxx` —— **永远别用 `/` 或盘符开头** ✓

> ★★ **本应用执行命令用的是 git bash**（实测 `bash 5.2.37`）——
> **下面一律用 bash 语法**。PowerShell 的 `$env:PATH = ...` 在这里会报
> `:PATH: command not found`，**照做反而掉坑**（真机 task_20261013_6a006c3d 就这么卡住的）✗

```bash
# ★ 正确的 ffmpeg 在 /d/MiniMax Design（带 libx264 = 2）
#   PATH 上那个是 GPT-SoVITS 里的残版（libx264 = 0，编不了 H.264）⇒ **必须前置它**
#   ★ 路径含空格 ⇒ 引号不能省
export PATH="/d/MiniMax Design/current/resources/ffmpeg:$PATH"

FF="/d/MiniMax Design/current/resources/ffmpeg/ffmpeg.exe"
FP="/d/MiniMax Design/current/resources/ffmpeg/ffprobe.exe"
PY="/d/AI/agent-shell/backend/.venv/Scripts/python.exe"   # 唯一装了 scipy / edge-tts 的解释器
export CHROME_PATH="/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
```

**一句话版**（懒人用法）：`export PATH="/d/MiniMax Design/current/resources/ffmpeg:$PATH"` ——
**这行必须和你真正要跑的命令写在同一条命令里**（环境变量只影响当前 shell，
下一个 `shell_exec` 是新进程、拿不到）✓

**本机是哪个 ffmpeg 可用？跑一下就知道**（不必猜）：

```bash
"$PY" tools/ffmpeg_path.py      # 打印候选、判定、以及两种 shell 的可复制命令
```

| # | 检查 | 命令（bash） | 期望 |
|---|---|---|---|
| 1 | ffmpeg 版本 | `"$FF" -version` | `ffmpeg version 6.1.1` |
| 2 | **libx264 在不在** | `"$FF" -hide_banner -encoders \| grep -c libx264` | **≥1**（0 = 残版，换个 ffmpeg）|
| 3 | xfade 存在 | `"$FF" -filters \| grep ' xfade '` | 有 `xfade  VV->V` |
| 4 | ffprobe 存在 | `test -x "$FP" && echo OK` | `OK` |
| 5 | 中文字体 | `test -f /c/Windows/Fonts/msyhbd.ttc && echo OK` | `OK` |
| 6 | 浏览器 | `test -x "$CHROME_PATH" && echo OK` | `OK`（本机**只有 Edge，没有 Chrome**）|
| 7 | Python + scipy | `"$PY" -c "import scipy,numpy;print(scipy.__version__)"` | 打印版本号（系统 Python 3.13 **没装 scipy**）|

**必须显式 `fontfile=`**：四个 ffmpeg 构建在 `drawtext` 时都会打印 `Fontconfig error: Cannot load default config file`。本机实测复现——不带 `fontfile=` 时中文「中文测试」渲染成 **4 个空豆腐块**（433 字节 PNG），带 `fontfile='C\:/Windows/Fonts/msyhbd.ttc'` 才正常（2781 字节）。**烧中文字幕一律显式指定字体。**

**★ 用哪个模型（用户 2026-10-12 明确）**：**哪个便宜用哪个 ⇒ 默认 `mimo-v2.6-flash`**（MiMo 官方价 **1.00 / 2.00 元每百万 token**，缓存 0.02），备选 `deepseek-flash`（**2.00 / 8.00**，缓存 0.04）——**输出价差 4 倍，长任务优先小米**。
- ⛔ **不许跑 `deepseek V4 pro`**（贵，用户明确否掉）。
- **报价要说清是"实测"还是"外推"**：18 秒片 ≈ 1.4 元是实测；更长的片子按秒数线性外推，**并说明是外推**。
- 真实用量从任务的 `usage_inflight.json` 读（`calls / input_tokens / output_tokens / cached_tokens`），**不要凭印象报钱**。

## 2. 流水线总览（每一步写出的文件，就是下一步读入的文件，顺序不可改）

| 步 | 做什么 | 输出（= 下一步输入） |
|---|---|---|
| 1 | 立项：钉死主题/信源/时长/风格 | `plan.md` |
| 2 | 脚本 + 配音 + 词级时间戳 | `data/script.json`、`assets/audio/vo/*.mp3`、`data/vo-lines.json` |
| 3 | 节拍检测（机器初稿） | `data/beats.json` |
| 4 | ★节拍复核（人工硬门） | `data/beat-review.md` |
| 5 | 合成时间线 | `data/timeline.json` + 注入的 `window.TIMING` |
| 6 | 画面（逐帧绘制或 GSAP 时间线） | `index.html`（+ JS） |
| 7 | 渲染 | `renders/video.mp4` |
| 8 | ★渲完自检 + 混音 + 返工判定 | `renders/out.mp4` + `out/qa/*` |

---

## 3. 逐步操作

### 第 0 步 · ★★ 环境体检（**必做，别跳过**；2026-12-13 实测新增）

**开工第一件事**，在技能目录下跑：

```powershell
& <python> tools\preflight.py
```
它会查三件事，并**把该设的环境变量打印成可直接复制的命令**：

| 查什么 | 不修的后果 |
|---|---|
| 渲染依赖（`playwright-core`） | 渲染器起不来（`Cannot find module`） |
| 浏览器 + `CHROME_PATH` | `render.mjs` 去找 Google Chrome，本机没装 ⇒ 直接失败 |
| **ffmpeg 必须带 `libx264`** | 渲染跑到**编码那步**才崩：`Unknown encoder 'libx264'`（最阴——PATH 上那个可能是残版）|

**为什么不能省**：真机实测**两轮 720 步、1336 万 token、0 产物** —— 步数全耗在这三堵墙上试错。
`preflight.py` 加 `--install` 可自动装渲染依赖；`--json` 给程序读。

★ **它只管环境，不碰任何创作**：页面长什么样、什么风格、几个场景、片长多少，
**全部由你决定**（页面契约见 `tools/render/example.html`，照抄即可）。

**验收**：退出码 0（打印「✅ 环境就绪」）。**若是 1，先按它给的三条命令修，别往下走。**

### 第 1 步 · 立项

- **输入**：用户需求。**输出**：`plan.md`。
- **必写要素**：主题与信源、视觉风格、目标时长 `DURATION`（具体秒数）、分辨率/fps、配音语言、结尾方式、签名位。
- **验收**：① 每个要素都被写死，无「大概/再说」；② `DURATION` 是具体秒数；③ 画面上将出现的**每一个事实**都能指回一条信源。
- **本机建议**：**1920×1080 @ 30fps**（与两个参考项目一致，且本机实测 30fps 可跑）。

### 第 2 步 · 脚本与配音（拿词级时间戳）

- **输入**：`plan.md`。**输出**：`data/script.json`、`assets/audio/vo/<sceneId>.mp3`、`data/vo-lines.json`。
- **命令**（本机实测路径，`edge-tts` **不在**产品便携运行时 `runtime\` 里，这是已知缺口）：

```python
import asyncio, json, edge_tts
# 关键：boundary="WordBoundary"，否则一个词的时间戳都拿不到（默认只发 SentenceBoundary）
c = edge_tts.Communicate(TEXT, VOICE, boundary="WordBoundary")
async for ch in c.stream():
    if ch["type"] == "audio":          buf += ch["data"]
    elif ch["type"] == "WordBoundary": # offset/duration 单位是 100 纳秒
        words.append({"w": ch["text"], "s": round(ch["offset"]/1e7, 3),
                      "e": round((ch["offset"]+ch["duration"])/1e7, 3)})
```
- **验收（四条，缺一不可）**：① 每个词 `s < e`；② 词时间**严格单调递增**；③ `words` 数量 == 文本词数（不等就照抄参考项目打印 `N aligned words vs M script tokens` 并修文本或重生成）；④ **切片段按末词 `e` 裁，不按文件长度**——实测最后一个词在 5.35 s 结束而文件长 6.216 s，**有约 0.87 s 静音尾巴**。
- **兜底（未验证）**：若 TTS 不给词时间戳（如 GPT-SoVITS）已说要用 Whisper / WhisperX 强制对齐——**本机未装任何 ASR，这条路没跑过，先小样验证**。

### 第 3 步 · 节拍检测（机器初稿，**不是结论**）

- **输入**：音乐文件。**输出**：`data/beats.json`（含 `beats / downbeats / tempo / offset / maxDriftMs / quiet / hits`）。

#### ★★ 3.0 · **手上没有音乐？先自己造一条**（2026-10-13 实测新增）

**用户实测提问：「那个视频里没有音乐，他音乐是不是弄不了」**
—— 查下去**是真的没有**：技能处处要求音乐，但工作区里**一个音乐文件都没有**、
本机也**没有音乐素材库**，而 `make_click_track.py` 还写死了那次 18 秒小样的路径
⇒ 模型**无米下锅**，只好交了一条**纯人声**的片子 ✗

**别跳过音乐，也别去找素材 —— 直接合成**（不下载任何东西）：

```powershell
"$PY" tools\make_bgm.py data\bgm-120bpm.wav --seconds 32 --bpm 120 --style beat
```
| 参数 | 说明 |
|---|---|
| `--seconds` | 时长（与 `DURATION` 对齐；**宁可略长**，后面 `--trim` 会裁） |
| `--bpm` | **必须与你打算的切点节奏一致**；文件名会带上它 |
| `--style` | `beat`（卡点·重拍）/ `voiceover`（口播·轻垫底）/ `warm`（温柔·少打击）|

**风格是参数，不是写死的题材** —— 选哪个由你按片子定 ✓
**输出是 44.1k/16bit 单声道，峰值留了余量**（后面要压人声进去，别顶满）。

**★ 顺手记下的检测上限（实测扫出来的，省得你踩）**：

```
目标 BPM   100  120  130  140  150  155  |  160   165   180
检测结果    ✓    ✓    ✓    ✓    ✓    ✓   |  ✗一半  ✗一半  ✗一半
```
**`beatmap.mjs` 的可靠上限 ≈ 155 BPM**，超过会被检成**一半**（八度误判）。
⇒ **想快节奏就把 BPM 定在 155 以内**；实在要更高，脚本会警告你，
并且**拍点位置仍然正确**（可以用 `--trim` 后按 `beats` 数组切，别用常量 BPM 映射）✓

- **脚本位置**：`beatmap.mjs` **随本技能包带了一份**，在 `tools\render\beatmap.mjs`（它依赖同目录 `audio-analysis.mjs`，**两个文件必须在一起**）。**不要照抄 `node beatmap.mjs` 这种裸命令——本机原来的位置没有这个脚本**，先按 `references/脚本位置与跑法.md` 把 `tools\` 拷到工作目录。
- **命令**（`PATH` 里必须先有**带 libx264 的** ffmpeg，否则 Node `spawn('ffmpeg')` 直接 ENOENT。
  ★ 用 `export PATH="/d/MiniMax Design/current/resources/ffmpeg:$PATH"` —— **和命令写在同一条里**）：

```bash
node tools/render/beatmap.mjs track.mp3 data/beats.json --trim track-trimmed.wav
```
- **验收**：① 命令 exit 0 且 JSON 写出；② 记录 `maxDriftMs`；③ **`maxDriftMs > 40` 时不得使用常量 BPM 映射，必须直接切 `beats` 数组**（脚本自己会打印 `large: the track is not on a steady grid; cut to result.beats instead`）；④ **本条通过 ≠ 节拍正确**，必须进第 4 步。
- 备选路线（同样实测会错）：`"$PY" tools\scripts\fit-beat-grid.py <音乐> --min-bpm 100 --max-bpm 130`，输出 `(BPM, BEAT0)` 常量网格 + 逐小节 `K`/dB 表。**必须用 `$PY`**（系统 Python 无 scipy）。

### 第 4 步 · ★节拍复核（人工硬门，不允许自动通过）

任一条不过 → **回第 3 步换参数/换音乐/换路线**，不许往下走。

- **复核 A · 倍频关系**：算 `检测值 / 真值`。接近 **1.5 / 2 / 0.5** 即判初稿错（实测 `181.82/120 = 1.515`，典型三连音误判）。
- **复核 B · 踩点叠听**：用检测出的 `beats` 生成提示音轨，与原曲叠起来听。
  **⛔ 不要写 `-f lavfi -i "sine=frequency=1500:duration=0.03"` 那种命令**——它只是把**一段 0.03 s 的正弦贴在 t=0**，
  **只出一响**，不是"每拍一响"的节拍轨 ⇒ 实测残差里只有 **1 响（−25.5 dBFS）**，判据根本无从执行 ✗。
  **按 `data/beats.json` 的每个时间点逐条 `adelay` 摆 click**（脚本已随包带）：

```powershell
"$PY" tools\make_click_track.py          # 读 data\beats.json，写 music_with_clicks.wav
```
  **判据**：提示音必须**每一响都落在鼓点上**；有半拍错位或整段错位即错。
  （★ 判据能成立的前提是**每拍一响** ✓ —— 实测修好后 **20/20 全中，偏差 +0 ms**）
- **复核 C · 独立指标**：拿 `gridFit(events, beats)` 的圆集中度 `R` 与 `chanceR = 1/sqrt(n)` 比较（`R` 明显高于 `chanceR` 才说明网格对；见 `references/卡点与音频.md`）。
- **硬性要求**：**至少两条独立复核通过 + 一条人工听/看记录**，把「检测值 / 复核值 / 依据 / 结论」写进 `data/beat-review.md` 存档。**参考项目自己承认这个局限**：`Beat tracking finds the pulse reliably but can put "one" on the wrong beat: check the first bars by ear in the live preview. \
### 第 5 步 · 时间线 TIMING（画面的唯一时间源）

- **输入**：`data/vo-lines.json`、`data/beats.json`、`data/script.json`、人工锚点表 `ANCHORS`。
- **输出**：`data/timeline.json` + 注入 HTML 的 `window.TIMING`：

```jsonc
{ "duration": 60, "fps": 30, "beat0": 0.0, "beatLen": 0.5,
  "kick": [[0,16],[24,48]], "drops": [16,40], "outroBeat": 120,
  "scenes": { "s01": { "start": 0, "end": 8,
      "lines": [ { "on": 0.5, "off": 3.2, "words": [["你好",0.5,0.82]] } ] } },
  "captions": [ { "start": 0.38, "end": 3.7, "words": [["你好",0.5]] } ],
  "sfx": [ { "sfx": "whoosh", "vol": 0.5, "t": 0.5 } ], "env": [0.0, 0.12, 0.9] }
```
- **四条排布规则**：① 没锚点的场景 = **上一句结束后 ≥0.12 s 的第一个拍上**（`CUT_AFTER = 0.12`，`nextBeat()`）；② 有锚点则**反向锚定**：`place = anchorLineAt − (l.start − seg.a)/tempo`；③ 片段切割留 **0.08 s 前导 / 0.15 s 尾巴**，且**绝不越过与相邻行的中点**；④ **只要对配音做了变速（如 `TEMPO=1.08`），词级时间戳必须同步除以倍率**。
- **验收**：① 无 `clips the previous voice` / `overlaps previous line` / `voice runs past its scene end` 告警，**每条告警都要读**；② 每个场景 `start < end` 且首尾相接；③ 所有词时间落在其行的 `[on, off]` 内；④ 打印出的场景起点**全部落在拍号上**（人工确认）。

### 第 6 步 · 画面

- **输入**：`window.TIMING` + 风格参考。**输出**：`index.html`。
- **五条硬约束**：① 时间**只**来自 `TIMING`；② **禁 `Date`、禁 `requestAnimationFrame`、禁 CSS 动画/过渡**；③ 随机只用**带种子的 PRNG**（如 `mulberry32`）；④ 所有 DOM 在第一帧前建好；⑤ 复用共享层的 `fromTo` 要 `immediateRender:false`。
- **页面契约**（不满足渲染器直接退出）：

```js
window.__meta  = { W: 1920, H: 1080, FPS: 30, DURATION: 60 };
window.__draw  = t => { /* 画出 t 时刻的整帧；必须是 t 的纯函数 */ };
window.__ready = true;                       // 必须在 document.fonts.ready 之后
window.__renderAudio = async () => base64Wav;// 可选：自带配乐
```
- **卡点手法**：遍历拍号，`kick` 拍上脉冲背景层，`kick` 的小节强拍（`n % 4 === 0`）缩放 `#pulse` **约 1%**；`TIMING.drops` 上单独给 flash + shake；**用词锚点编排**（「芯片出现在 *OAuth* 这个词上」），不要写「场景开始后 0.8 秒」。
- **验收**：① `draw(t)` 是纯函数，**同页渲两遍 SSIM = 1.0**；② 无超过 **2 s** 的完全静止段（设计内的 hold 除外，需登记）；③ 字幕停留够读；④ 文字不出安全区、不压物体、不压最亮处；⑤ 无 `Math.random()` 造成的闪烁。

### 第 7 步 · 渲染

**脚本位置**：`render.mjs` 随本技能包带了一份，在 `tools\render\render.mjs`。**第一次用要先 `cd tools\render; npm install`**（唯一依赖 `playwright-core`，不下载浏览器）。**裸命令 `node render.mjs` 在本机跑不起来**——见 `references/脚本位置与跑法.md`。

```powershell
node tools\render\render.mjs ..\index.html ..\renders\video.mp4 --workers 1 --from 0 --to 10   # 先 1 worker 小片段跑通
node tools\render\render.mjs ..\index.html ..\draft.mp4 --draft                                # 迭代期用草稿
node tools\render\render.mjs ..\index.html ..\renders\video.mp4 --workers 4 --profile          # 放量 + 性能体检
```
- **验收**：`ffprobe` 报 **1920×1080 / 30-1 fps / 时长符合 `DURATION` / h264 + aac 48 kHz 立体声**。
- **本机注意**：**没有 Chrome**，`CHROME_PATH` 必须指向 Edge；多 worker 共享一块 GPU（参考项目注释：笔记本 iGPU 到 4–6 worker 就不再增长），**从小往大试**。
- **★ 必须挂后台**：单条命令 60 s 上限，**任何 ≥15 s 的片子一帧帧渲都必然超时**（18 秒片 540 帧那次前台跑就撞了 60 s 超时，改成后台 + 轮询才跑完）。见 `references/时长档位表.md` §4。

### 第 8 步 · ★渲完自检 + 不合格返工（**不可跳过，这是本技能的立身之本**）

**先合片再自检**：把**未经渲染器改动的** `mix.m4a` 原样 remux 回去（参考项目实测：渲染器重编码 AAC 会把真峰顶到 0.0 dBFS）。

```powershell
"$FF" -y -i renders\video.mp4 -i mix.m4a -map 0:v:0 -map 1:a:0 -c copy -movflags +faststart renders\out.mp4
```
#### 8.0 · ★★ 先跑一遍机器体检（`tools\qa_video.py`；2026-10-13 实测新增）

**渲染完第一件事**（比下面 8.1 的人工开图更早）：

```powershell
"$PY" tools\qa_video.py out\final.mp4 --workspace .
```
**它是你的"眼睛"** —— 你写得出渲染代码，但**看不见自己画的结果**；这个脚本替你量六项：

| 检查项 | 治什么（都是真机踩过的） |
|---|---|
| **编码兼容** | 用 mpeg4/libvpx 编码 ⇒ 用户播放器**只有声音没画面** ✗ |
| **亮度** | 全片太暗（真机只有 27.7/255，手机上像黑屏）✗ |
| **黑场** | 中间一大段连续黑 ✗ |
| **字幕换行** | 一行 32 个字挤一行 ⇒ 小屏读不过来、看着像"说了两遍" ✗ |
| **片尾留白** | 配音比成片长 ⇒ **最后一句被切**（"话没说完"的感觉）✗ |
| **交付物** | 清单缺文件（没发布上去）✗ |

**退出码 0 = 全过 ⇒ 继续做下面的 8.1–8.13**（人工细查内容质量）；
**退出码 1 = 有项没过 ⇒ 看它给的「怎么改」，只改那一项，再跑一次** ✓

★ **它只管客观质量，不碰创作**（内容/风格/时长仍由你定）。
★ 报告里每条失败都写了**具体怎么改** —— 照着改，别重做整条片子。
★ **别跳过它去"自己判断"**：真机上模型在一条 `ffmpeg -encoders` 上**绕了 37 次**
（输出每次一样），根因就是**它没有任何客观反馈**。这一条命令把"看不见"变成"量得出来" ✓

| # | 查什么 | 命令 | 合格线 |
|---|---|---|---|
| 8.1 | **画面抽帧** | 见 `references/验收清单.md` 的 contact sheet 命令 | **必须实际打开这张图，先把看到的描述出来，再动手改**；每个场景最忙的一刻、字幕带、签名位都要看过 |
| 8.2 | **关键帧原尺寸** | `node tools\render\still.mjs index.html stills\ 12.5 34 61.2`（脚本随包带，见 `references/脚本位置与跑法.md`）或 `"$FF" -ss <t> -i out.mp4 -frames:v 1 -update 1 k.png` | 文字不被裁、不压物体、不压最亮处（contact sheet 缩到 320 px 会掩盖细节） |
| 8.3 | **响度 / 真峰** | `"$FF" -i out.mp4 -af "ebur128=peak=true" -f null /dev/null` | `I ≈ −14 LUFS`、`Peak ≤ −1 dBFS`（见下方两说并存） |
| 8.4 | **采样峰值兜底** | `"$FF" -i out.mp4 -af volumedetect -f null /dev/null` | `max_volume` 不逼近 0.0 dB |
| 8.5 | **黑场** | `"$FF" -i out.mp4 -vf "blackdetect=d=0.10:pix_th=0.10" -an -f null /dev/null` | **设计内黑场之外为 0**；设计黑场（如 12f 蓄爆）必须**登记在案做白名单比对**，而不是要求「零命中」 |
| 8.6 | **冻帧** | `"$FF" -i out.mp4 -vf "freezedetect=n=-60dB:d=0.5" -an -f null /dev/null` | 设计内 hold 允许静止；**设计外冻帧为 0**（`d=1.0` 会漏掉 35f 级 hold） |
| 8.7 | **配音与画面时长对齐** | 人声实际起点 vs `TIMING` 的 `on`（`ebur128`/RMS 定位） | **补偿后**偏差 **< 50 ms**；每条字幕出入点误差 **≤ 2f**，连续两条字幕不重叠。**⚠️ 必须先用第 2 步的 `acOffset` 补偿，再套这条线——见 8.7 下方说明，裸测一定不达标** |
| 8.8 | **混音平衡（测量，不许用耳朵）** | 干声 + 台词掩码算 RMS，见 `references/卡点与音频.md` | 音乐在人声下低 **4–6 dB**；`~10 dB` 会被听成「根本没有音乐」 |
| 8.9 | **确定性** | 同参数渲两遍 → `"$FF" -i run1.mp4 -i run2.mp4 -lavfi ssim -f null -` | `SSIM All = 1.000000` |
| 8.10 | **事实核对** | 逐条回溯第 1 步信源 | 画面上每条信息都能追溯到信源 |

### 8.7 说明：`< 50 ms` 这条线为什么必须写「补偿后」（**本机实测，别再当矛盾条文用**）

`edge-tts` 的 `WordBoundary` 时间戳与 mp3 **解码后真正出声**的时刻之间，有一条**系统性的固定滞后**。所以拿「`TIMING` 里的 `on`」直接去和「音轨上的可听起点」比，**永远不可能 < 50 ms**，与技能写没写好无关。

**本机实测（2026-10-12 复测，本会话独立跑的）**：对象 = 应用内 18 秒跑片那条片子的三条真配音（`zh-CN-XiaoxiaoNeural`）。做法 = `ffmpeg` 把 mp3 解成 `f32le` 单声道 24 kHz → 10 ms 窗 RMS → 取第一个超过「本行峰值 −35 dB」的窗起点 = 可听起点。

```
行     首词 s    可听起点    滞后 ms
s01    0.100     0.190       90
s02    0.100     0.180       80
s03    0.100     0.170       70
三行滞后 70~90 ms，中位 80 ms  ⇒ 技能合格线 <50 ms：裸测三行全部不达标
```
同一批素材，制作方自己的 qa 量到的是 **95 / 80 / 75 ms**（起点阈值与窗长不同 ⇒ 读数差 5~10 ms，量级一致）。两条独立测量都落在 **70–95 ms**，**结论一致：这是滞后，不是"它没做好"。**

**执行口径（照这个做，不要自己选）**：

1. **第 2 步就要量 `acOffset`**：每行取「首词 `s`」与「可听起点」之差，写进 `vo-lines.json` 的 `acOffset` 字段（18 秒那条片子里它记的是 0.095 / 0.08 / 0.075，并附 `acOffsetBasis` 说明取样依据）。
2. **第 5 步排布时把音频整体前移** `acOffset`（`place = on − acOffset`），补偿后再渲。
3. **第 8.7 验收的是补偿后的残差**，合格线 **< 50 ms**。补偿后残差 ≈ 0 是定义使然，**它证明的是"补偿被真的执行了"，不是"TTS 很准"**——别把它当精度成绩报出去。
4. **报告里两个数都要写**：`补偿前滞后 = __ ms（本机 70–95 ms 区间）` + `补偿后残差 = __ ms（线 <50 ms）`。只报后者 = 隐瞒已知偏差 ✗。

> **为什么不能干脆把线放宽到 100 ms**：8.7 这条线本来要防的是「画面字幕和人声对不上」；补偿前的 70–95 ms 是**已知常量**、能算掉，而补偿后仍超 50 ms 的多半是**逐行真错位**（切错、变速忘除倍率、场景切点压到人声上）。线放宽到 100 ms 会把真错位一起放过去 ⇒ **线不放宽，改为要求"先补偿再测"。**
> ⚠️ **未验证**：`acOffset` 是不是**恒定常量**（跨音色 / 跨语速 / 跨文本长度是否都稳定在 70–95 ms）——本机只测了这 3 行、单一音色。**换音色或换引擎必须重新量，不许照抄 0.08 s。**

### 8.11 / 8.12 / 8.13 · 三条内容质量线（**技术全绿也照样是废片，必须单独查**）

> 来源：第三方独立审查实测（`小样-10秒\第三方审查.md`）——那条片子 **技术面全过**（I=−14.1 LUFS / 真峰 −1.5 dBFS / 无黑帧 / 两遍 SSIM=1.0），**照样在这三条上被判"不能当卡点视频招牌"**。技术门不查内容，所以这三条必须自己列出来查。

| # | 查什么 | 怎么测 | 合格线 |
|---|---|---|---|
| 8.11 | **尾部空转** | **口径必须写死，否则两次测量对不上**：整片解码成**小尺寸灰度**（480×270 够用）→ 算**逐帧平均灰度差** → 按 **0.5 s** 分桶取 mean，记全片中位数 `M` | **最后 4 个 0.5 s 桶（= 尾部 2 秒）里，至少 2 个桶的 mean ≥ M**。实测校验：10 秒小样尾部两桶 **0.438 / 0.438** ≪ 中位 0.72 ⇒ **判不合格 ✓**（与"五个拍点全空转"的人工结论一致）；30 秒片尾部 **2.04 / 0.81 / 0.64 / 1.41** ≥ 中位 0.86 ⇒ **合格 ✓**。反例原貌：10 秒小样 **7.533 s 之后再无切点**，8.0–9.0 s 平均 **0.432**、9.0–10.0 s **0.438**（对照前 7.5 秒每段 0.84–1.21），**7.5/8.0/8.5/9.0/9.5 五个拍点全部空转 = 全片 25% 白扔** ✗。★ **报告必须写明用的是哪种帧差**（逐帧平均灰度差 / 相邻帧差分和），否则和别人量的数**不可比** —— 2026-10-12 实测踩到：同一条片子，制作方按"0.25 s 平均帧差"报中位 1.690，独立验收按"逐帧平均灰度差"量到尾部 0.42~2.04，**两个数都对但无法互证** |
| 8.12 | **逐字高亮必须跟词时间戳，不能是定时动画** | ① 量青色填充前沿速度是否**忽快忽慢**；② 把每个字格"变亮时刻"列出来，和人声的**停顿区间**比；③ ★**量"词尾过冲"**（2026-10-12 新增，最容易漏的一条） | ① 高亮推进必须**由 `TIMING` 里的词 `s/e` 驱动**（每个 span 的亮起时刻 = 该词的 `s`）；② **人声停顿期间高亮不许前进**；③ ★ **每个词的动画窗口必须收在"实际发声尾"上，过冲 ≤ 40 ms**（量法见下方 8.12 说明）。实测反例：前沿速度 **110–120 px/s → 峰值 355 px/s → 落回 110 px/s**（快慢差 4 倍），且第 4 字格在 **1.933 s** 变亮而人声 **1.86–2.16 s 是停顿**、第 18 字格 **4.800 s** 变亮而人声 **4.560 s 已结束** ⇒ 判为**定时匀速擦除**，不是逐词跟读 ✗ |

### 8.12 说明：为什么还要单独量「词尾过冲」（**2026-10-12 实测新增**）

**「高亮跟词时间戳驱动」≠「高亮跟人声对齐」。** 只查驱动源会漏掉这一类：

> 动画窗口直接用了 TTS 给的词边界 `[ws, we]`，**而 `we` 比这个词真正的发声尾巴长**
> ⇒ 词已经没声音了，那个字还在继续变亮 ⇒ 观众看到的是"**字幕慢半拍**"。

**本机实测（那条 30 秒抖音片，56 个可比词）**：

```
过冲（= we − 实际发声尾）   中位 4 ms   均值 29 ms   最大 109 ms
过冲 >40 ms 的词            23 / 56（41%）
过冲 >80 ms 的词             9 / 56（16%）
实际发声占窗口的比例         中位 0.97，尾部低到 0.64
最差几个：外网 109ms（占比 0.74）· 报告 91ms（0.77）· 统计 86ms（0.84）· 干活 84ms（0.83）
```
★ **别只看中位数**：中位 4 ms 会让人误判"没问题"，**问题全在尾部那四成词上**（这一点本轮实测自己踩过：先按中位数放过，改成看分布才发现）。

**怎么量（可执行）**：

```python
# 在「人声干声」上量，别用混音轨（混音轨有音乐铺底，静音检测会失效 —— 本轮踩过）
# 1) 解成单声道 24k f32le，5 ms 窗 RMS → dB；阈值取「相对峰值 −35 dB」
# 2) 对每个词的 we，往前 0.8 s 找最后一个有声窗 → 那就是「实际发声尾」
# 3) 过冲 = we − 实际发声尾；>40 ms 就算不合格
```
**修法（写进第 6 步）**：把每个字 span 的动画窗口从 `[ws, we]` 收紧到 `[ws, 实际发声尾]`——
窗口尾 = min(we, 该词最后一个有声窗的时刻)。**驱动源仍然是词时间戳，只是尾巴按音频实收紧。**

**两个阈值必须写明**：本轮先用 −30 dB 量得"中位 4 ms"差点放过，换 −35 dB 才看清尾部；
**报告里必须写清用的相对阈值**（本技能统一用 **−35 dB**），否则两次测量不可比。
| 8.13 | **字幕覆盖率** | 逐帧统计**字幕行所在的横带**（惯例是 y 85%–90%，本机 30 秒片实测就在这条带）里有没有"**字幕行的字**"（字号与对白字幕一致、成行居中），**不要**把画面中部的大字标题算进去 | **≥ 80%**；**纯动画段必须挂 caption**。★ **片尾字标段单独判**：品牌卡片上的说明小字（tagline）**算不算"有字幕"，必须事先写死并说明**，否则这条会自欺 —— 2026-10-12 实测踩到：30 秒片按"下三分之一任意亮字"量得 **97.4%**，但把带口收窄到字幕行 y 85%–90% 后，**片尾 3.5 s 是品牌卡片**（大字 + tagline 在 y 40%–45%），并非跟读字幕 ⇒ **真实字幕覆盖约 88%，不是 97.4%**。实测反例（10 秒小样）：字幕只在 **0.53–4.97 s** 存在 = **44%**，5.0 s 后的卡片与 7.5 s 后的片尾标题**全程无字幕** ✗ |

**这三条的返工去向**：8.11 不过 → 回第 5 步把尾巴的拍点用起来（加字标动效 / 加收尾 SFX / 缩短 `DURATION`，**不许留着空转**）；8.12 不过 → 回第 6 步把高亮的驱动源从"定时器"改成"词时间戳"（每个字 span 的亮起时刻绑 `words[].s`）；8.13 不过 → 回第 5 步补 caption 条目。

**返工判定（硬规则）**：8.3 / 8.5 / 8.6 / 8.7 / 8.8 / 8.9 / 8.11 / 8.12 / 8.13 任一项不过 → **回到对应步骤重做，不许放过**。
**不要用没有证据的「整体不错」代替逐项检查，也不要为了报告好看而全部标通过。**
**独立审查（流程级，不可省）**：交付前派一个**干净上下文的 subagent** 做第三方审查，不给它制作过程的辩解与修改历史，按编号出**带帧号证据**的报告（格式例：`F2 ✗（第 340 帧缺少已确认的知识图谱功能）`），最后分「必须修复项 / 建议优化项 / 无法验证项」。**首检永远不能交给用户。**

## 4. 交付时必须报出来的数字

`DURATION` 与实测时长 · 检测 BPM + 复核结论 · `maxDriftMs` · 切点距最近拍的最大误差（f） · `I` LUFS / `Peak` dBFS · 混音平衡 dB · 黑场/冻帧清单（含设计白名单） · **字幕对齐：补偿前滞后 ms + 补偿后残差 ms（两个都要报）** · **字幕覆盖率 %** · **尾部拍点有无空转** · **逐字高亮的驱动源（词时间戳 / 定时动画）** · 两遍 SSIM。
**报告测出来的数字，不要报告印象。**

## 5. 已验证 / 未验证（照抄调研分栏，未验证的写成「这条路没跑过，先小样验证」）

### ✅ 已在本机实测跑通（原 10 项，同类合并列出）

| # | 事项 | 实测证据 |
|---|---|---|
| 1 | Playwright 驱动系统 Edge 截图 / **HTML → MP4（Edge 路线完整可用）** | `chromium.launch(channel='msedge')` 成功、CDP 截图返回 5248 字节 PNG；`render.mjs` + `CHROME_PATH`=Edge 渲出 90 帧 / 640×360 @30fps / 1.9 s，ffprobe 确认 h264 + aac |
| 2 | 联系表 contact sheet | 2596×188 PNG、7 帧带时间码，已实际看图确认内容正确 |
| 3 | 验收类滤镜 `ebur128=peak=true` / `blackdetect` | `I: -11.2 LUFS` / `LRA: 20.0 LU` / `Peak: -10.5 dBFS`；`blackdetect=d=0.1:pix_th=0.05` 正常执行 |
| 4 | `edge-tts` 词级时间戳 | `boundary="WordBoundary"` → 英文 11/11、中文 10/10 词均带毫秒时间戳 |
| 5 | 节拍检测两条路线都能跑 | `beatmap.mjs`：96 s 音频 **0.3 s** 跑完，输出 beats / tempo / downbeats / quiet / hits / `maxDriftMs`；`fit-beat-grid.py`（venv 上）输出 `grid fit: BPM 93.40 BEAT0 0.500` + 逐小节 `K`/dB 表 |
| 6 | `sidechaincompress` ducking 链路可测 | 12 s 素材：空隙 −26.07/−26.94 dB → 说话时 −41.92 dB（≈15 dB 压低），人声 −6.11 dB |
| 7 | `-filter_complex_script` 读文件 | exit 0，输出正常 |
| 8 | **中文词 → 字幕 span 逐字高亮**（原第 10 条的一半） | **已在 18 秒真片里做出来**（据 `小样-应用内\应用内跑片报告.md` §3.4：字幕做出来了、逐字高亮在逐词推进、不是静态染色）。**⚠️ 两点限制**：① 那份报告只抽看了 10 帧、**没做 ASR**，所以"高亮跟不跟发音"它无法核实；② 同一条片子的**驱动源到底是词时间戳还是定时动画，没有定论**（10 秒小样那条已实测是定时动画 ✗，见 8.12）⇒ **渲染能力已证，跟读正确性未证** |
| 9 | **全链路端到端**（脚本→TTS→时间线→画面→渲染→混音→自检） | **17:35:40 提交 → 18:33 出片，整条链真跑通**：18.0 s / 1920×1080 / 540 帧 / 切点 0-5.998-11.998-15.998 全在拍上 / I=−14.1 LUFS / 两遍 SSIM=1.0。**⚠️ 但它没走到"交付"**：卡在 8.7 配音对齐返工，且 60 次迭代上限把任务掐断过一次（靠用户发「继续」续上） |

### ❌ 未验证（**这条路没跑过，先小样验证再上量**）

| # | 未验证事项 | 怎么对待 |
|---|---|---|
| 1 | **`acOffset` 是否恒定**（跨音色/语速/文本长度） | 只测过 `zh-CN-XiaoxiaoNeural` 的 3 行（70–95 ms）。**换音色/换引擎必须重新量，不许照抄 0.08 s**；见 8.7 说明 |
| 2 | **节拍检测的准确性** | 两条路线本机实测**都错过**（含 18 秒那次：初稿 240.43 BPM = 真值 ×2）。见第 3、4 步，**必须复核** |
| 3 | GPT-SoVITS API 调用 | 服务未启动（9880/9874/9873/9872 端口均未监听）；**是否提供词级时间戳未知（很可能没有）** |
| 4 | `hyperframes` 的实际行为 | `lint/check/snapshot/render` 的命令行与输出**全为二手转述，本机未跑过**；无网络验证前不要依赖 |
| 5 | 参考项目 `build-timeline.mjs` / 小节拼接（bar-splice） | 前者用 `-/filter_complex <文件>`，本机 2019 构建直接失败，改 `-filter_complex_script` 后**完整脚本未跑过**；后者的多段 `amix` 图未实测，「第一段必须从 0 s」这类坑只在文档里读到。**18 秒那条片子没用小节拼接**（它自合成 BGM），所以这条仍然没跑过 |
| 6 | 多 worker 并行渲染 | 只验证过 `--workers 1`；共享 GPU 行为与块拼接正确性未验证 |
| 7 | 强制对齐路线 | faster-whisper / WhisperX / ElevenLabs align：本机没装任何 ASR、无 key |
| 8 | `edge-tts` 进产品便携运行时 | 只在开发 venv 可用（**用户已知缺口**），便携运行时能否调用未验证 |
| 9 | `librosa` 在 Windows 会挂起 / Python 版 Playwright 逐帧渲染循环 | 前者是参考项目的**声明**，本机**未复现**；后者只验证过「能截图」，完整逐帧循环未写未跑（Node 版已跑通；18 秒那条片子用的是**它自己写的 `render.py`**，不是本技能的 `render.mjs`） |
| 10 | **15 / 30 / 60 / 120 秒四档的时长参数** | `references/时长档位表.md` 里**只有 18 秒那一档是实测**，其余三档是**推算**。跑完第一条 30 秒片必须回来用真数替换推算 |
| 11 | Remotion / Manim | **完全未调研**，不要假设任何细节 |

## 6. references 索引（细节都在这里，本文只留骨架）

| 文件 | 什么时候读 |
|---|---|
| `references/时长档位表.md` | **开工第一步就读**：15/30/60/120 秒各多少场景/多少拍/配音字数/留白/渲染策略（基准 = 那条 18 秒真片） |
| `references/脚本位置与跑法.md` | **跑任何 `node xxx.mjs` 之前必读**：脚本在包里哪、怎么装依赖、`beatmap.mjs`/`render.mjs` 怎么调 |
| `references/环境与坑.md` | **动手前必读**：本机实测环境事实、四个 ffmpeg 构建的差异、字体/沙箱/venv/edge-tts 缺口 |
| `references/卡点与音频.md` | 第 3–5 步与混音：节拍检测→复核→时间线→关键帧对齐、edge-tts WordBoundary、BGM/SFX、`sidechaincompress` 参数 |
| `references/分镜配方卡.md` | 第 1 步与第 6 步：10 张中文配方卡（字段结构、参数表、已知坑、验收帧） |
| `references/验收清单.md` | 第 8 步：每条验收的可执行命令/脚本 + 实测输出样例 + 返工判据（含 8.11/8.12/8.13 三条内容线） |

**`tools\` 目录（随包带的脚本，不是命令）**：`tools\render\{beatmap,audio-analysis,render,still}.mjs` · `tools\make_click_track.py` · `tools\scripts\{fit-beat-grid,measure-mix-balance}.py` —— **用前先读 `references/脚本位置与跑法.md`**。

**两说并存（材料里对同一件事说法不一致处，按此执行，不要自己选一个）**：
- **ffmpeg 选型**：调研方案一用 `EVCapture` 2019 构建做全部工作（它能跑全部验收类滤镜）；调研方案二说 **MiniMax 6.1.1 是首选**。本技能**按后者**，因为 2019 构建缺 `xfade`。
- **ducking 深度**：参考项目一 = 说话时音乐低 **5 dB**（`sidechaincompress`）；参考项目二 = 压低 **10–15 dB**（手写增益自动化，0.1 s attack / 0.4 s release）。**两者不矛盾**（全片铺满电子乐 + 旁白 vs 自制配乐 + 叙述），**必须按自己素材实测调整，不要照抄数字**。
- **交付响度**：两个参考项目**独立地**给出 `loudnorm=I=-14:TP=-1.5:LRA=11`；而 video-shotcraft 的 `sound-design` 用的是 `loudnorm=I=-16:TP=-1.5`。**两说并存**：社媒/平台交付用 −14，需要更保守时用 −16。
- **BGM 垫底电平**：`music −5 dB → sidechaincompress`（方案一）vs `BGM 压到约 0.34 系数（约 −9.4 dB）给 SFX 留 headroom`（方案二）。**并存，按素材实测。**
- **SFX 电平**：`每个 SFX 归一到 −16 LUFS，再叠 cue vol（默认 0.5）`（方案一）vs `常规乘法系数 0.2–0.6，素材峰值贴近 0 dB 时才成立`（方案二）。**并存。**
- **品牌字标 hold**：`pipeline.md` 给的是正向预算「品牌字标落定 **hold ≥1s（30f）**」，而 `aesthetic-rules` R1 给的是上限判例「**hold ≤1s**」，且 `outro sign-off hold ≤1s`。**两条都要满足 ⇒ 品牌字标 hold 就是约 1 s（30f），不是越长越好。**
