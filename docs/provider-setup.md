# 模型接入指南（Provider Setup）

> LanternLogic Agent 的模型大脑通过 `config.json` 的 `model` 段配置——改三行就能切换任何主流模型。
> **API Key 只存环境变量名（`api_key_env`），值绝不写进配置文件**（契约三规则 1）。

---

## 快速切换（改 config.json 的 model 段三行）

```json
{
  "model": {
    "provider": "<提供者名>",
    "model_name": "<模型名>",
    "base_url": "<API 地址>",
    "api_key_env": "<环境变量名>"
  }
}
```

然后设置环境变量（PowerShell）：
```powershell
$env:<环境变量名> = "<你的 API Key>"
```

---

## 全部 10 个提供者

### 1. DeepSeek（推荐——性价比最高的国产模型）

```json
{ "provider": "deepseek", "model_name": "deepseek-chat", "base_url": "https://api.deepseek.com/v1", "api_key_env": "DEEPSEEK_API_KEY" }
```
- **Key 获取**：https://platform.deepseek.com → API Keys → 创建
- **价格**：约 ¥1/百万输入 token（最便宜的旗舰级模型之一）
- **工具调用**：✅ | **流式**：✅

### 2. 通义千问 Qwen（阿里云）

```json
{ "provider": "qwen", "model_name": "qwen-max", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "api_key_env": "QWEN_API_KEY" }
```
- **Key 获取**：https://dashscope.console.aliyun.com → API-KEY 管理
- **价格**：¥2-40/百万 token（按模型档次）
- **工具调用**：✅ | **流式**：✅
- **新用户免费额度**：100 万 token

### 3. 智谱 GLM

```json
{ "provider": "glm", "model_name": "glm-4-flash", "base_url": "https://open.bigmodel.cn/api/paas/v4", "api_key_env": "GLM_API_KEY" }
```
- **Key 获取**：https://open.bigmodel.cn → API Keys
- **价格**：GLM-4-Flash **免费**；GLM-4-Plus ¥50/百万
- **工具调用**：✅ | **流式**：✅

### 4. Kimi（月之暗面）

```json
{ "provider": "kimi", "model_name": "moonshot-v1-8k", "base_url": "https://api.moonshot.cn/v1", "api_key_env": "KIMI_API_KEY" }
```
- **Key 获取**：https://platform.moonshot.cn → API Keys
- **价格**：¥12/百万 token（8k 上下文）
- **工具调用**：✅ | **流式**：✅

### 5. 小米 MiMo

```json
{ "provider": "mimo", "model_name": "mimo-v2.6-flash", "base_url": "https://api.xiaomimimo.com/v1", "api_key_env": "XIAOMI_MIMO_API_KEY" }
```
- **Key 获取**：https://api.xiaomimimo.com → 创建 API Key
- **模型族**：mimo-v2.5（基础）/ mimo-v2.5-pro（强）/ mimo-v2.6-flash（快）/ mimo-v2.6-pro
- **额外能力**：TTS（mimo-v2.5-tts）、语音克隆（mimo-v2.5-tts-voiceclone）、ASR（mimo-v2.5-asr）
- **工具调用**：✅ | **流式**：✅

### 6. Anthropic Claude（国际——需科学上网）

```json
{ "provider": "anthropic", "model_name": "claude-sonnet-4-20250514", "base_url": "https://api.anthropic.com", "api_key_env": "ANTHROPIC_API_KEY" }
```
- **Key 获取**：https://console.anthropic.com → API Keys
- **价格**：$3/百万输入 + $15/百万输出（Sonnet）
- **工具调用**：✅（已按 Anthropic 原生格式转换：`tool_calls`→`tool_use`、`role:tool`→`tool_result`、
  system 并回顶层；有单测，但**尚未用真 Key 跑端到端**）
- **流式**：✅（第 41 班补齐：`content_block_delta` 的 text_delta 即时回传、tool_use 按
  input_json_delta 分片重组、usage 从 message_start/message_delta 两处收集；已用**严格按官方
  SSE 形状的模拟服务**端到端验证——文本流/工具流/非流式回归全过，仍未用真 Key 验收）
- **注意**：API 格式与 OpenAI 完全不同——本项目已做原生适配（非兼容层）。
  2026-09-30 前这里标注的「工具调用✅ 流式✅」是**不实的**：当时 history 被原样转发，
  第一次工具调用后的第二轮会 400，且所有 system 消息被丢弃（项目指令/技能清单静默失效）。现已修复前者。

