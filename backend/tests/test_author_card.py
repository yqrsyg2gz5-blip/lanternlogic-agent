# -*- coding: utf-8 -*-
"""作者卡测试 —— **只读 + 防伪签名** ✓（用户 2026-10-08 定的内容 ✓）

用户怎么定的（原话）：
  · "**真名你就写我工作室，非得写我名干啥**" ⇒ 主名只写工作室全称 ✓ 不写个人名字 ✗
  · 邮箱 `yangbo0801@163.com` / 微信 `yangbo1349` ✓（"点一下才显示" ✓）
  · 笔名「漫天炫舞大呲花」⇒ 底部小字彩蛋 ✓
  · 一句话「一个人独立完成」✓

★ 本文件最要紧的两条：
  1. **签名必须真验** ✓ —— 「✅ 正版」不许是写死的 ✗（回滚实验：verify 写死 True ⇒ 必红 ✓）
  2. **工作室名不许两处打架** ✗ —— 卡里的 `org` 必须 = `version.VENDOR` ✓
     （2026-10-07 就出过"文档里写错名字" ✓ 有测试钉着 ✓ 作者卡再抄一份就是又开一个口子 ✗）
"""
from __future__ import annotations

import json
import pathlib
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app import author  # noqa: E402
from app import main as m  # noqa: E402
from app import version  # noqa: E402

_FE = (pathlib.Path(__file__).resolve().parents[2]
       / "frontend" / "src" / "components" / "SettingsPanel.tsx")
_API = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src" / "api.ts"


def test_the_card_ships_with_the_real_content():
    """★ 卡上就该有这几样 ✓（用户点名给的 ✓ 少一个都算没做到 ✗）。"""
    c = author.load_card()
    assert author._vendor() == "丹东振兴云杉互联网服务工作室", author._vendor()
    for field in ("line", "email", "wechat", "alias", "signed_at", "signature"):
        assert str(c.get(field, "")).strip(), f"作者卡少了 {field} ✗：{c}"
    assert c["line"] == "一个人独立完成", c["line"]
    assert c["email"] == "yangbo0801@163.com" and c["wechat"] == "yangbo1349", c
    assert c["alias"] == "漫天炫舞大呲花", c["alias"]   # ★ 2026-10-09 用户更正：是「炫舞」✓


def test_no_personal_name_on_the_card():
    """★ **不写个人名字** ✗ —— 用户原话："真名你就写我工作室，非得写我名干啥" ✓

    ★ 判据取"卡里不许出现个人姓名" ✓（工作室名里没有那两个字 ✓）
    """
    raw = json.dumps(author.load_card(), ensure_ascii=False)
    assert "杨波" not in raw, f"卡上写了个人名字 ✗（用户明确说只写工作室 ✓）：{raw}"
    # 界面上也不许出现（关于页 + 作者卡那块 ✓）
    fe = _FE.read_text("utf-8")
    assert "杨波" not in fe, "界面上写了个人名字 ✗"


def test_the_signature_verifies_for_real():
    """★★ **签名必须真验得过** ✓ —— 而且**改一个字就该验不过** ✗

    回滚实验：把 `verify` 写成"反正返回 True" ⇒ 本条第二段必红 ✓
    """
    ok, why = author.verify()
    assert ok is True, f"官方发布的卡验不过 ✗：{why}"

    # 篡改任意一个参与签名的字段 ⇒ 必须验不过 ✓
    for field in author.SIGNED_FIELDS:
        bad = dict(author.load_card())
        bad[field] = str(bad.get(field, "")) + "改"
        ok2, why2 = author.verify(bad)
        assert ok2 is False, f"改了 {field} 竟然还能验过 ✗ ⇒ 签名是摆设 ✗"
        assert why2, "验不过时必须给出原因 ✓（界面要显示它 ✓）"

    # 没签名的卡 ⇒ 验不过 ✓ 且原因说得清 ✓
    ok3, why3 = author.verify({k: author.load_card().get(k, "") for k in author.SIGNED_FIELDS})
    assert ok3 is False and "签名" in why3, (ok3, why3)


