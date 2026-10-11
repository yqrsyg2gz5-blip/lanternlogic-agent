# -*- coding: utf-8 -*-
"""经验库（lessons）—— "这类活上次怎么栽的" ✓

## 为什么要有它

今天真跑里同一个坑**反复出现** ✓：交付里不贴真实输出 ⇒ 被打回 ✓ ⇒ 下一个任务**又**犯 ✓。
每一次都重烧十几万 tok ✗ —— 而"上次为什么栽"这件事，**系统是知道的** ✓（验收意见白纸黑字 ✓），
只是**从来没人把它带给下一个干活的人** ✗。

这个模块就干一件事：**把栽过的坑记下来，下次派同类活时带上** ✓。

## 设计（刻意保守 ✓）

· **只记"被打回/失败"的原因** ✓，不记成功经验（后者信息量低 ✓ 还占上下文 ✗）
· **按关键词挑相关**（不做向量检索 ✓ 够用且零依赖 ✓）
· **硬上限**：一次最多带 `MAX_INJECT` 条 / `MAX_CHARS` 字 ✓ ——
  注入的经验**也是上下文** ✓ 不能为了"长记性"把每次派活都撑大 ✗
· **落盘** ✓（`backend/data/lessons.json` ✓ 重启仍在 ✓），**去重** ✓（同一句只留一条 ✓ 记次数 ✓）

## 它不该做什么（边界 ✓）

· 不做"自动修改提示词"（那是另一件事 ✓ 也更危险 ✗）
· 不记人名/隐私（只记"什么原因" ✓）
"""
from __future__ import annotations

import json
import pathlib
import re
import threading
import time
from typing import Any

MAX_KEEP = 200          # 库里最多留多少条（超了丢最久没用过的 ✓）
MAX_INJECT = 3          # 一次最多注入几条
MAX_CHARS = 500         # 注入的总字数上限（含标题 ✓）


def _tokens(text: str) -> set[str]:
    """极简分词：中文按 2 字滑窗 ✓ 英文数字按词 ✓ —— 够用来算"像不像" ✓。"""
    t = str(text or "").lower()
    out = set(re.findall(r"[a-z0-9_]{3,}", t))
    han = re.sub(r"[^\u4e00-\u9fff]", "", t)
    out |= {han[i:i + 2] for i in range(max(0, len(han) - 1))}
    return out


class LessonStore:
    def __init__(self, root: pathlib.Path) -> None:
        self.root = pathlib.Path(root)
        self.path = self.root / "lessons.json"
        self._lock = threading.Lock()

    # ---------- 读写 ----------

    def all(self) -> list[dict[str, Any]]:
        try:
            if self.path.exists():
                data = json.loads(self.path.read_text("utf-8"))
                if isinstance(data, list):
                    return [x for x in data if isinstance(x, dict)]
        except Exception:                                   # noqa: BLE001
            pass                                            # 坏了当空 ✓（绝不能让启动挂掉 ✗）
        return []

    def _save(self, items: list[dict[str, Any]]) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(items[-MAX_KEEP:], ensure_ascii=False, indent=2),
                                 encoding="utf-8")
        except Exception:                                   # noqa: BLE001
            pass

    # ---------- 记一条 ----------

    def record(self, task_text: str, reason: str, *, who: str = "") -> bool:
        """记下"这类活栽在哪" ✓ 返回是否记了新的一条（重复的只加计数 ✓）。

        `reason` 会被压成**一句话** ✓ —— 验收意见常常几百字 ✓，
        原样存下来下次注入就成了噪音 ✗（注入的是"教训" ✓ 不是"案卷" ✓）。
        """
        one = self._squeeze(reason)
        if not one:
            return False
        key = one[:60]
        with self._lock:
            items = self.all()
            for it in items:
                if str(it.get("key") or "") == key:
                    it["hits"] = int(it.get("hits") or 0) + 1
                    it["at"] = time.strftime("%Y-%m-%d %H:%M")
                    self._save(items)
                    return False
            items.append({"key": key, "what": one, "task": str(task_text or "")[:200],
                          "who": str(who or "")[:40], "hits": 1,
                          "at": time.strftime("%Y-%m-%d %H:%M")})
            self._save(items)
            return True

    @staticmethod
    def _squeeze(reason: str) -> str:
        """把一段验收意见压成**一句可照做的教训** ✓。

        ★ 分三档挑（本班实测过：只按"含关键词"挑会挑到"这一项整体不通过"这种**没信息量**的句子 ✗）：
          ① 带「要改」的 ✓（那才是**可照做**的一句 ✓）
          ② 带「补/缺/没给/未给」的 ✓
          ③ 都不占就取最长的 ✓
        """
        text = " ".join(str(reason or "").split())
        if not text:
            return ""
        parts = [p.strip(" ·-—*：:") for p in re.split(r"[。；;!?！？\n]", text)]
        parts = [p for p in parts if 4 <= len(p) <= 200]
        if not parts:
            return text[:120]
        for marks in (("要改",), ("补", "缺", "没给", "未给", "没有"), ()):
            for p in parts:
                if marks and any(k in p for k in marks):
                    return p[:120]
            if not marks:
                break
        return max(parts, key=len)[:120]

    # ---------- 取相关的 ----------

    def relevant(self, task_text: str, k: int = MAX_INJECT) -> list[dict[str, Any]]:
        """挑跟这次任务最像的几条 ✓（按关键词重叠 + 命中次数排序 ✓ 无重叠就不注入 ✓）。"""
        want = _tokens(task_text)
        if not want:
            return []
        scored: list[tuple[float, dict[str, Any]]] = []
        for it in self.all():
            have = _tokens(str(it.get("task") or "") + " " + str(it.get("what") or ""))
            if not have:
                continue
            overlap = len(want & have)
            if overlap < 3:                     # 太不像就别硬塞 ✗（"像"的门槛 ✓）
                continue
            scored.append((overlap + min(int(it.get("hits") or 1), 5) * 0.5, it))
        scored.sort(key=lambda x: -x[0])
        out: list[dict[str, Any]] = []
        used = 0
        for _, it in scored[:k]:
            add = len(str(it.get("what") or "")) + 12
            if used + add > MAX_CHARS:
                break
            out.append(it)
            used += add
        return out

    def render(self, task_text: str) -> str:
        """给工作单用的一段文字（没相关的就返回空串 ✓ 绝不占位 ✗）。"""
        got = self.relevant(task_text)
        if not got:
            return ""
        lines = ["【过去这类活栽过的地方（照做别再栽 ✓）】"]
        for it in got:
            hits = int(it.get("hits") or 1)
            mark = f"（栽过 {hits} 次）" if hits > 1 else ""
            lines.append(f"· {it.get('what')}{mark}")
        return chr(10).join(lines)

    def stats(self) -> dict[str, Any]:
        items = self.all()
        return {"count": len(items), "top": sorted(items, key=lambda x: -int(x.get("hits") or 0))[:5]}
