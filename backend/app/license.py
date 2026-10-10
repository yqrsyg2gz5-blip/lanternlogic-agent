"""License 闸 —— 试用期 + 商用授权（用户拍板的商业模型：先免费试用 + 禁止商用）。

规则：
· 首次运行日期记在 data/license.json（老用户升级：第一次读到无 first_run 时以当天起算）；
· 试用期 30 天：到期后**创建新任务**返回 402（历史数据/导出/查看不受限——数据是用户自己的）；
· 商用授权 key：`ASL2.<payload_b64>.<sig_b64>`，sig = Ed25519(payload_b64)。
  v2 起改**非对称签名**（K1 整改）：产品只内置公钥，私钥永不进仓库/安装包——
  v1（HMAC 对称密钥硬编码）任何拿到安装包的人都可自签 key，已废弃且不再受理。
· 验签失败一律 fail-closed 且可观测：拒绝原因记 last_error 并打到 stderr（uvicorn 日志可见）。
· 禁止商用：个人/单一团队内部使用；转售、代运营、对外提供服务需 commercial key。

生成正式 key 用签发工具（卖家专用，**不在本仓库**：D:/AI/licgen-tool/licgen.py，
私钥 D:/AI/licgen-tool/license_private_key.pem 随机生成、不进任何 git）。
"""
from __future__ import annotations

import base64
import json
import sys
import time
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

TRIAL_DAYS = 30

# 产品内置的 Ed25519 **公钥**（32 字节，urlsafe-b64 去填充）。
# 对应私钥只存在于卖家签发机（仓库外），本仓库/安装包任何位置都不含私钥。
_PUBLIC_KEY_B64 = "n5FI3TI4_AHLEptd4MePLaeNZ8D0PdhKVirjWyDxPsc"


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _load_public_key(public_key_b64: str) -> Ed25519PublicKey:
    raw = _b64d(public_key_b64)
    if len(raw) != 32:
        raise ValueError("Ed25519 公钥必须 32 字节")
    return Ed25519PublicKey.from_public_bytes(raw)


class LicenseState:
    def __init__(self, data_dir: Path, public_key_b64: str | None = None) -> None:
        self._file = Path(data_dir) / "license.json"
        # public_key_b64 可注入（测试用自备密钥对）；缺省 = 生产内置公钥
        self._pub = _load_public_key(public_key_b64 or _PUBLIC_KEY_B64)
        self.key: str | None = None      # 已录入的商用 key（校验通过才落盘）
        self.info: dict[str, Any] = {}   # 解析出的 key 信息
        self.first_run: str = ""
        self.last_error: str = ""        # 最近一次验签失败原因（可观测性）
        self._load()

    # ---------- 持久化 ----------

    def _load(self) -> None:
        try:
            d = json.loads(self._file.read_text("utf-8"))
        except Exception:
            d = {}
        self.first_run = str(d.get("first_run") or "")
        self.key = d.get("license_key") or None
        self.info = {}
        if self.key and self.verify_key(self.key)[0]:
            self.info = self.verify_key(self.key)[1]
        else:
            if self.key:
                # 落盘的 key 校验不过（文件被手改/格式废弃）→ 按无 key 处理（fail-closed）
                print(f"[license] 落盘 key 校验失败（{self.last_error}），按未授权处理", file=sys.stderr)
            self.key = None

    def _save(self) -> None:
        self._file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({
            "first_run": self.first_run,
            "license_key": self.key,
        }, ensure_ascii=False, indent=1), "utf-8")
        import os
        os.replace(tmp, self._file)

    # ---------- 试用期 ----------

    def ensure_first_run(self) -> str:
        """首次运行日期不存在则以今天起算（幂等）。"""
        if not self.first_run:
            self.first_run = time.strftime("%Y-%m-%d", time.gmtime())
            self._save()
        return self.first_run

    def trial_days_left(self) -> int:
        if self.key:
            return -1  # 有商用 key：不受试用期约束
        try:
            # 复审：gmtime 写入就按 UTC 解析（mktime 按本地时区——东八区试用起点被前移近一天）
            import calendar
            t0 = calendar.timegm(time.strptime(self.first_run, "%Y-%m-%d"))
        except Exception:
            return TRIAL_DAYS
        used = (time.time() - t0) / 86400
        return max(0, TRIAL_DAYS - int(used))

    def trial_expired(self) -> bool:
        return self.trial_days_left() == 0

    # ---------- key 校验 ----------

    def verify_key(self, key: str) -> tuple[bool, dict[str, Any] | None]:
        """校验 `ASL2.<payload_b64>.<sig_b64>`；返回 (是否有效, 信息)。

        fail-closed：任何一步异常都拒绝，并把原因写进 self.last_error（可观测）。
        """
        self.last_error = ""
        try:
            parts = key.strip().split(".")
            if len(parts) != 3:
                self.last_error = "格式错误（应为 ASL2.<数据>.<签名> 三段）"
                return False, None
            if parts[0] == "ASL1":
                self.last_error = "v1（HMAC）key 已废弃，请联系销售换发 v2 key"
                return False, None
            if parts[0] != "ASL2":
                self.last_error = "未知版本前缀（仅支持 ASL2）"
                return False, None
            payload = json.loads(_b64d(parts[1]))
            sig = _b64d(parts[2])
            if len(sig) != 64:
                self.last_error = "签名长度非法（Ed25519 签名必须 64 字节）"
                return False, None
            try:
                self._pub.verify(sig, parts[1].encode())
            except InvalidSignature:
                self.last_error = "签名校验失败（key 被篡改或非本产品签发）"
                return False, None
            exp = str(payload.get("exp") or "")
            if exp and time.strftime("%Y%m%d", time.gmtime()) > exp.replace("-", ""):
                self.last_error = "授权已过期"
                return False, None
            return True, payload
        except Exception as e:
            self.last_error = f"解析失败（{type(e).__name__}）"
            return False, None

    def activate(self, key: str) -> tuple[bool, str]:
        ok, info = self.verify_key(key)
        if not ok:
            print(f"[license] activate 被拒绝：{self.last_error}", file=sys.stderr)
            return False, f"License key 无效（{self.last_error}）。格式 ASL2.<数据>.<签名>，请核对后重试"
        self.key = key.strip()
        self.info = info or {}
        self._save()
        return True, f"授权生效：{self.info.get('name') or '未署名'}（{self.license_kind_label()}）"

    def license_kind_label(self) -> str:
        if not self.key:
            return "试用期（禁止商用）"
        t = str(self.info.get("type") or "commercial")
        return {"commercial": "商用授权", "team": "团队授权", "personal": "个人授权"}.get(t, t)

    # ---------- 总门 ----------

    def can_create_task(self) -> tuple[bool, str]:
        """创建新任务前的闸。返回 (允许, 拒绝原因)。"""
        self.ensure_first_run()
        if self.key:
            return True, ""
        if self.trial_expired():
            return False, (
                f"试用期（{TRIAL_DAYS} 天）已结束。历史任务与数据仍可查看导出；"
                "继续使用请录入 License key（设置 → 关于 → 授权管理），或联系销售获取授权。"
                "本软件未经授权不得用于商业用途。"
            )
        return True, ""
