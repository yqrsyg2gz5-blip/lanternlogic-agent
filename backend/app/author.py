# -*- coding: utf-8 -*-
"""作者卡 —— **只读 + 防伪签名** ✓（用户 2026-10-08 定的内容）

## 用户怎么定的（原话）

- "**真名你就写我工作室，非得写我名干啥**" ⇒ 主名只写**工作室全称** ✓ 不写个人名字 ✗
- 邮箱 `yangbo0801@163.com` / 微信 `yangbo1349` ✓ **点一下才显示** ✓
- 笔名"漫天炫舞大呲花" ⇒ 放**底部小字**当彩蛋 ✓
- 一句话："一个人独立完成" ✓

★ 工作室名**不在这里写死** ✗ —— 从 `version.VENDOR` 读 ✓
  （一处声明处处读 ✓ 2026-10-07 那回就是"文档里写错名字" ✓ 有测试钉着 ✓
    作者卡再抄一份就又多一个会写错的地方 ✗）

## 防伪签名怎么做的（为什么值得做）

**防的是**：别人拿这个程序**改个名、换张作者卡**就说是自己做的 ✗
做法：
  · 单独一对 **Ed25519** 密钥 ✓（**不跟授权那套共用** ✗ —— 授权私钥是商业机密 ✓
    混用一对 ⇒ 签名工具一外流，授权也跟着完蛋 ✓ 各管各的 ✓）
  · 卡的内容 → 规范化成字节 → **私钥签** → 签名写进 `author_card.json` ✓ 随程序发布 ✓
  · 程序内置**公钥** ✓ ⇒ 打开关于页就验 ✓
    对得上 ⇒ 「✅ 正版作者卡（已验证）」✓
    对不上/被改过 ⇒ 「⚠️ 这张卡不是原版」✓
★ 私钥**只在用户手里** ✓ 存在仓库外：
  `<仓库外的某个目录>\\作者卡密钥\\作者卡-私钥-请保管好-别进仓库.txt`
★ 签名工具：`scripts/sign_author_card.py` ✓（改完内容重新签一次即可 ✓ 不用改代码 ✓）
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

#: 内置**公钥**（Ed25519 raw hex ✓ 公开的 ✓ 与私钥配套 ✓）
#: ★ 换密钥 = 改这一行 + 重新签名 + 重发版 ✓（所以别轻易换 ✓）
PUBLIC_KEY_HEX = "67d37a21c7a0af3cd14722bae56e6c46db19016bbd716de28e5a2b7804f4ff60"

#: 卡的内容 + 签名（**随程序发布** ✓ 改内容用 scripts/sign_author_card.py 重签 ✓）
CARD_FILE = Path(__file__).resolve().parent / "author_card.json"

#: 参与签名的字段（顺序无关 ✓ 规范化成 sorted keys ✓）
#: ★ `signature` 自己不参与 ✓ —— 别的**都得**参与 ✗（少一个就等于那个字段可以随便改 ✓）
SIGNED_FIELDS = ("org", "line", "email", "wechat", "alias", "signed_at")


def _vendor() -> str:
    """工作室全称 —— **只从 version 拿** ✓（不在本文件里抄一份 ✗）。"""
    from . import version

    return str(version.VENDOR)


def load_card() -> dict[str, Any]:
    """读卡 ✓（读不到就返回一张"只有工作室名"的骨架 ✓ —— 不让关于页整个崩掉 ✓）。"""
    try:
        data = json.loads(CARD_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:                                   # noqa: BLE001
        return {}


def canonical(card: dict[str, Any]) -> bytes:
    """把参与签名的字段规范成字节 ✓ —— 排序 + 不转义中文 ✓（两侧算法必须一字不差 ✓）"""
    body = {k: card.get(k, "") for k in SIGNED_FIELDS}
    return json.dumps(body, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def fingerprint(card: dict[str, Any]) -> str:
    """指纹 = 规范化内容的 sha256 前 16 位 ✓（给人肉核对用 ✓ 不参与安全判断 ✓）"""
    return hashlib.sha256(canonical(card)).hexdigest()[:16]


def verify(card: dict[str, Any] | None = None) -> tuple[bool, str]:
    """验签 ✓ → (通过吗, 原因) —— **任何异常都算不通过** ✗（宁可说"验不了" ✓ 不说"没问题" ✗）

    ★ 这函数是防伪的**唯一**判据 ✓ —— 界面上的"✅ 正版"必须来自它 ✓
      绝不允许写成"反正就是正版" ✗（红绿有一条实验专门钉这个 ✓）
    """
    card = load_card() if card is None else card
    sig = str(card.get("signature", "") or "")
    if not sig:
        return False, "这张卡没有签名"
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric import ed25519

        pub = ed25519.Ed25519PublicKey.from_public_bytes(bytes.fromhex(PUBLIC_KEY_HEX))
        pub.verify(bytes.fromhex(sig), canonical(card))
        return True, ""
    except InvalidSignature:
        return False, "签名对不上（内容被改过，或不是原版）"
    except Exception as e:                               # noqa: BLE001
        return False, f"验签失败：{type(e).__name__}"


def card_for_ui() -> dict[str, Any]:
    """给界面的那张卡 ✓ —— **不含私钥、不含签名原文** ✗（只给验签结论 ✓）。"""
    card = load_card()
    ok, why = verify(card)
    return {
        # ★ 工作室名**以 version.VENDOR 为准** ✓（卡里那份只用于签名；两边不一致时以版本为准 ✓
        #   并有一条测试专门盯着"两边不许打架" ✗）
        "org": _vendor(),
        "line": str(card.get("line", "") or ""),
        "email": str(card.get("email", "") or ""),
        "wechat": str(card.get("wechat", "") or ""),
        "alias": str(card.get("alias", "") or ""),
        "signed_at": str(card.get("signed_at", "") or ""),
        "verified": bool(ok),
        "reason": why,
        "fingerprint": fingerprint(card),
    }