### 7. Ollama（本地模型——免费、隐私、离线）

```json
{ "provider": "ollama", "model_name": "qwen3.5:9b", "base_url": "http://127.0.0.1:11434/v1", "api_key_env": null }
```
- **安装**：https://ollama.com → 下载安装 → `ollama pull qwen3.5:9b`
- **价格**：**免费**（跑在你自己的 GPU 上）
- **工具调用**：取决于模型（qwen3.5 支持 tools ✓）
- **推荐**：qwen3.5:9b（快+支持工具）、llava:13b（视觉）

### 8. ComfyUI 图像生成（本地——免费）

```json
{ "executor": { "comfyui_url": "http://127.0.0.1:8189", "image_checkpoint": "Juggernaut-XL_v9.safetensors" } }
```
- **安装**：https://github.com/comfyanonymous/ComfyUI → 下载 → 启动
- **模型**：放 `models/checkpoints/` 下（推荐 RealVisXL_V5.0 或 Juggernaut-XL）
- **价格**：**免费**（本地 GPU 出图）
- **工具**：image_gen（Agent 自动调用）

### 9. 自定义 OpenAI 兼容端点

任何兼容 OpenAI Chat Completions 格式的 API 都能用：
```json
{ "provider": "openai_compatible", "model_name": "<模型名>", "base_url": "<你的端点>/v1", "api_key_env": "<环境变量名>" }
```
已验证兼容：SiliconFlow、ModelScope、vLLM、LM Studio、text-generation-webui

### 10. Mock（无 Key 演示——默认）

```json
{ "provider": "mock", "model_name": "mock", "base_url": null, "api_key_env": null }
```
- 无需任何配置，前端三步剧本演示 Agent Loop 交互

---

## 环境变量设置（永久生效）

**PowerShell（当前用户）**：
```powershell
[Environment]::SetEnvironmentVariable("DEEPSEEK_API_KEY", "sk-your-key", "User")
```

**系统环境变量（图形界面）**：
系统属性 → 高级 → 环境变量 → 用户变量 → 新建

**⚠️ 安全提醒**：API Key 绝不写入 config.json 或任何文件——只存环境变量（契约三规则 1）。


---

## 中文语音包（TTS）安装

LanternLogic Agent 默认已带两层 TTS：**Edge-TTS**（微软神经语音，免费免 Key，联网可用，音质好）+ **Windows TTS**（离线兜底，音质偏机械）。

想要更自然、可商用的本地语音，运行一键安装器：
```powershell
scripts\安装中文语音包.bat
```
脚本**钉死内容哈希**：MeloTTS 的 commit + tarball sha256（从官方 `codeload.github.com` 拉取），NLTK 数据六包逐个校验 sha256（从 jsdelivr CDN 拉取）。**这几个下载校验步骤是 fail-closed 的**：哈希不符即拒绝安装并以非零码退出。不再经任何第三方代理拉取代码（★ 但仍会在本机执行该 tarball 的构建后端，只是来源是哈希校验过的官方源）。上游源可自选（内容寻址，换源不改变校验）：`AGENT_SHELL_TTS_GIT_MIRROR` 覆盖 MeloTTS 源、`AGENT_SHELL_TTS_MIRROR` 覆盖 NLTK 数据源。⚠️ 注意覆盖边界：随后的 `pip install` 依赖步（torchaudio 等）与"NLTK 数据已存在则跳过"这两步**不参与哈希校验**。安装 MeloTTS（MIT 许可，~100MB，CPU 实时）。

**TTS 后端选择**（`POST /tasks/{id}/tts` 的 `backend` 参数）：
| backend | 音质 | 联网 | 许可 |
|---------|------|------|------|
| `edge`（默认） | 自然 | 需要 | 免费但为非官方接口（自用无碍，商业产品建议加本地后端） |
| `pyttsx3` | 机械 | 不需要 | 系统组件 |
| `melotts` | 较好 | 不需要（装好后） | MIT，可商用 |

**用户自行安装更强的 TTS**（产品预留接口，分发责任在用户）：GPT-SoVITS（MIT，声音克隆）、ChatTTS（CC BY-NC，禁止商用——仅供个人研究）。