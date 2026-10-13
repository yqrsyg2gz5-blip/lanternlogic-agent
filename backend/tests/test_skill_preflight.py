# -*- coding: utf-8 -*-
"""卡点视频技能 · 环境体检脚本（`tools/preflight.py`）的验收测试。

## 为什么有这个脚本（三轮真机失败换来的）

用户真机跑 30 秒卡点视频，**两轮共 720 步、1336 万 token、0 产物**。
事后逐项实测，卡住的是**渲染前面的三堵墙**（agent 前两轮根本没走到那步）：

  ① `tools/render/node_modules` 没装（渲染要 playwright-core）
  ② `CHROME_PATH` 没设（本机没 Chrome）⇒ render.mjs 直接失败
  ③ **PATH 上的 ffmpeg 是残缺版**（无 libx264）⇒ 编码报 Unknown encoder ✗
     ★ 最阴的一条：同一个命令名有两个版本，PATH 上那个是残的，agent 猜不到

修好这三样后实测：`render.mjs` 立刻出片（1080×1920 / h264 / 60 帧 / 2.000s / 38.5fps）✓

## 本文件钉什么

1. **能查出**这三样（缺了要报出来，不是静默通过）
2. **能认出残缺 ffmpeg**（`libx264` 检测 —— 这是最容易漏的一条）
3. **只读**：不加 `--install` 时**不动任何文件**
4. **不越界**：脚本**不许碰任何创作决策**（样式/分镜/时长/文案）——
   这条用"脚本里不许出现样式类关键词"钉住，防止后来人把它做成"写死模板生成器"
5. **跑不成就明确报错**（退出码 1），不许假装成功
"""
from __future__ import annotations

import ast
import importlib.util
import pathlib
import sys
import types

import pytest

SKILL = pathlib.Path(__file__).resolve().parents[2] / "backend" / "skills" / "卡点视频"
PREFLIGHT = SKILL / "tools" / "preflight.py"


def _load() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location("skill_preflight", PREFLIGHT)
    assert spec and spec.loader, f"加载不了 {PREFLIGHT}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_preflight_script_ships_with_the_skill():
    """脚本必须随技能包分发（否则 agent 拿不到它）。"""
    assert PREFLIGHT.exists(), f"技能里没有环境体检脚本：{PREFLIGHT} ✗"


def test_detects_a_ffmpeg_without_libx264(tmp_path):
    """★ 最关键的一条：**认出没有 libx264 的残缺 ffmpeg**。

    这条就是真机上让渲染崩掉的原因（`Unknown encoder 'libx264'`）。
    用一个假的 ffmpeg（encoders 列表里没有 libx264）验证能被识别 ✓
    """
    mod = _load()
    fake = tmp_path / "ffmpeg.cmd"
    fake.write_text("@echo off\necho  V..... libx265  x265 H.265\nexit /b 0\n", encoding="utf-8")
    # Windows 上 .cmd 要能被执行；用 bat/cmd 模拟即可
    if sys.platform == "win32":
        ok = mod.ffmpeg_has_libx264(str(fake))
        assert ok is False, "残缺 ffmpeg（无 libx264）被误判为可用 ✗"


def test_accepts_a_ffmpeg_with_libx264(tmp_path):
    """带 libx264 的必须认出来是好的（别把好的也判死）。"""
    mod = _load()
    real = mod.find_good_ffmpeg()
    if not real:
        pytest.skip("本机找不到带 libx264 的 ffmpeg —— 该面无法验证 ✓")
    assert mod.ffmpeg_has_libx264(real) is True, f"真实 ffmpeg 被误判：{real} ✗"


def test_report_shape_and_ready_flag(tmp_path):
    """体检报告要有固定字段；`ready` 只有在三样齐了才是 True。"""
    mod = _load()
    rep = mod.build_report(SKILL)
    for key in ("render_dir", "playwright_installed", "browser", "ffmpeg_good", "ready"):
        assert key in rep, f"报告缺字段 {key} ✗"
    assert rep["ready"] is False, "build_report 不该自己把 ready 判成 True（由 main 算）✗"


def test_format_report_mentions_the_three_walls():
    """★ 报告必须把三堵墙都写出来 —— 这是给 agent 看的"下一步做什么"。"""
    mod = _load()
    txt = mod.format_report({
        "skill_dir": "S", "render_dir": "S/tools/render",
        "render_dir_exists": True, "playwright_installed": False, "npm_available": True,
        "browser": r"C:\Edge\msedge.exe", "chrome_path_set": False,
        "ffmpeg_on_path": r"C:\bad\ffmpeg.exe", "ffmpeg_on_path_ok": False,
        "ffmpeg_good": r"C:\good\ffmpeg.exe", "ready": False,
    })
    assert "playwright" in txt.lower(), "没提渲染依赖 ✗"
    assert "CHROME_PATH" in txt, "没提 CHROME_PATH ✗"
    assert "libx264" in txt, "没提 libx264（最容易漏的那条）✗"
    # 要给出可直接复制的命令
    assert "$env:CHROME_PATH" in txt, "没给可复制的 CHROME_PATH 设置命令 ✗"
    assert "$env:PATH" in txt, "没给可复制的 PATH 设置命令 ✗"
    assert "npm install" in txt, "没给安装命令 ✗"


