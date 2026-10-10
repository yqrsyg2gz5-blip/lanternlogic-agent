"""技能注册表 —— 三级渐进加载（上下文管理的精髓）。

Level 1（常驻，~100 token/技能）：名称 + 描述进系统提示，模型据此判断是否需要；
Level 2（按需）：模型调 load_skill(name) → 返回 SKILL.md 全文（<5k token）；
Level 3（按需）：load_skill(name, resource=文件名) → 返回技能目录下的资源文件。

技能 = skills/<目录>/SKILL.md（frontmatter: name/description）+ 可选资源文件。

安全（第 41 班）：SKILL.md 是直接进模型上下文的"语义控制面"——供应链攻击面。
加载前统一**清洗不可见 Unicode**（零宽字符/双向控制符/标签字符可把人眼看到的
无害文本与模型读到的恶意指令做成两套内容）。⚠️ 清洗 ≠ 免疫：明文恶意指令照样
是注入——开放第三方技能前必须再加签名/审核机制（见 contracts/03-config.md）。
"""
from __future__ import annotations

import unicodedata
from pathlib import Path
from typing import Any

# 允许保留的"可见格式"控制符：换行、制表；FE0E/FE0F 是 emoji 表意选择符（🏔️🖥️ 等
# 的合法组成部分，单个选择符无法编码隐藏文本）——其余不可见/双向/标签字符一律剥离
_ALLOWED_CONTROLS = {"\n", "\t", "\uFE0E", "\uFE0F"}

# ★★ 2026-10-07（功能体检读代码读出来的缺口 ✗）：**技能正文没有大小上限**。
#   模块 docstring 一直写着"返回 SKILL.md 全文（<5k token ✓）"✗ —— 而代码**从不管大小** ✓
#   ⇒ 谁放一个几百 KB 的 SKILL.md（或一个超大资源文件 ✓）✓
#     模型一调 `load_skill` 就把它**整篇塞进上下文** ✗✓
#     ⇒ 轻则**一次烧掉几万 token 的钱** ✓ 重则直接把上下文顶爆、任务失败 ✗。
#   ⇒ 现在**截断**并**明确告诉模型**"后面还有内容被截了" ✓
#     （**不静默截断** ✗ —— 静默截断会让模型以为技能就这么点内容 ✓ 那是另一种骗人 ✓）。
MAX_SKILL_CHARS = 20000          # ≈5k token（中文 1 字≈1 token 量级，留足余量 ✓）


def sanitize_skill_text(text: str) -> tuple[str, int]:
    """剥离零宽/双向控制/标签/杂项控制字符。返回 (清洗后文本, 剥离数量)。

    剥离的类别（每类都是已知的隐藏指令载体）：
      · 零宽：U+200B–200F（零宽空格/连接符/方向标记）
      · 双向控制：U+202A–202E、U+2066–2069（可视觉重排文字，掩盖真实阅读顺序）
      · 词接符/不可见分隔：U+2060–2064
      · BOM：U+FEFF
      · 标签字符：U+E0000–E007F（经典的"隐藏文本"编码区）
      · 其余 C0/C1 控制字符（除 \\n \\t）
    """
    out: list[str] = []
    stripped = 0
    for ch in text:
        cp = ord(ch)
        if ch in _ALLOWED_CONTROLS:
            out.append(ch)
            continue
        cat = unicodedata.category(ch)
        if cat in ("Cc", "Cf") or 0xE0000 <= cp <= 0xE007F:
            stripped += 1
            continue
        out.append(ch)
    return "".join(out), stripped


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """简易 frontmatter：文件开头的 --- ... --- 段按 key: value 解析；返回 (meta, 正文)。"""
    meta: dict[str, str] = {}
    body = text
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            for line in parts[1].splitlines():
                if ":" in line:
                    k, _, v = line.partition(":")
                    meta[k.strip()] = v.strip()
            body = parts[2].lstrip("\n")
    return meta, body


class SkillRegistry:
    def __init__(self, skills_dir: Path) -> None:
        self.dir = Path(skills_dir)

    def list_skills(self) -> list[dict[str, Any]]:
        """Level 1：全部技能的元数据（常驻系统提示，每条 ~100 token）。"""
        out: list[dict[str, Any]] = []
        if not self.dir.exists():
            return out
        for d in sorted(self.dir.iterdir()):
            f = d / "SKILL.md"
            if d.is_dir() and f.exists():
                meta, _ = _parse_frontmatter(f.read_text("utf-8"))
                name, desc = meta.get("name", d.name), meta.get("description", "")
                name, n1 = sanitize_skill_text(name)
                desc, n2 = sanitize_skill_text(desc)
                if n1 + n2:
                    print(f"[技能安全] 「{d.name}」元数据含不可见字符，已剥离 {n1 + n2} 个", flush=True)
                out.append({"name": name, "description": desc})
        return out

    def _find(self, name: str) -> Path | None:
        if not self.dir.exists():
            return None
        for cand in sorted(self.dir.iterdir()):
            f = cand / "SKILL.md"
            if not cand.is_dir() or not f.exists():
                continue
            meta, _ = _parse_frontmatter(f.read_text("utf-8"))
            if name in (meta.get("name", ""), cand.name):
                return cand
        return None

    def load(self, name: str, resource: str | None = None) -> str:
        """Level 2/3：按名称加载 SKILL.md 全文，或按文件名加载技能附带资源。
        内容一律过不可见字符清洗（供应链防线，见模块 docstring）。"""
        d = self._find(name)
        if d is None:
            available = [s["name"] for s in self.list_skills()]
            raise FileNotFoundError(f"未找到技能：{name}（可用：{available}）")
        if resource:
            rf = (d / resource).resolve()
            if not rf.is_relative_to(d.resolve()):
                raise PermissionError(f"资源越界（限技能目录内）：{resource}")
            text = rf.read_text("utf-8", errors="replace")
        else:
            text = (d / "SKILL.md").read_text("utf-8", errors="replace")
        cleaned, n = sanitize_skill_text(text)
        if n:
            print(f"[技能安全] 「{d.name}」正文含不可见字符，已剥离 {n} 个（若非本机自建请立即检查来源！）", flush=True)
        # ★ 2026-10-07：**大小上限** ✓（见 MAX_SKILL_CHARS 的注释 ✓）
        #   超了就截断 ✓ 但**明确写出来**"被截了、想看全请分段读资源文件" ✗✓
        #   —— 静默截断 = 让模型以为技能就这么点内容 ✓ 那是另一种骗人 ✓。
        if len(cleaned) > MAX_SKILL_CHARS:
            kept = len(cleaned) - MAX_SKILL_CHARS
            print(f"[技能] 「{d.name}」正文 {len(cleaned)} 字 > 上限 {MAX_SKILL_CHARS}，已截断 {kept} 字", flush=True)
            cleaned = cleaned[:MAX_SKILL_CHARS] + (
                f"\n\n[已截断：本文件共 {len(cleaned)} 字，此处只给了前 {MAX_SKILL_CHARS} 字。"
                "需要后面部分请用 load_skill(name, resource=...) 分段读取该技能的资源文件。]"
            )
        return cleaned
