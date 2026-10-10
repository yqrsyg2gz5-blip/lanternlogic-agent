/**
 * 模型预设的**单一事实来源**（第 48 班统一切换器；A5 批合并三处硬编码）。
 *
 * ★ 为什么要有这个文件（A5，审计台账 P1）：
 *   同一份"有哪些模型/怎么配"曾经**三处各写一份**，且互不一致：
 *     · 切换器（本文件原 6 项）      —— 首页 + 任务页两个弹层
 *     · 设置页（SettingsPanel 内联 8 项）
 *     · 后端 providers/__init__.py 的 _REGISTRY（10 个 **provider 实现别名**）
 *   后果（实测）：设置页能配 Claude（Anthropic），但切换器里没有它 —— 切走之后
 *   再也切不回来，且按钮上显示的是原始模型 id（`claude-sonnet-4-20250514`）
 *   而不是友好名。
 *   ⇒ 现在只有这一张表：切换器、设置页、显示名都从它取；
 *     后端侧的对应关系由 tests/test_model_presets_single_source.py 交叉钉住
 *     （每个 preset.provider 必须在 _REGISTRY 里，否则"配了也起不来"）。
 *
 * 字段口径：
 *   provider   —— 后端 _REGISTRY 的键（**必须显式写**：providerForModel 的
 *                 qwen 前缀匹配会把 ollama 的 qwen3.5:9b 错标成 qwen，十二轮 🔴3）
 *   label      —— 切换器弹层的短名（弹层窄；被 verify_model_unify.mjs 点击断言钉住）
 *   full_label —— 设置页的长名（信息更全，例如"DeepSeek（深度求索）"）
 *   desc/models—— 设置页的简介与可用模型清单
 */
export interface ModelPreset {
  provider: string;
  model_name: string;
  base_url: string;
  key_env: string;
  label: string;
  full_label: string;
  desc: string;
  models: string;
}

