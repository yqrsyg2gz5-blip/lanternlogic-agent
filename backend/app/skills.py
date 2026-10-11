"""技能注册表 —— 三级渐进加载（上下文管理的精髓）。

★ 2026-10-11 改口径：Level 2 交付的是**第一页**，不是"全文截断" ✗→✓

Level 1（常驻，~100 token/技能）：名称 + 描述进系统提示，模型据此判断是否需要；
Level 2（按需）：模型调 load_skill(name) → 返回 SKILL.md 的**一页**（默认 4000 字）；
Level 3（按需）：load_skill(name, resource=文件名) → 返回技能目录下的资源文件。

**为什么要分页而不是"截断"**（真实事故，2026-10-11）：
  一份 2 万字左右的技能文档，原来这里是 `cleaned[:20000] + "（尾巴上挂一句'已截断'）"` ✓
  而**下游还有第二道截断**（`loop.py` 把工具观察砍到 4000 字 ✓）
  ⇒ 那句话**正好被砍掉** ✗ ⇒ 模型**不知道自己看到的不是全文** ✗✓
  ⇒ 它照着残缺的技能做事，反复返工，一个 18 秒视频的任务跑满 60 分钟也没交付 ✗。
  修法两条（缺一不可）：
    ① **提示放开头** ✓ —— 扛得住下游任何一次再截断 ✓
    ② **给可照抄的续读参数**（`offset=N` ✓）—— 模型才真能读全，而不是"被告知读不全" ✗

★ 上限不设成"不截断"✗：单条巨内容会把上下文顶爆（另一种把任务弄死 ✓）。
  默认一页 4000 字 ≈ 4k token（中文 1 字≈1 token 量级 ✓ 与 `loop._compact_history`
  的估算口径一致 ✓），相对 `max_context_tokens` 默认 65536 只占约 6% ✓ 安全 ✓。

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
#   ⇒ 现在**分页**并**明确告诉模型**"后面还有内容" ✓
#     （**不静默截断** ✗ —— 静默截断会让模型以为技能就这么点内容 ✓ 那是另一种骗人 ✓）。
#
# ★★ 2026-10-11（平台闸修复 ①）：**上限从"截断"改成"分页"** ✓
#   20000 字那一版有两个洞：
#     ① 提示挂在**尾巴**上 ⇒ 被 `loop.py` 的第二道 4000 字截断吃掉 ⇒ 模型不知情 ✗✓
#     ② 想读后面**没有手段**（提示只说"用 resource= 读资源文件"✗ —— 可技能正文
#        根本不是资源文件 ✓ 那句话等于没说 ✓）
#   ⇒ 现在：一页 4000 字 ✓ 提示在**开头** ✓ 带**真数字**与**照抄即用的 offset** ✓
MAX_SKILL_CHARS = 20000          # 一个技能/资源文件**最多给多少**（兜底硬上限，防 context 顶爆）
DEFAULT_SKILL_PAGE = 4000        # 一次 `load_skill` 默认给多少字（≈4k token；可用 offset 续读）


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

    def load(self, name: str, resource: str | None = None, offset: Any = 0) -> str:
        """Level 2/3：按名称加载 SKILL.md（或资源文件）的**一页**。
        内容一律过不可见字符清洗（供应链防线，见模块 docstring）。

        ★ 2026-10-11（平台闸修复 ①）：口径从"截断"改成"**分页 + 开头提示**" ✓
          · `offset` 从第几个字开始给（模型照抄提示里的数字即可接着读 ✓）
          · 超长时**第一行**就是"你看到的是第 X–Y 字 / 共 N 字 / 用 offset=Y 续读" ✓
            —— 放开头是因为**下游还会再截一次**（`loop.py` 的观察上限 ✓）
               挂在尾巴上的提示在那一步必被砍掉 ✗（这正是本次事故的形态 ✓）
        """
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
        # 资源文件与正文同一条通道（否则"分段读资源"自己就能把上下文顶爆 ✗）
        what = f"技能「{d.name}」的 {resource}" if resource else f"技能「{d.name}」正文"
        return self._paginate(cleaned, what, offset, resource=bool(resource))

    @staticmethod
    def _paginate(text: str, what: str, offset: Any, *, resource: bool = False) -> str:
        """把（已经清洗过的）正文切一页出来 —— **提示在开头、带真数字、带续读参数** ✓。

        抽成静态方法是为了能单独钉住它（见 tests/test_skills_registry.py ⑤）——
        这次的病根就是"分页逻辑藏在 load 里、没人单独测过" ✓。
        """
        try:
            start = int(offset or 0)
        except (TypeError, ValueError):
            start = 0                      # 模型给怪值 ⇒ 当从头读 ✓（绝不抛异常弄死任务 ✗）
        # 负偏移截到开头；同时兜住"上限"这道硬闸 ✓
        start = max(0, min(start, min(len(text), MAX_SKILL_CHARS)))
        if start >= len(text):
            if not text:
                return f"[{what}是空的（0 字）]"
            return (f"[{what}共 {len(text)} 字，offset={start} 已超出正文范围 —— "
                    "这已经是结尾了，没有更多内容。要看开头请只传 name（不带 offset）。]")
        page = text[start:start + DEFAULT_SKILL_PAGE]
        end = start + len(page)
        remaining = len(text) - end
        if remaining <= 0 and start == 0:
            return page                    # 没超限就**一个字都不动** ✓（不白烧 token ✓）
        # 续读时要不要再带 resource：资源文件要带 ✓（正文不带 ✓）
        res_arg = ", resource=..." if resource else ""
        lines = [f"[{what}共 {len(text)} 字；这里是第 {start + 1}–{end} 字。]"]
        if remaining > 0:
            lines.append(f"[已截断：后面还有 {remaining} 字没给。读完这页请接着调 "
                         f"load_skill(name=..., offset={end}{res_arg})"
                         " 读下一页——**在读完之前，不要以为你已经看到了全部。**]")
        else:
            lines.append("[已到结尾：这一页就是最后一段，没有更多内容了。]")
        print(f"[技能] {what} {len(text)} 字，本页 {start + 1}–{end}（限 {DEFAULT_SKILL_PAGE} 字/页，"
              f"还剩 {max(0, remaining)} 字）", flush=True)
        return "\n".join(lines) + "\n" + page
