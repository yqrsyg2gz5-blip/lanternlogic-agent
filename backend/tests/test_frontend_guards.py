"""前端守卫静态锚点（K6b）——sources 链接协议白名单必须真的接在渲染路径上。

前端无单测 runner，本组做接线锚（规矩⑭）：函数本体由 tsc 编译产物 + node 断言
实测（见整改报告 K6b 节），这里钉"组件必须真的调用守卫"——回滚 EventItem 到
裸 href={s.url} 时本组必须红。
"""
from __future__ import annotations

import re
from pathlib import Path

FRONTEND_SRC = Path(__file__).resolve().parents[2] / "frontend" / "src"


def test_event_item_sources_use_protocol_guard():
    tsx = (FRONTEND_SRC / "components" / "EventItem.tsx").read_text("utf-8")
    assert "safeHref" in tsx, "EventItem 未引入协议白名单守卫"
    assert re.search(r"href=\{s\.url\}", tsx) is None, "sources 仍存在裸 href={s.url}（伪协议可注入）"
    assert "safeHref(s.url)" in tsx, "sources 渲染路径未经过 safeHref"


def test_safe_url_module_whitelists_protocols():
    ts = (FRONTEND_SRC / "lib" / "safeUrl.ts").read_text("utf-8")
    assert "https?" in ts and "mailto" in ts
    assert "null" in ts, "非白名单协议必须降级为 null（纯文本）"


def test_no_other_bare_external_href():
    """同类变体扫描锚点：全 src 的 href={var} 直绑必须全部落在已知安全清单内
    （内部 API_BASE/rawUrl 构造 or 已过 safeHref）；出现新裸绑即红，迫使人工登记。"""
    allowed_patterns = (
        "href={h}",                       # K6b：safeHref 过滤后的
        "href={url}",                     # EventItem Media：API_BASE 内部构造
        "href={rawUrl(",                  # ArtifactPanel：内部构造
        "href={`${API_BASE}",             # TeamView：内部构造
        # ★ 2026-10-07 补：**原地过白名单**这种写法本来也该算数 ✓ ——
        #   本测试的 docstring 第 30 行原话就是「内部构造 **or 已过 safeHref**」✓
        #   而实现里**只有 `href={h}`**（要求先把结果存进一个叫 h 的变量 ✗）⇒
        #   写成 `href={safeHref(x)!}` 会被判违规 ✗ —— 那是**实现没跟上规矩** ✓
        #   （升级提示那个下载链接就是这么被拦下的 ✓ 拦得对：那个地址来自远端 JSON ✓
        #     但正确的修法是"让它过 safeHref"，而不是"改成先赋值给 h 绕过去" ✗）
        #   这一条**不放松任何东西**：以它开头的必然过了白名单 ✓
        "href={safeHref(",
    )
    offenders = []
    for f in FRONTEND_SRC.rglob("*.tsx"):
        for i, line in enumerate(f.read_text("utf-8").splitlines(), 1):
            m = re.search(r"href=\{([^}'\"`]+)\}", line)
            if not m:
                continue
            frag = m.group(0)
            if not any(frag.startswith(p.rstrip("{") + "{") or p in frag for p in allowed_patterns):
                offenders.append(f"{f.name}:{i} {line.strip()[:80]}")
    assert not offenders, "发现未登记的裸 href 直绑：\n" + "\n".join(offenders)
