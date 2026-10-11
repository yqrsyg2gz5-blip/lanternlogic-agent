# 视频生成服务商调研与接入指南（2026-10-01）

> 结论先行：**四家头部都有官方 API**，且全是同一种模式——提交任务拿 `task_id` → 轮询状态 → 下载视频 URL。
> LanternLogic Agent 的 `ApiVideoEngine`（backend/app/video.py）已按四家格式实现适配，**注册拿 Key → 填配置 → 就能测**。
> 「有的根本没有 API」的困惑来自：**消费端 App（即梦/海螺/可灵 App）是订阅会员制，API 是另一条按量计费的开发者产品线**——App 会员 ≠ API 权益，Agent 接的是 API 线。

## 一、四家对比（2026-10 调研）

| 服务商 | 模型 | 官方 API | 能力亮点 | 价格量级 | 接入难度 |
|---|---|---|---|---|---|
| **MiniMax**（海螺） | **H3** / H3-Max | ✅ `platform.minimax.cn` | 2K 原生 + 立体声、首尾帧/参考图/视频多模态、文生视频 4–15s | H3-Max 480P ≈ $0.06/s（聚合平台参考） | ⭐ 最简单：注册即建 Key |
| **阿里云百炼**（通义万相） | **wan3.0-video**（⚠️ 不是 wan3.0-t2v-*) | ✅ DashScope | **质量最佳**：原生音频、最长 30s、All-in-One | **480P 0.30 / 720P 0.60 / 1080P 1.20 元/秒**（官方标准价） | ⭐ 最顺：支付宝实名即开 |
| **字节**（火山方舟） | **Seedance 2.5**（即梦同款） | ✅ `ark.volcengine.com` | 单次 30s、场景切换、运镜 | 中等偏上 | ⭐⭐ 需开通模型（实名） |
| **快手**（可灵） | **Kling 3.0** | ✅ `kling.ai/dev` | 性价比最高（≈$0.075/s）、原生音频、3–15s | 0.3–0.5 元/10s（国内企业参考） | ⭐⭐ ak/sk 双密钥 |
| 生数 **Vidu** | Q2/S2 | ✅（platform.vidu + 百度千帆） | 多主体一致性（参考生视频之王） | 积分制 | ⭐⭐ 渠道多，文档分散 |

**LanternLogic Agent 内置适配优先级**：minimax（已按官方文档逐字段核对）→ wan（DashScope 官方异步模式）→ seedance（方舟官方模式）→ kling（官方 JWT 鉴权模式，未真机验证）。

## 二、注册路径（用户操作清单）

1. **阿里云百炼**（推荐第一个注册——有免费额度，先用它把全链路测通，不花钱）
   - https://bailian.console.aliyun.com → 支付宝实名 → 开通模型服务 → API-KEY 管理 → 创建 Key
   - 环境变量：`DASHSCOPE_API_KEY`
2. **MiniMax**（视频质量第一梯队，2K+原生音频）
   - https://platform.minimax.cn → 注册 → 充值（按量）→ 接口密钥
   - 环境变量：`MINIMAX_API_KEY`
3. **火山方舟**（Seedance 2.5 = 即梦的模型）
   - https://console.volcengine.com/ark → 实名认证 → 开通模型 → API Key
   - 环境变量：`ARK_API_KEY`
4. **可灵**
   - https://kling.ai/dev → 注册 → 创建应用 → 拿 AccessKey(AK) + SecretKey(SK)
   - 环境变量：`KLING_API_KEY`，值格式 `ak:sk`（两段拼一起，冒号分隔）

⚠️ **不要用 GitHub 上的「即梦逆向 API」类项目**——违反平台条款、随时失效、有法律风险。官方渠道都在上面。

## 三、LanternLogic Agent 配置方法

`config.json` 的 `model` 段加 `video` 子对象（model_extra 承载，不新增顶层段）：

```json
{
  "model": {
    "provider": "openai_compatible",
    "model_name": "mimo-v2.6-flash",
    "...": "（现有配置不动）",
    "video": {
      "provider": "minimax",
      "api_key_env": "MINIMAX_API_KEY",
      "model": "MiniMax-H3",
      "resolution": "768P",
      "ratio": "16:9"
    }
  }
}
```

