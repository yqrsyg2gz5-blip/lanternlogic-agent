"""内置工具集 —— name + JSON Schema + 描述。

22 个工具：文件/命令 5 + 联网 2 + 浏览器实况 5 + image_gen + image_read + speak + video_gen + update_plan + load_skill + wide_research。
task_done 不是执行器动作：loop 看到它就交付收尾；update_plan / load_skill 同理由 loop 特判。
"""
from __future__ import annotations

from typing import Any

_TOOL_DEFS: dict[str, dict[str, Any]] = {
    "list_dir": {
        "description": "列出目录内容：路径相对任务工作区根；工作区之外的绝对路径仅限 allowed_dirs 已授权目录。",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "相对工作区的目录路径，默认 ."}},
        },
    },
    "file_read": {
        "description": "读取文本文件的内容（超长自动截断）：路径相对工作区根，工作区外的绝对路径仅限已授权目录。",
        "parameters": {
            "type": "object",
            "required": ["path"],
            "properties": {"path": {"type": "string", "description": "相对工作区的文件路径"}},
        },
    },
    "file_write": {
        "description": "写入/覆盖文本文件（自动建父目录）：默认限工作区内；工作区外仅 allowed_dirs 已授权目录可写。",
        "parameters": {
            "type": "object",
            "required": ["path", "content"],
            "properties": {
                "path": {"type": "string", "description": "相对工作区的文件路径"},
                "content": {"type": "string", "description": "要写入的全文"},
            },
        },
    },
    "shell_exec": {
        "description": "执行一条 shell 命令（工作目录为任务工作区，但可操作本机任意路径，例如把产物复制到桌面）。危险命令会先进入等待审批状态。",
        "parameters": {
            "type": "object",
            "required": ["command"],
            "properties": {"command": {"type": "string", "description": "完整命令行"}},
        },
    },
    "task_done": {
        "description": (
            "任务完成：交付最终消息与产物清单，结束循环。每次任务必须以它收尾。"
            "**outcome 必须如实填写**：真做到了写 success；只完成一部分写 partial；"
            "没做到（工具失败、环境不支持等）写 failed——把没做成的事报成 success 比坦白失败更糟。"
            "联网调研过的任务必须用 sources 附来源清单，且只填本次真实访问过的链接（凭空写的会被系统丢弃）。"
        ),
        "parameters": {
            "type": "object",
            "required": ["message"],
            "properties": {
                "message": {"type": "string", "description": "给用户的交付说明"},
                "attachments": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "产物文件路径列表（必须是任务工作区内真实存在的文件）",
                },
                "outcome": {
                    "type": "string",
                    "enum": ["success", "partial", "failed"],
                    "description": "交付结果：success=已完成 / partial=部分完成 / failed=未完成（如实填写）",
                },
                "sources": {
                    "type": "array",
                    "description": "来源清单（研究/调研类交付必填）：仅填本次真实访问过的链接",
                    "items": {
                        "type": "object",
                        "required": ["title", "url"],
                        "properties": {
                            "title": {"type": "string", "description": "来源标题"},
                            "url": {"type": "string", "description": "来源链接"},
                        },
                    },
                },
            },
        },
    },
    "update_plan": {
        "description": "更新任务计划（编号步骤与状态）。动手前先建计划；每完成或推进一步就更新一次，并附 reflection 反思。",
        "parameters": {
            "type": "object",
            "required": ["steps"],
            "properties": {
                "steps": {
                    "type": "array",
                    "description": "步骤列表（按执行顺序）",
                    "items": {
                        "type": "object",
                        "required": ["text", "status"],
                        "properties": {
                            "text": {"type": "string", "description": "步骤内容"},
                            "status": {"type": "string", "enum": ["pending", "in_progress", "done"]},
                        },
                    },
                },
                "reflection": {"type": "string", "description": "当前进展反思（可选）"},
            },
        },
    },
    "browser_navigate": {
        "description": "在可见浏览器窗口打开 URL（窗口在用户桌面实时可见，用户可随时人工接管页面后再交回）。",
        "parameters": {
            "type": "object",
            "required": ["url"],
            "properties": {"url": {"type": "string", "description": "完整 URL 或域名（自动补 https://）"}},
        },
    },
    "browser_snapshot": {
        "description": "读取当前页面：可见文本 + 可交互元素清单（输入框/按钮/链接），作为后续 browser_click/browser_type 的依据。每次操作后先 snapshot 再行动。",
        "parameters": {"type": "object", "properties": {}},
    },
    "browser_click": {
        "description": "点击页面上的元素（按可见文本匹配，如按钮/链接文字、输入框占位符）。",
        "parameters": {
            "type": "object",
            "required": ["text"],
            "properties": {"text": {"type": "string", "description": "元素可见文本"}},
        },
    },
    "browser_type": {
        "description": "在当前聚焦的输入框逐字输入文本（先用 browser_click 点输入框聚焦）。",
        "parameters": {
            "type": "object",
            "required": ["text"],
            "properties": {"text": {"type": "string", "description": "要输入的内容"}},
        },
    },
    "browser_key": {
        "description": "按一个键（Enter/Escape/Tab 等），触发搜索或提交。",
        "parameters": {
            "type": "object",
            "required": ["key"],
            "properties": {"key": {"type": "string", "description": "键名"}},
        },
    },
    "web_search": {
        "description": "联网搜索：按关键词搜网页，返回标题/链接/摘要列表（前 8 条）。⚠️ 如果返回「无结果」，不要反复重试——直接改用 web_fetch 抓取目标网站页面（如 bing.com/search?q=关键词 或已知的项目 GitHub/官网 URL）获取信息。",
        "parameters": {
            "type": "object",
            "required": ["query"],
            "properties": {"query": {"type": "string", "description": "搜索关键词"}},
        },
    },
    "web_fetch": {
        "description": "抓取网页正文：给定 URL 返回页面文本（HTML 自动剥标签，超长截断）。配合 web_search 的结果链接深入阅读。",
        "parameters": {
            "type": "object",
            "required": ["url"],
            "properties": {"url": {"type": "string", "description": "完整 URL（http/https）"}},
        },
    },
    "video_gen": {
        "description": "生成视频（付费，每条几毛到几元）。**提示词决定出片质量**：不要原样转述用户的话，要扩写成导演级提示词——主体动作+环境场景+镜头语言（景别/运镜/景深）+光影色调+氛围风格，60–200 字。有视频生成技能（/视频生成）时先加载它。生成需 1–5 分钟属正常。视频保存到工作区，交付时用 attachments 带上文件名。多镜头叙事拆成多次调用、每条一个镜头。",
        "parameters": {
            "type": "object",
            "required": ["prompt"],
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "结构化导演级提示词（主体+动作+镜头+光影+风格）。示例：清晨海边日出：太阳从海平线缓缓升起，金色阳光在海面拉出长长的波光，几只海鸥掠过镜头前景。镜头低角度缓慢上摇，浅景深，暖金色调，电影感光影，胶片质感。",
                },
                "seconds": {"type": "integer", "description": "时长（秒，3–15，默认 5；一个动作 5s 够，多镜头拆多次调用）"},
                "engine": {"type": "string", "description": "可选：指定引擎（minimax/wan/seedance/kling/comfyui）。不填用配置默认；报错信息会列出当前可用的引擎。"},
                "first_frame": {"type": "string", "description": "可选：首帧图（工作区相对路径）——**降低抽卡的最强手段**：先用 image_gen 出图（免费）或用用户上传的图把构图定死，视频只负责动。对构图/主体一致性要求高时务必使用。"},
            },
        },
    },
    "kb_search": {
        "description": "搜索知识库：查询用户导入的文档/资料（语义检索）。回答涉及用户业务资料的问题前先用它。",
        "parameters": {
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {"type": "string", "description": "检索问题（用关键词，语义匹配）"},
                "name": {"type": "string", "description": "可选：限定某个知识库名"},
                "top_k": {"type": "integer", "description": "返回条数（默认 5）"},
            },
        },
    },
    "speak": {
        "description": "语音合成：把一段文字转成语音文件（WAV），交付时用 attachments 带上文件名。适合朗读总结、生成播报。",
        "parameters": {
            "type": "object",
            "required": ["text"],
            "properties": {"text": {"type": "string", "description": "要朗读的文字（中文）"}},
        },
    },
    "video_join": {
        "description": "拼接视频：把工作区里多段视频按顺序合成一条长视频（ffmpeg 重编码，不同分辨率/帧率也能接）。各家引擎单次只出 5–30 秒，**长视频/AI 漫剧的正确做法**：先逐镜 video_gen（同引擎同档位保持参数一致），再用本工具按分镜顺序拼接。交付时用 attachments 带上成片文件名。",
        "parameters": {
            "type": "object",
            "required": ["files"],
            "properties": {
                "files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "按播放顺序排列的工作区视频文件名（如 [\"shot_001.mp4\", \"shot_002.mp4\"]）",
                },
                "output": {"type": "string", "description": "输出文件名（可选，默认自动命名 joined_xxx.mp4）"},
            },
        },
    },
    "image_read": {
        "description": "看图：读取工作区（或授权目录）中的图片文件，由视觉模型描述内容。适合查看截图、验证生成的图片、理解图表。",
        "parameters": {
            "type": "object",
            "required": ["path"],
            "properties": {
                "path": {"type": "string", "description": "图片路径（相对工作区，png/jpg/gif/webp，≤8MB）"},
                "question": {"type": "string", "description": "可选：针对图片的具体问题"},
            },
        },
    },
    "code_check": {
        "description": "代码校验：对工作区里的代码文件做确定性语法检查（Python=py_compile / JS=node --check / JSON 解析 / HTML 标签平衡）。写完代码后必须调用它验证，再交付。",
        "parameters": {
            "type": "object",
            "required": ["path"],
            "properties": {"path": {"type": "string", "description": "相对工作区的文件路径"}},
        },
    },
    "image_gen": {
        "description": "生成图像：按文字描述作图（本地 ComfyUI + SDXL 模型）。图片保存到工作区，交付时用 attachments 带上文件名。",
        "parameters": {
            "type": "object",
            "required": ["prompt"],
            "properties": {
                "prompt": {"type": "string", "description": "图像描述（英文效果更好，详细具体）"},
                "width": {"type": "integer", "description": "宽度（默认 512）"},
                "height": {"type": "integer", "description": "高度（默认 512）"},
            },
        },
    },
    "wide_research": {
        "description": "并行 Wide Research：把一个调研主题拆成 2-8 个相互独立的要点，每个要点派一个并行子 Agent 同时研究，全部完成后自动生成汇总报告任务。调用后请立即用 task_done 结束当前任务。",
        "parameters": {
            "type": "object",
            "required": ["input", "items"],
            "properties": {
                "input": {"type": "string", "description": "总体调研主题"},
                "items": {"type": "array", "items": {"type": "string"}, "description": "2-8 个相互独立的研究要点"},
            },
        },
    },
    "load_skill": {
        "description": "加载技能的完整说明（SKILL.md）。系统提示里有可用技能清单；判断某技能与当前任务相关时调用它。resource 可选：加载技能附带资源文件（Level3）。",
        "parameters": {
            "type": "object",
            "required": ["name"],
            "properties": {
                "name": {"type": "string", "description": "技能名（见系统提示的可用技能清单）"},
                "resource": {"type": "string", "description": "可选：技能目录下的资源文件名"},
            },
        },
    },
}

