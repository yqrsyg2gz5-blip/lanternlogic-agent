# -*- coding: utf-8 -*-
"""★★ 发版包必须**开箱能渲染**（2026-10-14 发版验收抓到的两个真 bug）。

用户要求：「一次性给他做好审查测试，都弄好了，你再上传……你别老弄个半瞎子，
然后就上传，不好，然后别人下载完了，口碑也不好」

## Bug ①：`node_modules` 没打进发版包 ⇒ 用户一渲染就失败 ✗

```
渲染器 tools/render/render.mjs 依赖 playwright-core
而 node_modules/ 在 .gitignore 里 ⇒ `git ls-files` 看不到 ⇒ 导不进包 ✗
实测（v0.1.0 / v0.1.1 都中招）：
  ERR_MODULE_NOT_FOUND: Cannot find package 'playwright-core'
⇒ **核心功能（出视频）直接废掉**，报错还完全指不到"包打漏了" ✗
```

## Bug ②：渲染器写死找 Chrome ⇒ 没装 Chrome 的用户必失败 ✗

```
原来：const launchOpts = CHROME_PATH ? {...} : { channel: 'chrome' }
Windows 用户**多半只有 Edge**（系统自带）⇒ 起不来 ⇒ 报一句英文堆栈 ✗
```

## 本文件钉什么

1. `export_public.py` 里有强制带入清单，且**确实列出了渲染器的 node_modules**
2. 渲染器的 `package.json` 声明的依赖 == 强制带入的目录（声明了不带 = 必炸）
3. `render.mjs` **不许**写死 `channel: 'chrome'`；必须有 Edge 自动回退
4. 起不来时要给**人话**（说清试了哪个、装 Edge 就行、怎么指定 CHROME_PATH）
5. 起手模板 `starter.html` 必须在渲染器目录里（发版包要带）
"""
from __future__ import annotations

import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
RENDER = ROOT / "backend" / "skills" / "卡点视频" / "tools" / "render"


def test_export_script_force_includes_renderer_node_modules():
    """★ Bug ①：node_modules 被 .gitignore 排除 ⇒ 必须**强制带入**，否则发版包缺依赖 ✗"""
    src = (ROOT / "scripts" / "export_public.py").read_text("utf-8")
    assert "FORCE_INCLUDE_DIRS" in src, (
        "export_public.py 没有「强制带入」清单 ⇒ 被 .gitignore 排除的运行时依赖"
        "（渲染器的 playwright-core）永远进不了发版包 ✗"
    )
    assert "node_modules" in src, "强制带入清单里没提 node_modules ✗"
    assert "tools/render" in src.replace("\\", "/"), (
        "强制带入的不是渲染器目录 ⇒ 带错了 ✗"
    )


def test_every_declared_renderer_dependency_is_force_included():
    """★ 渲染器 `package.json` 声明的依赖，必须**每个**都在强制带入清单里。

    声明了却不带 ⇒ 用户解压后 `ERR_MODULE_NOT_FOUND`（实测 v0.1.0/v0.1.1 都这样）✗

    ★★ 2026-10-14 修（用户跑干净 checkout 时抓到）：
       这条测试原来还断言 `node_modules` **必须已存在** —— 那是**错的** ✗
       `node_modules` 在 `.gitignore` 里 ⇒ **任何干净 checkout（含从 GitHub/Gitee clone）
       都必然没有它** ⇒ 这条断言在别人机器上**必然红** ✗
       （真机复现：用户在 `D:\\a\\lanternlogic-agent\\...` 跑 pytest ⇒ 这一条 F）

       正确的口径是「**要么本来就有，要么装得回来**」：
         · 发版 zip：靠 `export_public.py` 的强制带入清单 → 一定有 ✓
         · 源码 clone：靠 `install.bat` 的 `[4c/5]` 步 npm install → 装得回来 ✓
       ⇒ 所以这里只断言「声明 ⟺ 强制带入清单 ⟺ install.bat 会装」三者一致 ✓
    """
    pkg = json.loads((RENDER / "package.json").read_text("utf-8"))
    deps = list((pkg.get("dependencies") or {}))
    assert deps, "渲染器没有声明依赖？那这条测试失去意义，检查 package.json ✗"

    src = (ROOT / "scripts" / "export_public.py").read_text("utf-8")
    for d in deps:
        assert d in src, (
            f"渲染器依赖 {d!r} 没在 export_public.py 的强制带入清单里 ⇒ "
            f"发版包会缺它 ⇒ 用户渲不了视频 ✗"
        )

    # ★ 源码路径：install.bat 必须会装（否则干净 checkout 永远跑不了渲染）✓
    inst = (ROOT / "install.bat").read_text("ascii", "replace")
    assert "npm install" in inst and "tools\\render" in inst, (
        "install.bat 不装渲染器依赖 ⇒ 干净 checkout（node_modules 被 .gitignore 排除）"
        "永远跑不了渲染 ✗"
    )

    # ★ 有就顺便验一下真的是它（没有不算失败 —— 干净 checkout 本来就没有）✓
    nm = RENDER / "node_modules" / deps[0]
    if nm.is_dir():
        assert (nm / "package.json").exists(), f"node_modules/{deps[0]} 装坏了 ✗"