| 字段 | 说明 |
|---|---|
| `provider` | `minimax` / `wan` / `seedance` / `kling`（四选一） |
| `api_key_env` | Key 的环境变量名（**Key 本身不进配置文件**，与 LLM Provider 同规矩） |
| `api_base` | 可选，覆盖默认站点（如 MiniMax 国际站填 `https://api.minimax.io`） |
| `model` | 各家模型名，留空用默认：`MiniMax-H3` / `wan3.0-t2v-plus` / `doubao-seedance-2-5` / `kling-v3` |
| `resolution` / `ratio` | 默认 768P / 16:9（各家支持档位不同，按需调） |
| `poll_seconds` / `timeout_seconds` | 轮询间隔（默认 10s）/ 总超时（默认 900s） |

之后在对话里直接说「用 video_gen 生成……」即可；生成 1–5 分钟属正常（任务会停在 running，观察流里有轮询过程）。

## 四、各家端点速查（适配器实现依据）

| 家 | 提交 | 查询 | 鉴权 | 成功状态 | 视频地址 |
|---|---|---|---|---|---|
| minimax | `POST {base}/v2/video_generation` | `GET /v2/query/video_generation/{tid}` | Bearer | `task.status=succeeded` | `task.file.download_url` |
| wan | `POST /api/v1/services/aigc/video-generation/video-synthesis`（头带 `X-DashScope-Async: enable`） | `GET /api/v1/tasks/{tid}` | Bearer | `output.task_status=SUCCEEDED` | `output.video_url` |
| seedance | `POST {base}/api/v3/contents/generations/tasks` | `GET /api/v3/contents/generations/tasks/{tid}` | Bearer | `status=succeeded` | `content.video_url` |
| kling | `POST {base}/v1/videos/text2video` | `GET /v1/videos/text2video/{tid}` | Bearer **JWT**（ak 签发，sk 加密） | `data.task_status=succeed` | `data.task_result.videos[0].url` |

- MiniMax 官方文档（已逐字段核对）：https://platform.minimax.io/docs/guides/video-generation
- 万相 3.0 API 参考：https://help.aliyun.com/zh/model-studio/wan3-video-generation-api-reference
- Seedance（方舟）创建任务：https://www.volcengine.com/docs/ark/create-video-generation-task-api
- 可灵开发者定价：https://kling.ai/dev/pricing

## 五、验证状态（如实）

| 适配器 | 验证方式 | 状态 |
|---|---|---|
| minimax | mock 官方形状全链路（提交→轮询→下载 mp4 落盘）+ **真实网关连通性**（Key 已验证认证通过，402=等充值） | ✅ 代码就绪，等充值 |
| **wan** | **✅ 真实出片成功**（2026-10-01）：wan2.2-t2v-plus 提交→41s→2.36MB 合法 MP4；真实欠费报错也验证了错误映射 | ✅ **真 Key 全链路验证过**（该账户现已欠费） |
| seedance | 请求形状 + 解析逻辑 mock 冒烟 | ✅ 代码就绪，待真 Key |
| kling | JWT 签名单测 + 请求形状冒烟（**JWT 字段未对官方文档逐字核对**） | 🟡 首 calls 若 401 核对 JWT 字段 |

### 真实 Key 实测的结论（2026-10-01，持续更新）

1. **wan3.0 的真实模型名是 `wan3.0-video`**——`wan3.0-t2v-plus` / `wan3.0-t2v` / `wan2.6-t2v-plus` / `wan2.5-t2v-plus` 全部 "Model not exist"。实测 wan3.0-video：142s 出片，**请求 480P 也强制超分 1080P/30fps**（用量回执 SR:1080），size 参数对 3.0 无效——它的成本杠杆只有时长
2. wan2.2-t2v-plus 实测可用（41s 出片）：**必须显式传 size**，不传默认 1080P（0.3×… wan3.0 官方价 1080P=1.2 元/秒，一条 5s 6 元）；适配器只在 wan2.x 时发 size（480P 锁定）
3. **欠费报错**：wan 返回 HTTP 400 `Arrearage`、minimax 返回 402 `insufficient_balance`——适配器都映射为"去控制台充值"的行动指引
4. **提示词质量决定出片上限**：已做成 `视频生成` 技能（提示词公式+质量清单+成本档位）+ video_gen 工具描述强制导演级提示词。 wan3.0 + 结构化提示词实测：金色波光/海鸥/运镜全中，自带浅景深（样片：workspace/视频实测/）

**已知限制**：图生视频/参考生视频参数（首帧图、参考图）尚未暴露到 video_gen 工具——wan3.0-video 本身支持（All-in-One），工具层下一步接。
