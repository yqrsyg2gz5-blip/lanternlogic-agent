"""配置加载 —— 契约三（零硬编码，解析失败启动即报错，不静默回退）"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _StrictModel(BaseModel):
    """配置模型基类：**多余字段一律报错**（P1-11）。

    旧行为是 pydantic 默认的 `extra="ignore"` —— 用户把 `workspace_root` 写成
    `workspaceRoot`、或填了已删除的字段，配置被**静默丢弃**、程序照常启动，
    用户完全不知道自己的设置没生效。契约三写着"解析失败启动即报错，不静默回退"，
    但这一条此前**只对"类型错"生效，对"字段名错"没生效**。
    """

    model_config = ConfigDict(extra="forbid")


class ServerCfg(_StrictModel):
    host: str = "127.0.0.1"  # 手机直连：改为 "0.0.0.0"（局域网可达；必须同时设 access_token）
    port: int = 8642
    access_token: str = ""  # 局域网/远程访问密码（第 41 班）：host=0.0.0.0 时强制要求非空


class ModelCfg(_StrictModel):
    # model_name 带 model_ 前缀，关掉 pydantic v2 的保留命名空间警告
    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    provider: str = "mock"
    model_name: str = "deepseek-chat"
    base_url: str | None = None
    api_key_env: str | None = None  # 只存环境变量名，值绝不落盘（契约三规则 1）
    temperature: float = 0.7
    max_tokens: int = 8192
    max_iterations: int = 25  # add-only 新增字段，向后兼容
    max_context_tokens: int = 65536  # add-only：history 上下文预算，超限自动裁剪（loop._compact_history）
    # ★★ 2026-10-06「模型分级」——**默认空 = 完全关着** ✓（不填就等于没这个功能 ✓）
    #
    #   做什么：把**只做判断、不动手**的那几处调用（开会发言 / 收口 / 主持人"够了没"）
    #           换成更便宜更快的模型 ✓ —— 它们不需要强模型 ✓ 而量大 ✓。
    #
    #   ★ 为什么默认关（而不是默认开）：
    #     这是**唯一一个可能把质量换钱的改动** ✗ —— 今天刚把"四个任务类型全过"调出来 ✓
    #     不能为了省一点钱把它换掉 ✗。所以做成**你填了才生效** ✓ 不填完全等于没这功能 ✓。
    #
    #   ★ 故意**不管**哪一处：**验收人的判定** ✗ ——
    #     它决定"能不能过" ✓ 用弱模型省下的钱，会以"该打回没打回"的形式几倍还回去 ✓。
    #     （这条写在这儿，免得将来有人"顺手"把它也接上 ✓）
    simple_model: str = ""       # 例：填 "glm-5.3-flash" 或 "mimo-v2.6-flash"；空 = 不启用 ✓


class ExecutorCfg(_StrictModel):
    type: str = "local"
    workspace_root: Path = Path("./workspace")
    allowed_dirs: list[Path] = Field(default_factory=lambda: [Path("./workspace")])
    # K4：代码默认与 contracts/config.example.json 对齐——此前默认 []，用户手写
    # 极简 config 时 rm 等破坏性命令【零审批】。单一事实来源仍是 example 文件；
    # 此处默认值由 test_code_default_approval_required_matches_example 钉住防漂移。
    approval_required: list[str] = Field(default_factory=lambda: [
        "rm", "del", "rmdir", "rd", "erase", "format", "reg", "remove-item",
    ])
    timeout_seconds: float = 60.0  # add-only 新增字段
    attachment_max_mb: int = 50  # 附件上传大小上限（MB）
    wide_max_concurrent: int = 8  # Wide Research 并发上限
    shell: str | None = None  # add-only：显式 shell（如 Git Bash），空 = 平台默认（Windows 为 cmd）
    search_url: str = "https://www.bing.com/search"  # add-only：web_search 数据源（可换 SearXNG/Tavily 等）
    searxng_url: str = ""  # add-only：SearXNG 实例地址（如 http://127.0.0.1:8080）。设置后搜索优先走它，失败回退 Bing 链
    browser_channel: str = "msedge"  # add-only：浏览器通道（msedge/chrome，实况窗口用系统浏览器免下载）
    comfyui_url: str = "http://127.0.0.1:8189"  # add-only：ComfyUI API（图像生成）
    image_checkpoint: str = "Juggernaut-XL_v9.safetensors"  # add-only：默认作图模型
    # 复审修正：Literal 白名单——拼写错误（"docker "、"on"）不再静默等于 off；
    # network 拒绝 host（容器共享宿主网络栈会让"断网"承诺失效）
    sandbox: Literal["off", "docker"] = "off"
    sandbox_image: str = "python:3.12-slim"  # 复审 P0-D：alpine 无 bash（bash -c 必 127），slim 自带  # 沙箱镜像（自带 Python，写完代码可直接真跑；首次自动拉取约 50MB）
    sandbox_network: Literal["none", "bridge"] = "none"  # none=断网（默认，最安全）；bridge=需要联网装包时临时开
    sandbox_memory: str = "512m"  # 沙箱内存上限


class McpServerCfg(_StrictModel):
    """一个 MCP stdio server 的启动方式（万物皆可插，第 41 班）。

    command+args 启动子进程，JSON-RPC over stdio（协议 MIT，可商用；
    ⚠️ 每个第三方 server 自身许可证逐个查）。
    """

    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)


class ImageCfg(_StrictModel):
    """图像生成路由（第 41 班）：云端通义万相（推荐，与通义/视频同账号）/ 本地 ComfyUI。"""

    provider: str = "dashscope"  # dashscope=云端（分发场景开箱即用）/ comfyui=本地
    model: str = "wanx2.1-t2i-turbo"
    size: str = "1024*1024"
    api_key_env: str = "DASHSCOPE_API_KEY"
    timeout_seconds: float = 180.0


class MemoryCfg(_StrictModel):
    """长期记忆库（第 41 班）：交付后自动提取、新任务自动注入。全本地，用户可控（可清空）。"""

    enabled: bool = True
    max_entries: int = 200


class NotifyCfg(_StrictModel):
    """外发通知（第 41 班）：任务交付时推送 Slack / 邮件。凭据走环境变量不落盘。"""

    slack_webhook: str = ""  # Slack Incoming Webhook URL（支持 env:VARNAME 引用环境变量）
    email: dict[str, Any] = Field(default_factory=dict)  # {smtp_host, smtp_port, smtp_user, smtp_pass_env, to: []}


class McpCfg(_StrictModel):
    servers: dict[str, McpServerCfg] = Field(default_factory=dict)
    enabled: bool = True  # 总开关（server 启动失败自动跳过不阻塞后端）


class StorageCfg(_StrictModel):
    type: str = "fs"
    data_dir: Path = Path("./data")


class SkillsCfg(_StrictModel):
    skills_dir: Path = Path("./skills")  # 技能库目录（每个子目录 = 一个技能，含 SKILL.md）


class AsrCfg(_StrictModel):
    """语音转文字（Phase 2 ⑤ 两档并列）：
    provider = mimo（云端，有 Key 就能用、零下载）/ local_qwen3（本地离线，需自行安装）。
    Key 同样只存环境变量名，值不落盘。"""

    provider: str = "mimo"
    model: str = "mimo-v2.5-asr"
    api_key_env: str = "XIAOMI_MIMO_API_KEY"
    # ★ 2026-10-06：本地那档用哪个规格（千问3-ASR 开源版只发了两个）
    #   0.6b = 约 2GB 显存、很快、中文只差约 1 个百分点 ✓（默认，先能用 ✓）
    #   1.7b = 约 4GB 显存、中文最准 ✓（显存够就选它 ✓）
    local_tier: str = "0.6b"


class KbCfg(_StrictModel):
    """知识库的**向量档位**（2026-10-07 用户拍板："我做这个知识库本地的" ✓）。

    原来知识库**只接阿里 DashScope** ✗ —— 用户看到界面上写着"需要阿里云百炼 Key"
    就问："这个为什么要用阿里百炼这个呢？我没明白" ✓ 并明确要求走本地 ✓✓。

    embedder:
      · **local**（默认）：本地模型 ✓ 完全离线 ✓ 免费 ✓ 数据不出本机 ✓
      · dashscope：云端 ✓ 免下载 ✓ 但内容会传到阿里 ✗ 且按量计费 ✓
    local_model: 本地模型名（默认 bge-small-zh-v1.5 ✓ 中文小模型 ✓ 约 100MB ✓）

    ★ 铁规矩：**绝不静默回退** ✗ —— 选本地就只用本地 ✓ 用不了如实报错 ✓（与 ASR 同款 ✓）。
    """

    embedder: str = "local"
    local_model: str = "BAAI/bge-small-zh-v1.5"


class TtsCfg(_StrictModel):
    """语音合成（说）—— 后端可切（2026-10-06 补）。

    ★★ 为什么要加这一段（用户实测问出来的 ✗✗）：
      在此之前"用哪个 TTS 后端"是**三处各写各的** ✗ ——
        · 关于页写「Edge」（**是对的** ✓ 实际跑的就是它 ✓）
        · 设置页写「现在固定用 MeloTTS」（**过时** ✗）
        · 能力总览表硬编码 `return "melotts"`（**错的** ✗）
        · 而真实默认来自接口层 `TTSReq.backend = "edge"` ✓
      ⇒ 用户看到的**三处口径互相打架** ✓ 而且**界面上根本切不了** ✗（设置页自己都写着"还没做"✓）。
      ⇒ 现在：**配置项是唯一真相** ✓ 界面上能选 ✓ 能力总览从配置读 ✓ 三处自然一致 ✓。

    backend: edge（微软在线、免费、免装）/ melotts（本地、MIT 可商用、~100MB）/
             pyttsx3（用系统自带的语音，零依赖兜底）。
    没装的后端**不报错、如实说"未安装 + 怎么装"** ✓（见 `tts.py` 的 HINTS ✓）。
    """

    backend: str = "edge"
    # ★★ 2026-10-08：**音色**（用户："千问TTS 有点慢呢" 之后接着问音色 ✓）
    #   千问3-TTS 自带 9 个音色 ✓ 留空 = 用该后端的默认音色 ✓（qwen3tts 默认 vivian ✓）
    #   只有支持多音色的后端才认它 ✓（edge/pyttsx3/melotts 忽略 ✓）
    voice: str = ""
    # ★★ 2026-10-08：**长文本自动换快的引擎** ✗→✓
    #   为什么要这条：实测千问3(0.6B) **一句 4~5 秒的话要合成 5~12 秒** ✗
    #   而朗读是**边合成边播**（后端本来就是流式 ✓ 见 main.tts_speak ✓）——
    #   合成比播放慢 ⇒ 队列永远是空的 ⇒ 用户听到的是一句一顿 ✓
    #   ⇒ 长文本用在线引擎（edge 几乎瞬时 ✓）短句仍用本地（离线、最准 ✓）
    #   ★ `long_text_backend` 留空 = **关掉这条策略** ✓（想强制全程用本地就留空 ✓）
    long_text_backend: str = "edge"
    #: 超过多少字算"长文本"（按整段文字算 ✓ 不是按句 ✓）
    long_text_threshold: int = 300


class LicenseGateCfg(_StrictModel):
    """★★ 2026-10-09：**授权闸的开关** —— 这是 AGPL 合规的硬要求 ✗→✓

    为什么必须有这个开关（不是"想不想"的问题 ✗）：
      · 本仓自 2026-10-08 晚起采用 **AGPL-3.0** ✓
      · AGPL **第 10 条**：不得对许可证授予的权利**附加任何进一步限制** ✗
      · 而原来的 `can_create_task()` 会**试用 30 天后锁住"创建新任务"** ✗
        文案里还写着「本软件未经授权不得用于商业用途」✗ —— 这是**双重违规** ✗✗
        （既限制使用 ✓ 又限制商用 ✓ 而 AGPL 明文允许商用 ✓）
      ⇒ **公开的 AGPL 版必须关掉它** ✓（`enforce = False` 是**默认值** ✓）
      ⇒ **你卖的商用版**（闭源/授权版）把 `enforce = True` 打开即可 ✓
        —— 这就是双授权的标准做法 ✓ 一份代码两种分发 ✓

    ★ 默认值必须是 `False` ✗ —— 因为默认值跟着**公开仓库**走 ✓
      如果默认是 True ⇒ 谁 clone 下来都带着"30 天锁" ✗ 那就等于在分发一个违反 AGPL 的版本 ✓
    """

    enforce: bool = False
    #: 关掉时界面显示的说明（让人一眼看出"这版是开源版、没有使用限制"✓）
    note: str = "开源版（AGPL-3.0）· 无使用限制 · 源码见设置 → 关于"


class UICfg(_StrictModel):
    language: str = "zh-CN"
    theme: str = "dark"


class VideoCfg(_StrictModel):
    """云端视频引擎配置（video_gen 工具用）——第 41 班：从 model_extra 死路改为正式段。

    provider: minimax / wan / seedance / kling（详见 docs/video-providers.md）；
    Key 走环境变量（api_key_env），与 LLM Provider 同规矩不落盘。
    engines: 多引擎并存（如用户同时有海螺+万相 Key）——名字 → 覆盖配置，
    video_gen 工具传 engine 参数按名切换（"用海螺生成"就自动路由到 minimax）。
    """

    provider: str = ""  # 空 = 未启用云端视频（回退 ComfyUI 本地）
    api_base: str = ""  # 可选覆盖默认站点（如 MiniMax 国际站 https://api.minimax.io）
    api_key_env: str = ""
    model: str = ""  # 留空用各家默认（wan3.0-video / MiniMax-H3 / ...）
    resolution: str = "480P"  # 默认锁最便宜档（wan3.0 用 resolution 参数，wan2.x 用 size，均不锁会默认 1080P）
    ratio: str = "16:9"
    audio: bool | None = None  # wan3.0 音频开关（有声更贵）；None = 不传用服务端默认
    engines: dict[str, dict[str, Any]] = Field(default_factory=dict)  # 名字 → {provider?, api_key_env, model, resolution...}
    poll_seconds: float = 10.0
    timeout_seconds: float = 1800.0  # 实测：i2v 排队高峰可超 15 分钟（t2v 才 1-2 分钟），900s 会误杀已成功的任务


class UpdateCfg(_StrictModel):
    """升级检查（★ 2026-10-07 第 3 项：版本检查 + 升级提示）。

    ★ 三条与项目一贯口径一致的设计：
      ① **默认空 = 不检查** ✓ —— 不填地址就**一个字节都不往外发** ✓
         （本项目的定位是"本地优先、数据不出本机"✓ 偷偷上报版本号是它最不该干的事 ✗）
      ② **只查、不装** ✓ —— 查到新版本就**告诉你去哪下** ✓ 绝不自动下载/替换程序 ✗
         （自动替换自己 = 谁改了那个地址谁就能给你换程序 ✓ 那是供应链攻击的口子 ✗）
      ③ **查不到就说查不到** ✓ —— 没配地址/断网/格式怪，都如实说 ✓ 不许假装查过 ✗
    """

    #: 更新信息地址：返回 `{"version": "0.2.0", "notes": "…", "url": "https://…"}` 的 JSON ✓
    #: （将来指向你自己的仓库/发布页即可 ✓ 现在留空 = 不检查 ✓）
    check_url: str = ""


class BudgetCfg(_StrictModel):
    """每个 Key 的花费上限（2026-10-07 用户点名要的）—— 见 `app/budget.py` 的完整说明 ✓。

    ★ 两条必须先说清楚（免得给了**假的安心** ✗）：
      ① 这个上限**只管语言模型**那一档 —— 它是唯一按 token 算得出钱的 ✓；
         出图 / 出视频 / 语音是**按次或按秒**计费，本程序**拿不到单价** ✗ ⇒ 拦不住 ✓
         （编一个单价出来比不拦更糟 ✓ 见 pricing.py 文件头那条规矩 ✓）。
      ② **默认空 = 完全不拦** ✓ —— 与 `model.simple_model` 同规矩：不填就等于没这功能 ✓。

    结构：`{"XIAOMI_MIMO_API_KEY": {"limit": 20.0, "since": "2026-10-07T02:00:00+00:00"}}`
      · `limit`：元（累计到这么多就**停下**，不再往上发请求 ✓）
      · `since`：从这一刻开始计 ✓ —— 所以「清零重来」= 把 since 推到此刻 ✓
        （老账不删 ✓ 只是不计入这个上限 ✓ 这样"总共花了多少"永远查得到 ✓）
    """

    enabled: bool = True
    caps: dict[str, dict[str, Any]] = Field(default_factory=dict)


class AppConfig(_StrictModel):
    version: int
    server: ServerCfg = ServerCfg()
    model: ModelCfg = ModelCfg()
    license: LicenseGateCfg = LicenseGateCfg()
    executor: ExecutorCfg = ExecutorCfg()
    storage: StorageCfg = StorageCfg()
    skills: SkillsCfg = SkillsCfg()
    mcp: McpCfg = McpCfg()
    notify: NotifyCfg = NotifyCfg()
    memory: MemoryCfg = MemoryCfg()
    image: ImageCfg = ImageCfg()
    ui: UICfg = UICfg()
    video: VideoCfg = VideoCfg()
    asr: AsrCfg = AsrCfg()
    # ★ 2026-10-06：语音合成后端（"说"用哪个）—— 加这一段就是为了**口径统一** ✓ 见 TtsCfg 的注释 ✓
    tts: TtsCfg = TtsCfg()
    # ★ 2026-10-07：知识库向量档位（用户要求走本地 ✓ 见 KbCfg 的注释 ✓）
    kb: KbCfg = KbCfg()
    # ★ P0-5 成本可见：单价表（元/百万 token），形如
    #   {"mimo-v2.6-flash": {"in": 2.0, "out": 8.0, "cached": 0.5}}
    #   **默认空** —— 没填就只报 token、不报钱（价格会变，编一个数字比不报更糟）。
    pricing: dict[str, dict[str, float]] = {}
    # ★ 2026-10-07：**每个 Key 的花费上限**（用户点名要的："只有显示 ✗ 没有上限 ✗"）
    budget: BudgetCfg = Field(default_factory=lambda: BudgetCfg())
    # ★ 2026-10-07：升级检查（默认空 = 不检查 ✓ 见 UpdateCfg ✓）
    update: UpdateCfg = Field(default_factory=lambda: UpdateCfg())


def _default_config_path() -> Path:
    # PyInstaller 冻结后 __file__ 指向临时解包目录（sys._MEIPASS），parents[2] 会解析到
    # Temp——分发实测（2026-10-02）当场炸出：config.json 去了 C:\Windows\Temp。
    # 冻结态按 exe 所在目录解析（与 run.py 的 os.chdir 约定一致）；源码态按仓库根。
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "config.json"
    # app/config.py → parents[0]=app, [1]=backend, [2]=agent-shell
    return Path(__file__).resolve().parents[2] / "config.json"


def config_path() -> Path:
    """配置文件路径的**统一解析**：`AGENT_SHELL_CONFIG` > 仓库根（或冻结态 exe 目录）。

    ★ 为什么要有这个公开函数（2026-10-04 第二次血案）：
      `load_config()` 一直认 `AGENT_SHELL_CONFIG`（所以测试**读**的是临时配置），
      但 `main._save_config()` 当时自己算 `__file__` 的路径 ⇒ **写**的却是用户真实的
      `config.json`。后果实测：跑一次 `tests/test_key_persist.py`，用户配置就被写成
      测试那份（server.host 打回 127.0.0.1、storage.data_dir 指向 %TEMP%
      ⇒ 重启后"任务全消失" + 手机直连断掉）。8f 批只把写路径收成一个模块级变量，
      **没让它跟着环境变量走**，所以隔离仍是破的。现在读写同源：
      测试进程里 `main._CONFIG_PATH` 必然指向临时文件。
    """
    return Path(os.environ.get("AGENT_SHELL_CONFIG", "") or _default_config_path())


def load_config(path: str | None = None) -> AppConfig:
    """优先级：显式参数 > AGENT_SHELL_CONFIG 环境变量 > agent-shell/config.json"""
    p = Path(path or os.environ.get("AGENT_SHELL_CONFIG", "") or _default_config_path())
    if not p.exists():
        raise FileNotFoundError(
            f"配置文件不存在：{p}\n"
            "请先复制模板：copy contracts/config.example.json config.json"
        )
    try:
        # 用 utf-8-sig 读：**容忍 BOM**。老版记事本、Windows PowerShell 的
        # `Set-Content -Encoding UTF8`、不少编辑器保存 UTF-8 时会写 BOM，
        # 而带 BOM 的 JSON 用 utf-8 解会直接抛 "Unexpected UTF-8 BOM" ——
        # 用户只是改了个配置，产品却起不来，还报一个看不懂的错。
        # （这个坑是 2026-09-30 做隔离目录安装验证时踩出来的。）
        raw = json.loads(p.read_text("utf-8-sig"))
    except json.JSONDecodeError as e:
        raise ValueError(f"配置文件不是合法 JSON（{p}）：{e}") from e

    try:
        cfg = AppConfig.model_validate(raw)
    except Exception as e:
        # 契约三规则 3：指出具体字段，不静默回退
        raise ValueError(f"配置文件字段有误（{p}）：\n{e}") from e

    if cfg.version != 1:
        raise ValueError(f"不支持的配置版本：{cfg.version}（当前只支持 version=1）")
    return cfg
