# -*- coding: utf-8 -*-
"""版本号的**唯一来源** ✓ —— 2026-10-07 加（第 3 项：版本检查 + 升级提示）。

## 为什么要有这个文件（现状是**三处各说各的** ✗）

加之前查了一遍：
  · `frontend/package.json` 写着 `0.1.0` ✓
  · 关于页**硬编码**一行「LanternLogic Agent v0.1.0」✗
  · **后端压根没有版本号** ✗（没有任何接口能回答"我是哪个版本"✓）
⇒ 三处各说各的 ✓ 改一处另两处不会跟着动 ✗
  —— 正是本项目栽过五次的"**多处口径打架**"✓（这次是版本号 ✓）

## 规矩（与"单价表""能力表"同一条）

**一处声明，处处读它** ✓：
  · 后端：`__version__` 在这里 ✓ 由 `GET /api/v1/version` 发出去 ✓
  · 前端：**不再硬编码** ✗ 显示后端报的那个 ✓
  · 打包/发版：也读这里 ✓（`package.json` 那份由守卫测试钉住"必须一致"✓ 不是靠自觉 ✓）
"""
from __future__ import annotations

#: 产品名（关于页、更新提示里用 ✓）
APP_NAME = "LanternLogic Agent"

#: 版本号：**唯一来源** ✓（改版本只改这一行 ✓ 守卫测试会盯着另一处别跑偏 ✓）
__version__ = "0.1.0"

#: 出品的名字（用户定的：主名「一人团队 / SoloTeam」，「匠台」当出品印记 ✓ 见交接书 §6"等你"）
#: ★ 这一格**先按现状写** ✓ 用户拍板改名时改这里一处即可 ✓（免得散在十几个文件里 ✗）
VENDOR = "丹东振兴云杉互联网服务工作室"

#: ★★ 2026-10-09：**源码地址** —— AGPL 第 13 条的**兑现方式** ✓（一处声明处处读 ✓）
#: 为什么它是一个**独立常量**而不是散在各处 ✗：
#:   AGPL 第 13 条要求"**通过网络与本程序交互的人**必须能拿到对应源码" ✓
#:   ⇒ 光把链接写在 README 里**不够** ✗（用网页/客户端的人根本看不到仓库 ✓）
#:   ⇒ 必须出现在**程序界面里**（设置 → 关于 ✓）+ 接口里（`/api/v1/license` ✓）
#:   ⇒ 所以它和版本号一样：**一处声明** ✓ 界面对外都读它 ✓
#: ★ 上线前把下面的占位符换成真地址 ✓（`https://github.com/yqrsyg2gz5-blip/lanternlogic-agent` 留着 = 自己没遵守 AGPL ✗）
#:   守卫测试会盯着它**必须不是空**、且在关于页能读到 ✓
SOURCE_URL = "https://github.com/yqrsyg2gz5-blip/lanternlogic-agent"

#: 本项目采用的许可证（SPDX 官方写法 ✓ —— 裸 `AGPL-3.0` 是已废弃写法 ✗）
LICENSE_ID = "AGPL-3.0-only"


def semver_tuple(v: str) -> tuple[int, ...]:
    """把版本串变成可比较的元组 ✓（`0.1.0` → `(0,1,0)` ✓）。

    ★ 故意**只认数字段** ✓：`v0.2.0` / `0.2.0-beta` / `0.2.0+build7` 都能比 ✓
      认不出来的段落当 0 ✓ —— 宁可保守地判"没更新"，也不许因为格式怪就弹一个假的"有新版本" ✗
      （提示用户去下载一个根本不存在的东西 = 最糟的那种"假消息" ✓）。
    """
    out: list[int] = []
    for part in str(v or "").lstrip("vV").replace("-", ".").replace("+", ".").split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        out.append(int(digits) if digits else 0)
    while len(out) < 3:
        out.append(0)
    return tuple(out[:4])


def is_newer(latest: str, current: str) -> bool:
    """`latest` 是不是比 `current` 新 ✓（相等 ⇒ False ✓ 绝不提示"有新版"✗）。"""
    return semver_tuple(latest) > semver_tuple(current)
