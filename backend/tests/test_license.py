"""License 闸回归 —— 试用期 + 商用授权（v2：Ed25519 非对称验签）。

K1 整改换锚说明（规矩⑮）：
· 旧锚点 test_valid_commercial_key_unlocks 用生产 `_secret()`+HMAC 自签合法 key——
  该签发逻辑（对称密钥硬编码）正是 K1 要消除的洞，生产代码已删除 `_secret`，
  旧锚点对象不复存在（git grep `_secret` 零命中可证）。
· 旧 test_tampered_or_garbage_key_rejected 里的 ASL1 样例 → ASL1 格式已废弃，
  替换为"v1 key 明确拒绝"断言。
新锚点：测试运行时自生成 Ed25519 密钥对并注入公钥（生产内置公钥对应私钥
不进仓库，单测无法也不应伪造生产 key——端点级锚点据此验证"无真实私钥必 422"）。
"""
from __future__ import annotations

import base64
import json
import time
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.license import _PUBLIC_KEY_B64, TRIAL_DAYS, LicenseState


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


@pytest.fixture()
def test_keypair():
    """测试专用密钥对（与生产密钥对无关）。"""
    sk = Ed25519PrivateKey.generate()
    from cryptography.hazmat.primitives import serialization
    pub_b64 = _b64e(sk.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw))
    return sk, pub_b64


def _mk(tmp_path: Path, pub_b64: str | None = None) -> LicenseState:
    return LicenseState(tmp_path / "data", public_key_b64=pub_b64)


def _sign(sk, payload: dict) -> str:
    p64 = _b64e(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    return f"ASL2.{p64}.{_b64e(sk.sign(p64.encode()))}"


def test_first_run_recorded_once(tmp_path):
    lic = _mk(tmp_path)
    first = lic.ensure_first_run()
    assert first == time.strftime("%Y-%m-%d", time.gmtime())
    # 再次实例化不重置（防"改日期绕试用期"——以落盘为准）
    assert _mk(tmp_path).first_run == first


def test_trial_not_expired_initially(tmp_path):
    lic = _mk(tmp_path)
    lic.ensure_first_run()
    assert not lic.trial_expired()
    assert 0 < lic.trial_days_left() <= TRIAL_DAYS


def test_expired_trial_blocks_task_creation(tmp_path):
    lic = _mk(tmp_path)
    lic.ensure_first_run()
    # 把首次运行日期改到 40 天前（模拟时间流逝）
    lic.first_run = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 40 * 86400))
    assert lic.trial_expired()
    ok, why = lic.can_create_task()
    assert not ok and "试用期" in why


def test_valid_commercial_key_unlocks(tmp_path, test_keypair):
    sk, pub_b64 = test_keypair
    lic = _mk(tmp_path, pub_b64)
    lic.first_run = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 40 * 86400))
    key = _sign(sk, {"name": "测试客户", "type": "commercial", "exp": ""})

    ok, msg = lic.activate(key)
    assert ok, msg
    assert not lic.trial_expired(), "有效商用 key 应解除试用期限制"
    ok2, _ = lic.can_create_task()
    assert ok2

    # 持久化：重新实例化仍是已激活状态
    lic2 = LicenseState(tmp_path / "data", public_key_b64=pub_b64)
    assert lic2.key == key
    assert lic2.license_kind_label() == "商用授权"


def test_wrong_private_key_rejected(tmp_path, test_keypair):
    """错误私钥签的 key → 拒绝，且 last_error 可观测。"""
    _, pub_b64 = test_keypair
    attacker_sk = Ed25519PrivateKey.generate()  # 攻击者自生成的另一对
    lic = _mk(tmp_path, pub_b64)
    ok, _ = lic.activate(_sign(attacker_sk, {"name": "伪造", "type": "commercial", "exp": ""}))
    assert not ok
    assert "签名" in lic.last_error
    assert lic.key is None