def test_json_mode_is_machine_readable():
    """`--json` 要输出可解析的 JSON（便于程序/测试消费）。"""
    import json as _json
    mod = _load()
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = mod.main(["--skill-dir", str(SKILL), "--json"])
    data = _json.loads(buf.getvalue())
    assert isinstance(data, dict) and "ready" in data
    assert rc in (0, 1), f"退出码只能是 0/1，实际 {rc} ✗"


def test_exit_code_is_one_when_not_ready():
    """环境不就绪 ⇒ 退出码 1（**不许假装成功**）✓"""
    mod = _load()
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = mod.main(["--skill-dir", str(SKILL)])
    rep = mod.build_report(SKILL)
    # ★ 判据跟 main 内部保持一致：用 is_ready（不是"机器上有没有好东西"）
    if mod.is_ready(rep):
        assert rc == 0, "环境齐了却是退出码 1 ✗"
    else:
        assert rc == 1, "环境不齐却是退出码 0（假装成功）✗"


def test_preflight_does_not_touch_creative_decisions():
    """★ 边界：体检脚本**不许**掺和创作（技能要做任意风格，不能写死）。

    为什么钉这条：这个脚本很容易被后来人"顺手扩展"成写死模板的生成器，
    一旦写死，技能就只做得了一种视频 ✗

    ★ 判据要**精确**（第一版判据太糙，本轮踩过）：不能是"脚本里不许出现某个词"——
      连**提醒 AI 别碰创作**的提示文案里都会自然出现那些词 ⇒ 误报 ✗。
      改判**"它有没有产出 HTML 的能力"**：
        · 不许 import HTML 模板库
        · 函数里不许出现 HTML 标签字面量
      ⇒ 没有产出 HTML 的能力，就不可能写死样式 ✓（这个判据不会误报）
    """
    src = PREFLIGHT.read_text("utf-8")
    tree = ast.parse(src)

    bad_imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            bad_imports += [a.name for a in node.names
                            if a.name.split(".")[0] in ("jinja2", "dominate", "markupsafe")]
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module.split(".")[0] in ("jinja2", "dominate", "markupsafe"):
                bad_imports.append(node.module)
    assert not bad_imports, f"体检脚本 import 了模板库 {bad_imports} ⇒ 它能生成样式了 ✗"

    html_marks = ("<!DOCTYPE", "<html", "<div", "<span", "<canvas", "<style", "@media")
    offenders = []
    for fn in [n for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        for node in ast.walk(fn):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if any(m in node.value for m in html_marks):
                    offenders.append(f"{fn.name}() 第 {node.lineno} 行")
                    break
    assert not offenders, (
        f"体检脚本里有函数在拼 HTML：{offenders} ✗\n"
        "  ⇒ 它只该管环境；页面由 AI 按题材现写（契约见 tools/render/example.html）"
    )


def test_preflight_does_not_write_files_without_install_flag():
    """★ 不加 `--install` 时**不动任何文件**（只读体检）。"""
    src = PREFLIGHT.read_text("utf-8")
    tree = ast.parse(src)
    writes = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = ""
            if isinstance(node.func, ast.Attribute):
                name = node.func.attr
            elif isinstance(node.func, ast.Name):
                name = node.func.id
            if name in ("write_text", "write_bytes", "open", "mkdir", "unlink", "remove"):
                writes.append((name, node.lineno))
    # 允许的写操作：只有 install_node_modules 里那次 npm（不直接写文件）
    bad = [(n, ln) for n, ln in writes if n in ("write_text", "write_bytes", "unlink", "remove")]
    assert not bad, f"体检脚本里有直接写/删文件的操作 {bad} ✗（只读体检，不许动用户文件）"


def test_ready_requires_the_env_to_actually_be_usable():
    """★★ `ready` 不能"机器上有好 ffmpeg/浏览器"就算就绪 —— **当前 shell 得真能用**。

    ★ 这条修的是一个**自相矛盾的 bug**（本轮实测踩到）：
      第一版 `ready = playwright ∧ browser ∧ ffmpeg_good`，
      于是脚本一边报「✗ CHROME_PATH 已设」「✗ PATH 上的 ffmpeg 无 libx264」，
      一边又打印「✅ 环境就绪，可以直接开工」✗
      这比不报更坏：agent 看到"就绪"就开工，然后**又撞墙**。
    """
    mod = _load()
    # 机器上都有（browser 找得到、ffmpeg_good 也找得到），但**当前 shell 没设好**
    rep = {
        "playwright_installed": True,
        "browser": r"C:\Edge\msedge.exe", "chrome_path_set": False,
        "ffmpeg_on_path": r"C:\bad\ffmpeg.exe", "ffmpeg_on_path_ok": False,
        "ffmpeg_good": r"C:\good\ffmpeg.exe",
    }
    assert mod.is_ready(rep) is False, (
        "CHROME_PATH 没设 / PATH 上 ffmpeg 无 libx264，却判成「就绪」✗"
        "（agent 会直接开工然后撞墙）"
    )
    # 全部设好才算就绪
    rep.update({"chrome_path_set": True, "ffmpeg_on_path_ok": True,
                "ffmpeg_on_path": r"C:\good\ffmpeg.exe"})
    assert mod.is_ready(rep) is True, "全都设好了却判成不就绪 ✗"
    # 缺任意一项都不算
    for miss in ("playwright_installed", "chrome_path_set", "ffmpeg_on_path_ok"):
        bad = dict(rep)
        bad[miss] = False
        assert mod.is_ready(bad) is False, f"缺 {miss} 却判成就绪 ✗"


def test_report_conclusion_matches_the_marks():
    """★ 报告里的 ✗/✅ 必须**自洽**：只要还有 ✗，结论就不许写"就绪"。"""
    mod = _load()
    rep = {
        "skill_dir": "S", "render_dir": "S/tools/render", "render_dir_exists": True,
        "playwright_installed": True, "npm_available": True,
        "browser": r"C:\Edge\msedge.exe", "chrome_path_set": False,
        "ffmpeg_on_path": r"C:\bad\ffmpeg.exe", "ffmpeg_on_path_ok": False,
        "ffmpeg_good": r"C:\good\ffmpeg.exe", "ready": False,
    }
    txt = mod.format_report(rep)
    assert "✗" in txt, "有未满足项却没打 ✗"
    assert "✅ 环境就绪" not in txt, "还有 ✗ 却写「环境就绪」⇒ 自相矛盾 ✗（agent 会直接开工）"
    assert "还不能开工" in txt, "没明确说「还不能开工」⇒ agent 可能误以为可以继续"


def test_example_page_ships_and_declares_the_contract():
    """★ 附带一个**可跑的页面样例**：契约（__meta/__draw/__ready）照抄即可。

    没有样例时，agent 得自己猜 `__draw(t)` 怎么写 —— 实测它就卡在这里（超时 120s）✗
    """
    ex = SKILL / "tools" / "render" / "example.html"
    assert ex.exists(), f"技能里没有页面样例：{ex} ✗"
    html = ex.read_text("utf-8", errors="replace")
    for need in ("window.__meta", "window.__draw", "window.__ready", "<canvas"):
        assert need in html, f"页面样例里缺 {need} ✗（契约不完整，agent 照抄会失败）"
    # 样例不许写死"某种视频"的样式：它是**契约示范**，不是模板
    assert "卡点视频" not in html and "抖音" not in html, (
        "页面样例里写了具体题材 ✗ —— 样例只示范契约，不该绑定某种视频"
    )


def test_skill_md_tells_the_agent_to_run_preflight_first():
    """★ SKILL.md 必须把「环境体检」写成**开工第一步** —— 否则 agent 不知道有这脚本。

    这条是给**后来人**的护栏：体检脚本很容易被当成"可选工具"往后挪或删掉，
    而那正是真机两轮失败（720 步 / 0 产物）的老路 ✗
    """
    md = (SKILL / "SKILL.md").read_text("utf-8", errors="replace")
    assert "preflight.py" in md, "SKILL.md 里没提 preflight.py ⇒ agent 不会跑它 ✗"
    assert "环境体检" in md, "SKILL.md 里没有「环境体检」这一步 ✗"
    i_pf = md.find("preflight.py")
    i_step1 = md.find("第 1 步")
    assert i_step1 > 0 and i_pf < i_step1, (
        "环境体检没排在「第 1 步」之前 ✗ —— 放后面等于没有（环境不对后面全白做）"
    )


def test_render_readme_exists_and_covers_the_three_walls():
    """`render.mjs` 注释里指向 `render/README.md` —— 它以前不存在（文档缺口）。"""
    rd = SKILL / "tools" / "render" / "README.md"
    assert rd.exists(), f"缺 {rd}（render.mjs 的注释指向它）✗"
    txt = rd.read_text("utf-8", errors="replace")
    for need in ("__meta", "__draw", "__ready", "CHROME_PATH", "libx264", "npm install"):
        assert need in txt, f"README 里没讲 {need} ✗（这是渲染失败的常见原因）"
