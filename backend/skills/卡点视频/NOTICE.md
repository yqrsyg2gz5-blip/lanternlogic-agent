# NOTICE —— 这个技能包的来源与许可（**别删这一页** ✓）

> 这一页是**护身符** ✓ 也是咱自己的规矩：**别人开源的经验，用了就得署名** ✓
> 对外一律这么说 ✓：**"借鉴了两个 MIT 开源项目 + 我在自己机器上实测改造"** ✗ 不许说"我原创" ✓

---

## 一、借鉴了谁（都是 MIT，允许商用，但要求保留本声明 ✓）

| 项目 | 许可 | 我们用了它的什么 |
|---|---|---|
| [bestagentkits/motion-video-skill](https://github.com/bestagentkits/motion-video-skill) | **MIT** · Copyright (c) 2026 BestAgentKits | 卡点视频的整体流程思路、`gridFit` 节拍网格片段、`measure-mix-balance` 的干声掩码 RMS 核心、`contact-sheet` 抽帧脚本（本项目改写为 PowerShell 版）|
| [kiselas/every-frame-is-code](https://github.com/kiselas/every-frame-is-code) | **MIT** · Copyright (c) 2026 Aleksandr | 零依赖节拍检测思路（自写 FFT + 谱通量 + Ellis 动态规划）、CDP 逐帧截图 → image2pipe → libx264 的渲染管线、验收指标口径 |

两份 **LICENSE 原文均已实读** ✓ 上述片段**逐段在 references 里标了出处与原始 URL** ✓

## 二、只读了介绍、**没有抄任何代码**的（源码没读到正文 ✗）

| 项目 | 情况 |
|---|---|
| [Vincentwei1021/video-shotcraft](https://github.com/Vincentwei1021/video-shotcraft) | 只读到卡名、命令、在线画廊的说明文字；`demos/**`、`gallery/api/library.json` 正文**未读到** ⇒ 只借鉴了"分镜配方卡"这个**结构**，卡内容是我们自己写的 ✓ |
| [zhangzhanglaila/ai-montage-agent](https://github.com/zhangzhanglaila/ai-montage-agent) | `pipeline.py`（99KB）读取时被 GitHub 限流 403 ⇒ **完全未读到源码**；文档里凡是引用它的公式/类名，均标注为"来自其自述文档，未经源码核实" ✓ |

## 三、**刻意没有引入**的（避免许可麻烦 ✓）

- **Remotion** —— `video-shotcraft` 依赖它；个人/小团队免费，**公司使用可能需付费** ✗
  我们改用**已在本机实测跑通**的 **Playwright + 系统 Edge 逐帧截图** 路线 ✓ 少一个法律问题 ✓

## 四、哪些是**我们自己**的（可以理直气壮说是自己的 ✓）

- **全部本机实测事实**：四个 ffmpeg 构建谁缺 `xfade`、谁缺 `drawtext`；drawtext 中文豆腐块的复现与 `fontfile=` 解法；
  `edge-tts` 必须显式传 `boundary="WordBoundary"` 才有逐词时间戳；节拍检测在本机会判错的错例（120→181.82）；
  单帧 PNG 需 `-update 1`；`ebur128` 逐帧行须行首锚定解析；**`edge-tts` 词时间戳与可听起点之间有 70–95 ms 系统性滞后**（`acOffset`）。
- **为 Windows + 本地优先产品定制的部分**：8 步操作手册与每步验收标准、返工判定表、沙箱/本机执行取舍、
  中文分镜配方卡正文、**8.11/8.12/8.13 三条内容质量线**、**时长档位表**、以及"渲完自检 + 不合格返工"这套闭环。

## 五、随包再分发的脚本（`tools\` 目录）——**MIT 要求保留本声明，别删**

技能包 `tools\` 里**带了一份可直接运行的脚本**（不是让我们自己照命令去别处找）：

| 文件 | 来源 | 许可 |
|---|---|---|
| `tools\render\beatmap.mjs` · `audio-analysis.mjs` · `render.mjs` · `still.mjs` | [kiselas/every-frame-is-code](https://github.com/kiselas/every-frame-is-code) | **MIT** · Copyright (c) 2026 Aleksandr |
| `tools\scripts\fit-beat-grid.py` · `measure-mix-balance.py` | [bestagentkits/motion-video-skill](https://github.com/bestagentkits/motion-video-skill) | **MIT** · Copyright (c) 2026 BestAgentKits |
| `tools\make_click_track.py` | **本项目自写**（2026-10-12） | 本工作室 |

⇒ **对外分发（含便携包、发布包）时 `tools\` 与这一页必须一起走** ✓
⇒ 用法与注意事项见 `references/脚本位置与跑法.md` ✓
