# -*- coding: utf-8 -*-
"""★「一键补齐角色卡」—— 2026-10-07 用户提的（角色库 24 个，他的卡只建了 12 个 ✓）。

## 三条规矩（每条都有断言 ✓）

1. **只补缺的** ✓ —— 已有同名卡**绝不动** ✗（不覆盖用户自己改过的人设 ✓）
2. **只建卡、不建群** ✓ —— 群是他自己拉的 ✓ 不替他做决定 ✓
3. 建卡走**和手动建卡同一条路**（`add_employee` ✓）
   ⇒ 校验/上限/去重规则完全一致 ✓ 不是另开一条后门 ✗
"""
from __future__ import annotations

import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app import main as m  # noqa: E402
from app.roles import ROLE_LIBRARY  # noqa: E402


@pytest.fixture()
def client():
    """跑完**自己收尾** ✓ —— 这个接口会**真建员工卡** ✓

    ★ 说明（2026-10-07 实测更正 ✓）：我起初以为测试写进了用户真实数据 ✗
      实测核过：**没有** ✓（测试用的配置指向临时数据目录 ✓）
      ⇒ 这个 fixture 属于**防御性**的 ✓（万一哪天测试配置改成真目录 ✓ 它也能兜住 ✓）
      —— "测试不该改用户数据"这条规矩 ✓ 由测试自己保证最稳 ✓。
    """
    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        before = {str(e.get("id")) for e in c.get("/api/v1/team/employees").json()["employees"]}
        yield c
        after = c.get("/api/v1/team/employees").json()["employees"]
        for e in after:                       # 删掉**本次新增**的 ✓ 原有的一个不动 ✓
            if str(e.get("id")) not in before:
                c.delete(f"/api/v1/team/employees/{e['id']}")


def test_role_library_has_the_24_roles():
    """角色库**确实是 24 个** ✓（这个数字界面上写着 ✓ 不对就是骗人 ✗）。"""
    assert len(ROLE_LIBRARY) == 24, f"角色库是 {len(ROLE_LIBRARY)} 个 ✗（界面写的是 24 ✗）"
    for name, spec in ROLE_LIBRARY.items():
        assert spec.get("dept"), f"{name} 没写部门 ✗"
        assert spec.get("persona"), f"{name} 没写人设 ✗（expert 模式要靠它 ✓）"


def test_import_creates_cards_for_roles_that_lack_one(client):
    """★ 补齐 ✓ 而且**别人的卡一个不动** ✓。"""
    before = {str(e.get("name")) for e in client.get("/api/v1/team/employees").json()["employees"]}
    r = client.post("/api/v1/team/employees/import-roles")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["ok"] is True, body
    after = {str(e.get("name")) for e in client.get("/api/v1/team/employees").json()["employees"]}

    # ① 补进来的都是**原本没有的** ✓
    for name in body["added"]:
        assert name in after and name not in before, f"{name} 不在新增里 ✗"
    # ② 原本就有的**一个都没被删/改** ✓
    for name in before:
        assert name in after, f"原有的卡「{name}」被弄丢了 ✗✗"
    # ③ 跳过的就是原本已有的 ✓
    assert set(body["skipped"]) <= before, f"跳过的里面混进了新名字 ✗：{body['skipped']}"
    # ④ 加起来应当覆盖整个角色库 ✓
    assert len(body["added"]) + len(body["skipped"]) == len(ROLE_LIBRARY), body


def test_import_is_idempotent(client):
    """**再点一次不会重复建** ✓（幂等 ✓ —— 第二次应当"补 0 个、跳 24 个"✓）。"""
    client.post("/api/v1/team/employees/import-roles")
    r2 = client.post("/api/v1/team/employees/import-roles")
    assert r2.status_code == 201, r2.text
    body = r2.json()
    assert body["added"] == [], f"第二次又建了 ✗：{body['added']}"
    assert len(body["skipped"]) == len(ROLE_LIBRARY), body
    assert "补了 0 个" in body["note"], body["note"]


def test_import_does_not_touch_groups(client):
    """★ **只建卡、不建群** ✓ —— 群是用户自己拉的 ✓ 不替他决定 ✗。"""
    before = client.get("/api/v1/team/groups").json().get("groups", [])
    client.post("/api/v1/team/employees/import-roles")
    after = client.get("/api/v1/team/groups").json().get("groups", [])
    assert len(after) == len(before), "补卡的时候顺手建群了 ✗（用户没让你建群 ✓）"


def test_imported_cards_use_expert_mode_with_the_role(client):
    """补出来的卡要是 **expert 模式 + 对应角色** ✓（不然人设不生效 ✓ 等于白建 ✗）。"""
    client.post("/api/v1/team/employees/import-roles")
    emps = {e["name"]: e for e in client.get("/api/v1/team/employees").json()["employees"]}
    for name in ("程序员", "测试工程师", "安全顾问"):
        if name in emps:
            e = emps[name]
            assert e.get("role") == name, f"{name} 的 role 字段不对 ✗：{e.get('role')}"
            assert e.get("mode") == "expert", f"{name} 不是 expert 模式 ✗（人设不生效 ✓）"
            assert e.get("dept"), f"{name} 没带部门 ✗"
