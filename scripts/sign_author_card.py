# -*- coding: utf-8 -*-
"""给作者卡签名（作者自己用 ✓ 私钥**不进仓库** ✗）

用法（私钥文件在评审目录里 ✓）：
    python scripts\\sign_author_card.py --key "<私钥文件的路径>"

它会：读 `backend/app/author_card.json` 的内容 → 用私钥签 → 把签名写回去 ✓
★ 只改 `signature` 一个字段 ✓ 别的字段一个字不动 ✗
★ 私钥也能走环境变量 `AUTHOR_CARD_KEY_HEX`（CI/临时用 ✓ 别把私钥写进命令行历史 ✗）
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import author  # noqa: E402


def read_key_hex(path: str | None) -> str:
    """私钥 hex：优先环境变量 ✓ 其次文件（文件里允许有 # 开头的注释行 ✓）"""
    env = os.environ.get("AUTHOR_CARD_KEY_HEX", "").strip()
    if env:
        return env
    if not path:
        raise SystemExit("✗ 没给私钥：--key <文件> 或设 AUTHOR_CARD_KEY_HEX")
    raw = pathlib.Path(path).read_text(encoding="utf-8")
    for line in raw.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            return line
    raise SystemExit(f"✗ 那个文件里没有十六进制私钥：{path}")


def main() -> int:
    ap = argparse.ArgumentParser(description="给作者卡签名")
    ap.add_argument("--key", help="私钥文件（仓库外 ✓）；不填则读 AUTHOR_CARD_KEY_HEX")
    ap.add_argument("--check", action="store_true", help="只验签不签（CI/体检用 ✓）")
    args = ap.parse_args()

    card_path = author.CARD_FILE
    if not card_path.exists():
        card_path.write_text(json.dumps({
            "org": author._vendor(),
            "line": "一个人独立完成",
            "email": "yangbo0801@163.com",
            "wechat": "yangbo1349",
            "alias": "漫天炫舞大呲花",
            "signed_at": dt.date.today().isoformat(),
            "signature": "",
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"[..] 卡不存在，已建骨架：{card_path.relative_to(ROOT)}")

    card = json.loads(card_path.read_text(encoding="utf-8"))

    if args.check:
        ok, why = author.verify(card)
        print(("✓ 签名有效" if ok else f"✗ {why}") + f" · 指纹 {author.fingerprint(card)}")
        return 0 if ok else 1

    from cryptography.hazmat.primitives.asymmetric import ed25519

    key_hex = read_key_hex(args.key)
    priv = ed25519.Ed25519PrivateKey.from_private_bytes(bytes.fromhex(key_hex))
    pub_hex = priv.public_key().public_bytes_raw().hex() if hasattr(priv.public_key(), "public_bytes_raw") else None
    if pub_hex and pub_hex != author.PUBLIC_KEY_HEX:
        print(f"✗ 这把私钥对应的公钥（{pub_hex[:16]}…）与程序内置的（{author.PUBLIC_KEY_HEX[:16]}…）**不是一对** ✗")
        print("  ⇒ 签了也验不过 ✓ 先确认用的是不是当初生成的那对 ✓")
        return 2

    card.setdefault("signed_at", dt.date.today().isoformat())
    card["signature"] = priv.sign(author.canonical(card)).hex()
    card_path.write_text(json.dumps(card, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    ok, why = author.verify(card)
    print(f"[ok] 已签名：{card_path.relative_to(ROOT)}")
    print(f"     验签：{'✓ 通过' if ok else '✗ ' + why} · 指纹 {author.fingerprint(card)}")
    return 0 if ok else 3


if __name__ == "__main__":
    raise SystemExit(main())
