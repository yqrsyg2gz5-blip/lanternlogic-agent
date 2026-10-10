"""长期记忆库（第 41 班）——"越用越懂你"的地基。

原理：模型不变，harness 记——每次任务交付后由大模型自动提取
"值得长期记住的事"（用户偏好/纠错/身份事实），存进本地 JSON；
新任务开始时注入系统提示，Agent 天生带着对你的了解上场。

安全与透明（用户拍板的底线）：
  · 全部本地存储（data/memory/memory.json），用户可直接查看/编辑/删除
  · 写入前过打码管线（密钥值不进记忆）
  · 设置可整体开关；条数上限防膨胀（FIFO 淘汰）
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_EXTRACT_PROMPT = """你是记忆提取器。从下面的任务记录中提取"值得跨任务长期记住"的信息。

只提取这几类（都是关于**用户**的）：
1. 用户偏好（如：喜欢简洁回复/只要代码不要解释）
2. 用户身份与事实（如：公司名/服务器位置/常用技术栈）
3. 纠错记录（如：上次用户纠正过它不要编造链接）

**绝对不要提取**：
- 任何关于 AI/助手/Agent 自身身份的信息（叫什么名字、什么部门、用什么工具——那是系统配置，不是记忆）
- 只在本次对话/任务语境成立的临时设定（临时身份、临时格式要求）
- 任务本身的执行细节

没有值得记的就返回 []。

只输出 JSON 数组，每项格式 {"type": "preference|fact|correction", "content": "一句话"}，每条不超过 100 字，最多 5 条。

【任务输入】
{task_input}

【最终交付】
{reply}"""


class MemoryStore:
    """长期记忆存储（线程安全；本地 JSON 持久化）。"""

    def __init__(self, path: Path, max_entries: int = 200):
        self.path = Path(path)
        self.max_entries = max_entries
        self._lock = threading.Lock()

    def _load(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (json.JSONDecodeError, OSError):
            # 复审：坏文件改名保留——防止下一次 _save 基于空列表覆写，历史记忆被永久抹掉
            try:
                import time
                self.path.replace(self.path.with_suffix(f".corrupt-{int(time.time())}"))
            except OSError:
                pass
            return []

    def _save(self, entries: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(entries, ensure_ascii=False, indent=1), "utf-8")

    def all(self) -> list[dict[str, Any]]:
        with self._lock:
            return self._load()

    def add(self, items: list[dict[str, Any]], source_task: str) -> int:
        """追加记忆（按内容去重；超上限 FIFO 淘汰最旧）。返回实际新增数。

        验证报告 24：头部声明"写入前过打码管线"但此前实现只依赖调用方打码——
        现在 add() **自身**也过一遍 redact_text（纵深防御：换任何调用方都不漏）。
        """
        if not items:
            return 0
        from .redact import redact_text
        with self._lock:
            entries = self._load()
            exist = {str(e.get("content", "")).strip() for e in entries}
            now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            added = 0
            for it in items:
                content = redact_text(str(it.get("content", "")).strip())[:200]
                if not content or content in exist:
                    continue
                entries.append({
                    "ts": now,
                    "source": source_task,
                    "type": str(it.get("type") or "fact")[:20],
                    "content": content,
                })
                exist.add(content)
                added += 1
            if added:
                entries = entries[-self.max_entries:]
                self._save(entries)
            return added

    def clear(self) -> int:
        with self._lock:
            n = len(self._load())
            self._save([])
            return n

    def format_for_prompt(self, max_chars: int = 1500) -> str:
        """注入系统提示的记忆块（最新的排前，截断防撑爆前缀——缓存红线：任务期内不变）。"""
        entries = self._load()
        if not entries:
            return ""
        lines = []
        total = 0
        for e in reversed(entries):  # 最新优先
            line = f"- [{e.get('type', 'fact')}] {e.get('content', '')}"
            if total + len(line) > max_chars:
                break
            lines.append(line)
            total += len(line)
        if not lines:
            return ""
        return "[长期记忆——来自以往任务，回答时参考]\n" + "\n".join(reversed(lines))


def parse_extraction(raw: str) -> list[dict[str, Any]]:
    """解析提取器的输出（容错：剥代码围栏、截取 JSON 数组、逐项校验）。"""
    if not raw:
        return []
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end <= start:
        return []
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return []
    out: list[dict[str, Any]] = []
    for it in data if isinstance(data, list) else []:
        if not isinstance(it, dict):
            continue
        content = str(it.get("content") or "").strip()
        if not content:
            continue
        out.append({"type": str(it.get("type") or "fact"), "content": content})
    return out[:5]
