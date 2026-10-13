# 渲染器（render.mjs / still.mjs）· 页面契约与跑法

> 这一页是 `tools/render/` 里那几个脚本的说明书。
> `render.mjs` 的代码注释里指向的 `render/README.md` **以前不存在**（文档缺口，2026-12-13 补上）。

---

## 1. 一句话跑法

在**技能目录**下（`tools\` 的上一级），PowerShell：

```powershell
# ① 先体检（一条命令告诉你环境缺什么、该设什么）
& <python> tools\preflight.py

# ② 照它打印的命令设好环境（它会把该填的路径填好，直接复制）
export PATH="<带 libx264 的 ffmpeg 目录>:$PATH"
export CHROME_PATH="<Edge 或 Chrome 的 exe>"
# ③ 渲染
node tools\render\render.mjs <你的页面.html> out.mp4 --workers 2
```
**`preflight.py` 是开工第一步，别跳过** —— 见 §4"三堵墙"。

---

## 2. ★ 页面契约（缺一条就 `waitForFunction: Timeout 120000ms exceeded`）

`render.mjs` / `still.mjs` 要求页面暴露：

| 接口 | 必须 | 说明 |
|---|---|---|
| `window.__meta = { W, H, FPS, DURATION }` | ✅ | 画布宽高、帧率、**总时长（秒）** |
| `window.__draw = (t) => {…}` | ✅ | 把「第 t 秒」的画面画到 canvas 上 |
| `window.__ready = true` | ✅ | 前面都就绪后**最后**置位 |
| `window.__renderAudio = () => '<base64 WAV>'` | 可选 | 给了就把音频混进去；不给=无声（可用 `--voice` / `--music` 另外混） |

### ★ `__draw(t)` 必须是**时间的纯函数**

同一个 `t` 画出来的东西必须**完全一样**。渲染器是**逐帧抽取**的：

```js
// ✗ 别这样写 —— 每帧都不一样，出来的片子会闪
function draw() { box.x += 1; }          // 依赖上次状态
function draw() { box.x = Date.now() % 100; }  // 依赖真实时钟
requestAnimationFrame(draw);              // 自己跑动画循环

// ✓ 这样写 —— 画面只由 t 决定
function draw(t) { const p = t / DURATION; box.x = 160 + 800 * p; }
```
**完整可跑样例**：`tools/render/example.html`（照抄契约即可，内容随你改）。

---

## 3. 命令速查

| 脚本 | 命令 |
|---|---|
| `beatmap.mjs` | `node tools\render\beatmap.mjs <音乐> data\beats.json --trim trimmed.wav` |
| `render.mjs` | `node tools\render\render.mjs <html> <out.mp4> --workers 2` |
| `still.mjs` | `node tools\render\still.mjs <html> <stills目录> 12.5 34 61.2` |
| `audio-analysis.mjs` | **不是命令** —— `beatmap.mjs` 的依赖，必须同目录 |

`render.mjs` 常用开关（`--help` 看全）：

| 开关 | 作用 |
|---|---|
| `--draft` | 快速预览：`--scale 0.5 --fps 30 --format jpeg --preset veryfast` |
| `--workers N\|auto` | 并行浏览器数（默认 auto = 1/4 核，1–6） |
| `--from S` / `--to S` | 只渲染片段（调试用，省时间） |
| `--voice FILE.wav` / `--music FILE` | 混入配音 / 音乐 |
| `--loudnorm` | 混完做 −14 LUFS 归一 |
| `--crf N` / `--preset P` | x264 质量/速度（默认 18, slow） |
| `--shots` / `--shot ID` | 列出/只渲染某个分镜（页面用 `runtime/film.js` 时才有效） |

---

## 4. ★★ 三堵墙（2026-12-13 实测：修好之前渲染必崩）

真机上跑 30 秒片时，**两轮 720 步、1336 万 token、0 产物** —— 卡住的就是这三条：

### ① 渲染依赖没装

```powershell
cd tools\render
npm install     # 只装 playwright-core，不下载浏览器
```
不装 ⇒ `Cannot find module 'playwright-core'`，渲染器起不来。

### ② `CHROME_PATH` 没设

`render.mjs` 的逻辑：`process.env.CHROME_PATH ? {executablePath: …} : {channel: 'chrome'}`。

**本机没装 Google Chrome**（只有 Edge）⇒ 不设这个变量，它会去找 Chrome 然后失败：

```powershell
export CHROME_PATH="/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"`` \
### ③ ★ **PATH 上的 ffmpeg 可能是残缺版**（最难发现的一条）

**同一个命令名可能有两个版本，PATH 上那个不带 `libx264`**：

```
D:\Software\GPT-SoVITS\...\runtime\ffmpeg.EXE   ← 无 libx264 ✗（PATH 上排前面）
/d/MiniMax Design/current/resources/ffmpeg.exe  ← 有 libx264 ✓
```
带残版渲染会走到编码那步再崩，报：

```
Unknown encoder 'libx264'
```
**解法**：把**带 libx264 的那个目录**加到 PATH 最前：

```powershell
export PATH="<完整 ffmpeg 的目录>:$PATH"`` \
**怎么确认哪个是完整的**：

```powershell
ffmpeg -hide_banner -encoders | grep libx264
# 有输出 = 这个能用；没输出 = 残版
```
`preflight.py` 会自动帮你判断这两个版本 ✓

---

## 5. 环境变量只影响当前 shell（★ 容易踩）

`export CHROME_PATH=…` **只对当前这个 shell 生效**。下一个进程（含 agent 起的每个
`shell_exec`）**都拿不到** ⇒ **必须在同一条命令里先设再跑**：

```bash
export PATH="/d/MiniMax Design/current/resources/ffmpeg:$PATH"; export CHROME_PATH="/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"; node tools/render/render.mjs page.html out.mp4 --workers 2
```

★ **本应用执行命令走 git bash**（实测 `bash 5.2.37`）⇒ 用 `export`，
**不要用 PowerShell 的 `$env:X = ...`**（bash 里报 `:X: command not found`）✗
★ PATH 里含空格的目录**必须加引号**（本机就是 `MiniMax Design`）✓

（`preflight.py` 只**打印**这两行、**不代设** —— 代设了下一个进程也拿不到，反而造成假象。）