def test_renderer_does_not_hardcode_chrome_channel():
    """★ Bug ②：不许**直接用** `channel: 'chrome'` —— Windows 用户多半只有 Edge ✗

    允许在"Edge/Chrome 可执行文件都没找到"时**最后兜底**用 channel，
    但**必须先把 Edge 的可执行文件路径试过** ✓
    """
    src = (RENDER / "render.mjs").read_text("utf-8", errors="replace")
    assert "msedge.exe" in src, (
        "render.mjs 里没有 Edge 的路径 ⇒ 找不到浏览器时不会回退到 Edge ⇒ "
        "没装 Chrome 的 Windows 用户必失败 ✗"
    )
    # Edge 必须排在 Chrome 前面（Windows 自带 Edge，命中率最高）
    i_edge = src.find("msedge.exe")
    i_chrome = src.find("Chrome/Application/chrome.exe")
    assert i_edge > 0 and i_chrome > 0, "浏览器候选清单不完整 ✗"
    assert i_edge < i_chrome, (
        "Edge 没排在 Chrome 前面 ⇒ 白装了 Edge 的用户反而更晚被找到（没必要）✗"
    )
    assert "existsSync" in src, "没有文件存在性判断 ⇒ 谈不上自动挑浏览器 ✗"
    assert "process.env.CHROME_PATH" in src, "没支持用户用 CHROME_PATH 指定浏览器 ✗"


def test_renderer_gives_a_human_error_when_browser_fails():
    """★ 起不来时要给**人话**（说清怎么办），不许只甩一句英文堆栈 ✗"""
    src = (RENDER / "render.mjs").read_text("utf-8", errors="replace")
    assert "起不了浏览器" in src, "没有中文的「起不了浏览器」提示 ✗"
    assert "CHROME_PATH" in src, "没告诉用户可以用 CHROME_PATH 指定浏览器 ✗"
    assert "Edge" in src, "没告诉用户装了 Edge 就够 ✗"


def test_starter_template_ships_with_the_renderer():
    """★ 起手模板必须在渲染器目录里（发版包要带它 —— 治"没样例只能读源码"）✓"""
    s = RENDER / "starter.html"
    assert s.exists(), f"起手模板不在：{s} ✗"
    t = s.read_text("utf-8", errors="replace")
    for m in ("window.__meta", "window.__draw", "window.__ready"):
        assert m in t, f"起手模板缺 {m} ✗"


def test_install_bat_installs_the_renderer_deps():
    """★★ 源码路径也要能装上渲染器依赖（2026-10-14 抓到的第三个同源 bug）。

    现场：`install.bat` 只装了 frontend 的依赖，**从来没装渲染器的 playwright-core** ✗
    ⇒ 别人 clone 仓库 → 跑 install.bat → 做卡点视频 ⇒
      `ERR_MODULE_NOT_FOUND: Cannot find package 'playwright-core'`
    ⇒ 跟 zip 包那个 bug 同一个病，只是发生在源码路径上 ✗
    （zip 包靠 `export_public.py` 的强制带入清单救，源码 clone 得靠这里装 ✓）
    """
    t = (ROOT / "install.bat").read_text("ascii", "replace")
    assert "playwright-core" in t, "install.bat 里没装渲染器依赖 ⇒ 源码用户做视频必炸 ✗"
    assert "tools\\render" in t or "tools/render" in t, "install.bat 没定位到渲染器目录 ✗"
    assert "npm install" in t, "install.bat 没跑 npm install ✗"
    assert "for /d" in t, (
        "install.bat 没用 `for /d` 拿技能目录名 ⇒ 要么写死中文（破坏纯 ASCII）、"
        "要么用通配符（Windows 的 for /d 不跨中文目录匹配 —— 本班实测失败 ✗）"
    )


def test_start_and_install_bat_stay_pure_ascii():
    """★ 两个 .bat 都必须纯 ASCII（本班实测：往里写中文会乱码/报错 ✗）✓"""
    for name in ("install.bat", "start.bat"):
        p = ROOT / name
        if not p.exists():
            continue
        raw = p.read_bytes()
        bad = [i for i, b in enumerate(raw) if b > 127]
        assert not bad, (
            f"{name} 里有 {len(bad)} 个非 ASCII 字节（位置 {bad[:5]}）⇒ GBK 控制台会乱码 ✗"
        )


def test_repo_has_a_release_preflight_that_really_renders():
    """★ 发版前必须有一道「真渲一小段」的体检 —— 本班就是靠它抓到上面两个 bug ✓

    用户原话：「一次性给他做好审查测试，都弄好了，你再上传……别老弄个半瞎子就上传」
    """
    rc = ROOT / "scripts" / "release_check.py"
    assert rc.exists(), (
        "没有 scripts/release_check.py ⇒ 发版前没人真跑过渲染 ⇒ "
        "又会出现「发出去用户用不了」✗"
    )
    t = rc.read_text("utf-8", errors="replace")
    assert "render.mjs" in t, "发版体检里没**真跑**渲染器（只看文件在不算）✗"
    assert "--to" in t or "starter" in t, "发版体检没渲一小段验证 ✗"
    assert "FORCE_INCLUDE_DIRS" in t or "node_modules" in t, (
        "发版体检没查「被 .gitignore 排除的运行时依赖会不会被打进包」✗"
    )
    assert "msedge" in t, "发版体检没查浏览器（新用户多半只有 Edge）✗"