export const MODEL_PRESETS: Record<string, ModelPreset> = {
  mimo: { provider: 'mimo', model_name: 'mimo-v2.6-flash', base_url: 'https://api.xiaomimimo.com/v1', key_env: 'XIAOMI_MIMO_API_KEY', label: 'MiMo（小米）', full_label: '小米 MiMo', desc: '性价比高，默认推荐', models: 'v2.6-flash / v2.6-pro / v2.5 / v2.5-pro / v2.5-asr / v2.5-tts（2026-10-04 实拉在线列表核对）' },
  // ★ 2026-10-06：默认模型名**照官方定价页更新过** ✓（此前写的是 deepseek-chat / glm-4-flash /
  //   moonshot-v1-8k —— 那些是**旧名**，新用户照着选很可能起不来 ✗）。
  //   官方价也一并收进了后端 `app/pricing.py::OFFICIAL`（含来源与更新日期 ✓）。
  deepseek: { provider: 'deepseek', model_name: 'deepseek-flash', base_url: 'https://api.deepseek.com/v1', key_env: 'DEEPSEEK_API_KEY', label: 'DeepSeek', full_label: 'DeepSeek（深度求索）', desc: '推理强，缓存折扣大省钱（空闲时段半价）', models: 'deepseek-flash / deepseek-v4-pro（官方页 2026-10-06 核对）' },
  qwen: { provider: 'qwen', model_name: 'qwen-max', base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1', key_env: 'QWEN_API_KEY', label: '通义千问', full_label: '通义千问（阿里）', desc: '全家桶：还能做视频/作图/语音', models: 'qwen-max / plus / turbo / flash / qwen3-14b…（国内价见官方定价页）' },
  glm: { provider: 'glm', model_name: 'glm-5.3-flash', base_url: 'https://open.bigmodel.cn/api/paas/v4', key_env: 'GLM_API_KEY', label: '智谱 GLM', full_label: '智谱 GLM', desc: '免费档多（glm-4.7-flash 官方标价免费）', models: 'glm-5.3 / 5.3-flash / 5.3-flashx / 5.2 / 5 / 4.7-flash（免费）（官方页 2026-10-06 核对）' },
  kimi: { provider: 'kimi', model_name: 'kimi-k2.6', base_url: 'https://api.moonshot.cn/v1', key_env: 'KIMI_API_KEY', label: 'Kimi', full_label: 'Kimi（月之暗面）', desc: '长文本见长', models: 'kimi-k3 / k2.7-code / k2.6（国内站价 ¥6.5/¥27，k3 ¥20/¥100）' },
  anthropic: { provider: 'anthropic', model_name: 'claude-sonnet-4-20250514', base_url: 'https://api.anthropic.com', key_env: 'ANTHROPIC_API_KEY', label: 'Claude', full_label: 'Claude（Anthropic）', desc: '海外旗舰，需海外支付', models: 'claude-sonnet-4 / opus-4' },
  ollama: { provider: 'ollama', model_name: 'qwen3.5:9b', base_url: 'http://127.0.0.1:11434/v1', key_env: '', label: '本地（Ollama）', full_label: '本地模型（Ollama）', desc: '跑在你自己电脑上，断网可用，无需 Key；需先安装 Ollama 并下载模型', models: 'qwen3.5:9b / 27b 等本地模型' },
  // ★ 2026-10-10（用户点名要接的 ✓）：Bonsai 2 27B —— llama.cpp 本地服务
  //   启动方式：桌面「Bonsai2 本地模型」→ D:\Bonsai-demo\启动-Bonsai-单槽32K.bat ✓
  //   · 64K 上下文 / 单槽 / 4-bit KV / 带视觉（mmproj）· 约 9.5GB 显存
  //   · 与 ComfyUI 抢显存 ⇒ 出图/出视频前先关掉它（它自己的横幅就这么写的 ✓）
  //   · 无需 Key（key_env 空 ⇒ 后端按"本地服务不用 key"处理 ✓）
  bonsai: { provider: 'bonsai', model_name: 'bonsai-2-27b', base_url: 'http://127.0.0.1:8080/v1', key_env: '', label: '本地 Bonsai 2', full_label: '本地 Bonsai 2 27B（llama.cpp）', desc: '跑在你自己电脑上：64K 上下文、带视觉、断网可用、不花钱；先在桌面开「Bonsai2 本地模型」再切过来', models: 'bonsai-2-27b（Ternary-Bonsai-2-27B-PQ2，本机 D:\\Bonsai-demo）' },
  mock: { provider: 'mock', model_name: 'mock', base_url: '', key_env: '', label: '演示模式', full_label: 'Mock（演示模式）', desc: '假数据演示用，不调真模型——仅测试界面', models: '—' },
};

/** 切换器（首页 / 任务页弹层）展示的预设 = 上面这张表的全部（8 项）。
 *  ★ 独立导出而不是各处再写一份清单——A5 的根因就是"每个面各维护一份"。
 *    CSS 侧已确认弹层是 `bottom:110%` 向上生长的（底边固定），加项只往上长，
 *    与 usage badge 的相交区间不变（§2 那处已知取舍的几何不受影响）。 */
export const SWITCHER_PRESETS: ModelPreset[] = Object.values(MODEL_PRESETS);

/** 模型名 → 友好名（未知模型原样回传）。
 *  以前只有弹层里的 6 项能查到名字，跑 Claude/Mock 时按钮上直接显示原始 id。 */
export function displayNameForModel(model: string): string {
  if (!model) return '';
  const hit = SWITCHER_PRESETS.find((p) => p.model_name === model);
  return hit ? hit.label : model;
}

export function providerForModel(model: string): string {
  if (model.startsWith('mimo')) return 'mimo';
  if (model.startsWith('deepseek')) return 'deepseek';
  if (model.startsWith('glm')) return 'glm';
  if (model.startsWith('qwen')) return 'qwen';
  if (model.startsWith('moonshot')) return 'kimi';
  if (model.startsWith('claude')) return 'anthropic';
  if (model.startsWith('bonsai')) return 'bonsai';   // ★ 2026-10-10：本地 Bonsai 2（llama.cpp）
  return model; // 未知模型原样回传（后端按 model_name 落配置）
}