def _tamper_middle(s: str) -> str:
    """篡改中段一个字符（全量回归抓出的锚点缺陷：改 base64 **末位**可能只动到
    未用填充位——解码后字节不变、验签照常通过，锚点假红/假绿随机。中段字符
    的所有位都是有效位，篡改必改变解码结果）。"""
    m = len(s) // 2
    return s[:m] + ("A" if s[m] != "A" else "B") + s[m + 1:]


def test_tampered_key_rejected(tmp_path, test_keypair):
    """篡改 key 任意一段 → 拒绝。"""
    sk, pub_b64 = test_keypair
    lic = _mk(tmp_path, pub_b64)
    good = _sign(sk, {"name": "测试客户", "type": "commercial", "exp": ""})
    head, p64, sig = good.split(".")
    for bad in (f"{head}.{p64}.{_tamper_middle(sig)}", f"{head}.{_tamper_middle(p64)}.{sig}"):
        ok, _ = lic.activate(bad)
        assert not ok, f"被篡改的 key 必须拒绝：{bad[:40]}…"
        assert lic.key is None


def test_v1_and_garbage_key_rejected(tmp_path):
    lic = _mk(tmp_path)
    for bad in ("随便写的", "ASL1.eyJhIjoxfQ.deadbeef", "ASL2.!!!.0000", "ASL3.a.b"):
        ok, _ = lic.activate(bad)
        assert not ok, f"非法 key 必须拒绝：{bad}"
    assert lic.key is None, "校验失败的 key 不得落盘"
    assert lic.last_error, "拒绝必须留可观测原因"


def test_expired_key_rejected(tmp_path, test_keypair):
    sk, pub_b64 = test_keypair
    lic = _mk(tmp_path, pub_b64)
    key = _sign(sk, {"name": "测试客户", "type": "commercial", "exp": "2020-01-01"})
    ok, _ = lic.activate(key)
    assert not ok and "过期" in lic.last_error


def test_on_disk_tampered_key_fails_closed(tmp_path, test_keypair):
    """落盘后手改 license.json 里的 key → 重启按未授权处理 + 可观测。"""
    sk, pub_b64 = test_keypair
    lic = _mk(tmp_path, pub_b64)
    lic.activate(_sign(sk, {"name": "测试客户", "type": "commercial", "exp": ""}))
    d = json.loads((tmp_path / "data" / "license.json").read_text("utf-8"))
    d["license_key"] = _tamper_middle(d["license_key"])  # 手改中段一位（末位有填充位假篡改面）
    (tmp_path / "data" / "license.json").write_text(json.dumps(d), "utf-8")
    lic2 = LicenseState(tmp_path / "data", public_key_b64=pub_b64)
    assert lic2.key is None, "被篡改的落盘 key 必须按未授权处理"
    assert lic2.last_error


def test_production_public_key_embedded():
    """生产公钥必须存在且为 32 字节 Ed25519（防被误删/替换后全量 key 失效）。"""
    raw = base64.urlsafe_b64decode(_PUBLIC_KEY_B64 + "=" * (-len(_PUBLIC_KEY_B64) % 4))
    assert len(raw) == 32


def test_endpoint_rejects_forged_key():
    """端点级锚（规矩⑭）：无真实私钥伪造的 key 打 POST /api/v1/license 必 422。

    生产全局 _lic 用内置公钥；测试无法持有对应私钥 ⇒ 任何自签 key 都应被拒——
    这正是"私钥不在仓库"的端到端含义。
    """
    pytest.importorskip("fastapi.testclient")
    from fastapi.testclient import TestClient
    from app.main import app

    attacker_sk = Ed25519PrivateKey.generate()
    forged = _sign(attacker_sk, {"name": "伪造", "type": "commercial", "exp": ""})
    with TestClient(app, base_url="http://127.0.0.1:8642") as c:
        r = c.post("/api/v1/license", json={"key": forged})
        assert r.status_code == 422, r.text
        r2 = c.get("/api/v1/license")
        assert r2.status_code == 200 and r2.json()["activated"] is False
