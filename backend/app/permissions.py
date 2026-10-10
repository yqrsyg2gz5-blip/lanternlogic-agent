# -*- coding: utf-8 -*-
"""按角色限权 —— 2026-10-07 用户点名要的那条：

> "**按角色限权**（测试只能看/跑 ✗ 不能改 ✓；现在审批**只按命令判** ✗ 不按"谁"判 ✗）"

## 这条要解决的真问题

现在的审批**只看命令本身** ✓ —— 同一句 `rm -rf dist`，
「程序员」跑和「测试工程师」跑是**一模一样**的待遇 ✗。
而团队模式里这两件事**根本不是一回事**：
  · 程序员删掉自己刚生成的构建产物 = 正常干活 ✓
  · **测试**去改被测代码 = **把独立验证这件事本身废掉了** ✗✗
    （测试说"我验过了"，可他刚刚亲手改了被验的东西 ✓ 那这个"验过"还有什么意义 ✓）

⇒ 权限必须**既看"干了什么"、也看"谁在干"** ✓。

## 收窄到一句话（用户原话）：**测试只能看 / 只能跑，不能改** ✓

具体落地（**刻意只禁"改"** ✓ 不搞"这个角色什么都不能干" ✗）：
  ✗ 改**已有**的文件（覆盖 = 改 ✗）—— 新建文件**可以** ✓（测试要能写自己的用例 ✓）
  ✗ 删除 / 移动 / 改名 / 重定向覆写 / 越界写 / 装包 / 结构性命令（`$()`、heredoc…）
  ✓ 读文件 ✓ 列目录 ✓ 跑测试 ✓ 联网查资料 ✓ 驱动浏览器 ✓ 新建文件 ✓

★ 为什么是"已有文件"这条线：**测试的活儿就是写用例** ✓
  一刀切成"什么都不能写" ⇒ 角色直接废掉 ✗（那是把功能做成摆设 ✓）。
  而这条线正好**复用审批层已有的能力面**（`_DESTRUCTIVE_OVERWRITE_HEADS` +
  `target_exists` ✓）——**一处口径** ✓ 不另写一套判断 ✗。

## 三条最要紧的规矩

**① 默认 = 现状** ✓✓ —— 认不出的角色、没设身份、普通单聊 ⇒ **一律不受限** ✓
   （这条保证了这个功能**不可能**弄坏任何现有行为 ✓ 128 组红绿一个都不该动 ✓）
**② 只读角色**只列**三个把关类角色**（测试/安全/审校 ✓）—— 他们不写东西是**本分** ✓
**③ 被挡住时要说话** ✓ —— 模型收到一句**能改做法**的说明（"换成程序员身份"✓），
   用户在事件流里也看得见 ✓（静默失败是最难查的 ✓ 本项目栽过多次 ✓）
"""
from __future__ import annotations

from typing import Any, Callable

#: 只能看 / 只能跑：不许改已有的东西（新建可以 ✓）
READONLY = "read_only"
#: 不受限（默认 —— 认不出的角色、没设身份、普通单聊都走这条 ✓）
FULL = "full"

#: 哪些角色是"把关类" ⇒ 只读 ✓
#: ★ 只收**本分就是"不改东西"的角色** ✓ —— 多收一个就是白白废掉一个角色的能力 ✗
ROLE_PROFILE: dict[str, str] = {
    "测试工程师": READONLY,   # 独立验证的意义就在这儿：把关的人不能自己改被测物 ✓
    "安全顾问": READONLY,     # 审计不该顺手改被审计的东西 ✓
    "审校编辑": READONLY,     # 把关文稿质量的不该自己改稿 ✓
}

PROFILE_LABEL: dict[str, str] = {
    READONLY: "只能看 / 只能跑（不能改已有的东西）",
    FULL: "不受限",
}

#: 只读角色**仍然可以**做的事（写在提示里，让模型和用户都知道还有哪些路 ✓）
CAN_STILL = ("读文件", "列目录", "跑测试/跑命令看结果", "联网查资料", "驱动浏览器", "新建文件")