# loop / 执行器认识的工具名（task_done 由 loop 特判，不进执行器）
ALL_TOOLS = set(_TOOL_DEFS)

# MCP 动态工具（第 41 班"万物皆可插"）：main.py 启动时把外部 server 的工具
# 注入 _TOOL_DEFS（名字 mcp__<server>__<tool>），loop 里分派给 mcp 注册表调用。
MCP_TOOL_PREFIX = "mcp__"


def register_mcp_tool(server: str, tool: dict[str, Any]) -> str | None:
    """把一个 MCP 工具定义注册进工具表。返回注册名；非法/重名返回 None。"""
    name = str(tool.get("name") or "").strip()
    if not name or not all(c.isalnum() or c in "_-." for c in name):
        return None
    reg = f"{MCP_TOOL_PREFIX}{server}__{name}"
    if reg in _TOOL_DEFS:
        return None
    schema = tool.get("inputSchema")
    params = schema if isinstance(schema, dict) and schema.get("type") == "object" else {"type": "object", "properties": {}}
    desc = str(tool.get("description") or f"MCP 工具（来自 server「{server}」）")
    _TOOL_DEFS[reg] = {"description": f"[MCP:{server}] {desc}", "parameters": params}
    return reg


def mcp_tool_names() -> set[str]:
    return {n for n in _TOOL_DEFS if n.startswith(MCP_TOOL_PREFIX)}


def unregister_mcp_tool(reg_name: str) -> bool:
    """从工具表移除一个 MCP 工具（server 停用/测试清理用）。"""
    return _TOOL_DEFS.pop(reg_name, None) is not None


def openai_tools() -> list[dict[str, Any]]:
    """转成 OpenAI tools 数组格式（DeepSeek/Qwen/GLM/Ollama 兼容）。"""
    return [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": spec["description"],
                "parameters": spec["parameters"],
            },
        }
        for name, spec in _TOOL_DEFS.items()
    ]


def validate_args(name: str, args: dict[str, Any]) -> str | None:
    """必填参数粗校验；返回错误文案或 None。"""
    spec = _TOOL_DEFS.get(name)
    if spec is None:
        return f"未知工具：{name}"
    missing = [
        k for k in spec["parameters"].get("required", []) if k not in args
    ]
    if missing:
        return f"缺少必填参数：{missing}"
    return None