def test_the_card_org_and_version_vendor_never_disagree():
    """★ **一处声明处处读** ✓ —— 卡里的 org 与 `version.VENDOR` 必须一字不差 ✓

    （作者卡要是自己抄一份工作室名 ⇒ 改名那天就会出现"两处打架" ✗
      2026-10-07 已经踩过一次 ✓ 那次是文档里写着旧名字 ✓ 有测试钉着 ✓
      ⇒ 这次的作者卡**直接从 version 读** ✓ 再加一条测试把两边钉死 ✓）
    """
    assert author.load_card()["org"] == version.VENDOR, \
        f"卡里写的是「{author.load_card()['org']}」而 version.VENDOR 是「{version.VENDOR}」✗"
    ui = author.card_for_ui()
    assert ui["org"] == version.VENDOR, ui


def test_the_endpoint_gives_the_card_without_the_signature():
    """★ 接口在 ✓ 而且**只给结论、不给签名原文** ✓（更不给私钥 ✗）。"""
    src = pathlib.Path(m.__file__).read_text("utf-8")
    assert '@app.get("/api/v1/author")' in src, "没有作者卡接口 ✗"
    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        r = c.get("/api/v1/author")
        assert r.status_code == 200, r.text
        body = r.json()
    assert body["ok"] is True and body["verified"] is True, body
    assert body["org"] == version.VENDOR and body["email"] == "yangbo0801@163.com", body
    assert body["fingerprint"] == author.fingerprint(author.load_card()), body
    assert "signature" not in body and "PUBLIC_KEY" not in json.dumps(body), \
        f"接口把签名/密钥吐出去了 ✗：{body}"


def test_the_ui_shows_the_card_and_hides_contacts_until_clicked():
    """★ 关于页得有这块卡 ✓ 而且**邮箱/微信默认不显示** ✓ 点一下才展开 ✓

    回滚实验：把"展开"那层判断拿掉（默认就显示联系方式）⇒ 本条必红 ✓
    """
    fe = _FE.read_text("utf-8")
    assert "getAuthorCard()" in fe, "关于页没读作者卡 ✗"
    assert "authOpen" in fe, "没有展开/收起状态 ✗"
    assert "{authOpen ? (" in fe, "展开判断没了 ⇒ 邮箱微信会默认摆在页面上 ✗"
    # 「✅ 正版」必须挂在 verified 上（不许写死 ✓）
    assert "auth.verified" in fe, "界面没按**后端验签结论**显示正版状态 ✗（写死就是骗人 ✗）"
    assert "这张卡不是原版" in fe, "验签不过时没有如实提示 ✗"
    # 笔名彩蛋要在 ✓
    assert "江湖人称" in fe and "auth.alias" in fe, "笔名彩蛋没了 ✗"
    # 接口层也得有（不然界面拿不到 ✓）
    api = _API.read_text("utf-8")
    assert "getAuthorCard" in api and "'/author'" in api, "api.ts 里没接作者卡接口 ✗"


def test_the_pen_name_is_a_small_footer_not_the_main_line():
    """★ 笔名只是**底部小字彩蛋** ✓ —— 主名必须是工作室 ✓"""
    fe = _FE.read_text("utf-8")
    # 主名那行显示的是 org ✓
    assert "<b>{auth.org}</b>" in fe, "主名没显示工作室名 ✗"
    # 笔名那行必须带"江湖人称"且是灰小字（同一个 div 里有 opacity ✓）
    i = fe.index("江湖人称")
    seg = fe[max(0, i - 260):i + 40]
    assert "opacity" in seg, f"笔名没有做成小字灰字 ⇒ 会喧宾夺主 ✗：{seg[-120:]}"