def profile_of(role: str) -> str:
    """这个角色是什么档。**认不出来一律 FULL** ✓（默认不改变现状 —— 最要紧的一条 ✓）。"""
    return ROLE_PROFILE.get(str(role or "").strip(), FULL)


def is_readonly(role: str) -> bool:
    return profile_of(role) == READONLY


def describe(role: str) -> dict[str, Any]:
    """给界面看的一份说明 ✓（身份选择器上要标出"这个角色不能改"✓ 别让用户选完才发现 ✓）。"""
    prof = profile_of(role)
    return {
        "role": str(role or ""),
        "profile": prof,
        "label": PROFILE_LABEL[prof],
        "can_write": prof != READONLY,
        "can_still": list(CAN_STILL) if prof == READONLY else [],
    }


def _deny_reason(role: str, what: str) -> str:
    """被挡住时**必须能照着改做法** ✓（模型看得懂、用户也看得懂 ✓）。"""
    return (
        f"⛔ 这一步被**角色权限**挡住了：当前身份「{role}」是**只能看 / 只能跑**的角色，"
        f"{what}。\n"
        f"这不是命令本身危险，而是**这个角色不该改东西**（{role}的本分是独立核查 ✓）——\n"
        "  · 你仍然可以：" + "、".join(CAN_STILL) + " ✓\n"
        "  · 确实需要改动时：请让用户把这个任务的身份换成「程序员」一类的可写角色，"
        "或者把这一步交给那位同事来做 ✓（**不要**试图绕过，比如换个写法去覆盖同一个文件 ✗）"
    )


def block_reason(
    role: str,
    tool: str,
    args: dict[str, Any] | None,
    *,
    target_exists: Callable[[str], bool | None] | None = None,
) -> str | None:
    """**工具级**的只读约束：改**已有**文件 ⇒ 拒 ✓（新建放行 ✓）。返回原因或 None ✓。

    `target_exists` 就是 loop 里审批用的那个（同一份实现 ✓ 一处口径 ✓）；
    它返回 `None` = **核不实**（越界/变量路径/符号链接逃逸）⇒ 这里**fail-closed 拦下** ✓
    （与审批层同一条口径：说不清就当"会改到东西"✓）。
    """
    if not is_readonly(role):
        return None
    if tool != "file_write" or not args:
        return None
    raw = str(args.get("path") or args.get("file") or args.get("filename") or "").strip()
    if not raw:
        return None
    exists = target_exists(raw) if target_exists is not None else None
    if exists is False:
        return None                      # 新建文件 ✓ 放行（测试要写自己的用例 ✓）
    return _deny_reason(role, f"不能覆盖/修改已有文件（{raw}）")


def shell_block_reason(role: str, verdict: Any) -> str | None:
    """**命令级**的只读约束：审批层判"要问"的那些（写/删/移/装包/越界/结构性…）
    对只读角色**直接拒** ✓；判"纯只读越界"的放行 ✓（那本来就是"看" ✓）。

    ★ 为什么复用审批层的结论而不是自己再判一遍：
      那个模块是**十二轮加固**出来的（引号拼接/包装器/赋值前缀/容器 CLI… ✓），
      自己再写一套必然更弱 ✓ 而且"两处口径"正是本项目栽过四次的老坑 ✓。
    """
    if not is_readonly(role) or verdict is None:
        return None
    action = getattr(verdict, "action", "ask")
    if action == "allow_readonly":
        return None                       # 纯只读越界：那是"看" ✓ 放行 ✓
    if action in ("allow_all", "allow_forever"):
        # ★ 「本任务全部允许」/「这类以后都别问」是**对某条命令的信任** ✓
        #   而角色限制是**对"谁"的约束** ✗ —— 两者不是一回事，前者不能盖掉后者 ✓
        return _deny_reason(role, "不能改东西（你之前放行的只是「这条命令」，换不了这个角色）")
    return _deny_reason(role, f"不能执行会改东西的命令：{str(getattr(verdict, 'reason', ''))[:60]}")
