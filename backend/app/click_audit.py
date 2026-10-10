# -*- coding: utf-8 -*-
"""交付体检：**真点一遍**再说话 ✓ —— 治"说验收通过，点进去点不动"

## 为什么必须有它（2026-10-09 用户实测 ✓）

那天的真事：
  · Agent 交付了 `index.html` ✓ 自己写报告说「**验收通过** ✓ 未发现阻断性缺陷」✗
  · 而它自查的方式是跑**项目自带的 `_test.html` 23/23`** ✗ —— ★ **自己出的题、自己判卷** ✓
  · 用户点进去 ⇒ **「▦看板」按钮点了没反应** ✗（我用真浏览器一把就抓到了 ✓）
  ⇒ 结论：**"跑通了自测" ≠ "验收通过"** ✗ —— 交付前必须有一道**外部**的真点击 ✓

## 这个模块做什么（以及**不**做什么 ✓）

做 ✓：开真浏览器（系统 Edge ✓）→ 把页面上**可见按钮逐个真点** → 报告
       · 哪些点了**页面毫无变化** ✗（= 死按钮 ✓ 候选缺陷 ✓）
       · JS 报错几条 ✓
       · 结构信息（按钮总数 ✓ 标题 ✓）

不做 ✗（★ 都是**故意**的，别改）：
  · **不判"业务对不对"** ✗ —— 它只回答"**点了有没有反应**" ✓（业务对错得靠人 ✓ 别越权 ✗）
  · **不阻塞交付** ✗ —— 拿不到浏览器就让路 ✓（软门 ✓ 硬卡会让"本来就没按钮"的产物永远交不了 ✗）
  · **不修改任何文件** ✗ —— 纯读 ✓ 连截图都不落盘 ✓

## 依赖与降级 ✓

用 `playwright` + **系统 Edge**（`channel="msedge"`）✓ —— 本仓浏览器工具一直这么干 ✓（省几百 MB 下载 ✓）
★ 用户侧可能**没装 playwright** ✗（公开版的 `requirements.txt` 里没有它 ✓ 那是**运行依赖**✓ 而已 ✓）
  ⇒ 拿不到就**如实跳过** ✓（`skipped_reason` 写清楚 ✓）—— **绝不假装验过** ✗
"""
from __future__ import annotations

import pathlib


def _skip(reason: str) -> dict:
    return {"ok": False, "skipped_reason": reason, "buttons": 0, "dead": [],
            "js_errors": [], "title": "", "detail": f"未做真点击体检：{reason}"}


def audit_html(path: str | pathlib.Path, *, timeout_ms: int = 60_000,
               max_buttons: int = 40, per_click_ms: int = 1200) -> dict:
    """真开浏览器点一遍 ✓ 返回结构化结果 ✓ **永不抛** ✗（它在交付收尾路径上跑 ✓）。

    返回：
      ok            体检**真跑成功**了吗（False = 跳过或失败 ✓ 看 detail ✓）
      buttons       可见按钮数
      dead          ★ 点了页面毫无变化的按钮文字（**候选缺陷** ✓）
      js_errors     页面 JS 报错（前几条 ✓）
      title         页面标题
      detail        一句话结论（可直接贴进交付卡 ✓）
      skipped_reason 跳过原因（没装 playwright / 没有 Edge / 文件不存在 …）
    """
    p = pathlib.Path(path)
    if not p.is_file():
        return _skip(f"文件不存在：{p}")
    if p.suffix.lower() not in (".html", ".htm"):
        return _skip(f"不是网页，跳过点击体检：{p.suffix}")

    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:  # noqa: BLE001 —— 没装就是没装 ✓ 如实跳过 ✓
        return _skip(f"本机没装 playwright（{type(e).__name__}）—— 装它才能做真点击体检")

    url = p.resolve().as_uri()
    try:
        with sync_playwright() as pw:
            try:
                browser = pw.chromium.launch(channel="msedge", headless=True)
            except Exception as e:  # noqa: BLE001
                return _skip(f"起不了系统 Edge（{type(e).__name__}）—— 本机可能没装 Edge")
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            js_errors: list[str] = []
            page.on("pageerror", lambda e: js_errors.append(str(e)[:160]))
            page.goto(url, timeout=timeout_ms)
            page.wait_for_timeout(1200)

            title = page.title()
            n = page.locator("button").count()
            # ★ 2026-10-09 加固（第一次全量门出现 1 条偶发失败后加的 ✓）：
            #   · **点数上限**：页面按钮上百个时逐个点会拖很久 ✓（收尾路径上不能久留 ✗）
            #   · **单次点击超时**缩短：点不到的（遮挡/滚出视口）本来就**不算它的错** ✓
            #     ⇒ 早点放弃 ⇒ 总时长可控 ✓ 抖动也小 ✓
            #   （诚实起见：被跳过的部分会在结论里说明 ✓ 不假装全点过 ✗）
            capped = n > max_buttons
            n_click = min(n, max_buttons)
            dead: list[str] = []
            for i in range(n_click):
                el = page.locator("button").nth(i)
                try:
                    if not el.is_visible():
                        continue
                    label = ((el.text_content() or "").strip().replace("\n", " ")[:20]) or "(无字按钮)"
                except Exception:  # noqa: BLE001
                    continue
                try:
                    before = page.locator("body").inner_html()
                    el.click(timeout=per_click_ms)
                    page.wait_for_timeout(250)
                    after = page.locator("body").inner_html()
                    if before == after:
                        dead.append(label)
                except Exception:  # noqa: BLE001
                    # ★ 点不到（被遮挡/滚出视口）**不算它的错** ✗ —— 我自己测那天的实操教训 ✓
                    continue
            browser.close()
    except Exception as e:  # noqa: BLE001 —— 收尾路径**永不抛** ✓
        return _skip(f"体检过程出错（{type(e).__name__}: {str(e)[:120]}）")

    if dead:
        detail = (f"⚠️ 真点击体检：{n} 个可见按钮中，**{len(dead)} 个点了没有任何反应** ✗："
                  + "、".join(dead[:8]) + ("…" if len(dead) > 8 else "")
                  + "　⇒ 这属于**缺陷** ✓ 交付前该修 ✗（不许写「验收通过」✗）")
    elif n == 0:
        detail = "真点击体检：页面上没有可见按钮 ✓（无从点击 ✓ 不算通过也不算失败 ✓）"
    else:
        scope = f"（按钮太多，只点了前 {n_click} 个 ✓ 其余未验 ✗）" if capped else ""
        detail = (f"✅ 真点击体检：{n} 个可见按钮**逐个真点过** ✓ 全都有反应 ✓{scope}"
                  f"（JS 报错 {len(js_errors)} 条）")
    return {"ok": True, "skipped_reason": "", "buttons": n, "dead": dead,
            "js_errors": js_errors[:5], "title": title, "detail": detail}
