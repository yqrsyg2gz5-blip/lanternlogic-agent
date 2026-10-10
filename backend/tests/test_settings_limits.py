"""★ 2026-10-06：把「单任务步数上限」与「单价表」接进设置界面（此前只能改配置文件）。

用户反复提到这两样"看得见、但改不了"的东西：
· 步数上限 —— 代码活要"写-跑-改"迭代，25 步必然被砍在半路
· 单价表 —— 不填就只报 token、不报钱；填一次之后群里每步都能看到约 ¥X

这个端点就是给设置界面用的：一次性写两样，写完 `_save_config()` 落盘。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import main as m
from app import pricing
from app.config import AppConfig, load_config


@pytest.fixture()
def client(monkeypatch, tmp_path):
    """不碰真配置：写回被替换掉，内存值也存回来。"""
    saved: list[dict] = []
    monkeypatch.setattr(m, "_save_config", lambda: saved.append(m.cfg.model_dump()))
    before = (m.cfg.model.max_iterations, dict(m.cfg.pricing or {}))
    # ★ base_url 必须是本机地址：应用有 DNS-rebinding 防护，`testserver` 会被 403
    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        yield c, saved
    m.cfg.model.max_iterations = before[0]
    m.cfg.pricing = before[1]
    pricing.PRICES.clear()


def test_set_limits_writes_both_and_saves(client):
    c, saved = client
    r = c.post("/api/v1/settings/limits", json={
        "max_iterations": 60,
        "pricing": {"mimo-v2.6-flash": {"in": 2.0, "out": 8.0, "cached": 0.5}},
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["max_iterations"] == 60
    assert "mimo-v2.6-flash" in body["pricing"]
    assert "已生效" in body["note"], body
    assert saved, "必须落盘（_save_config 被调用）"
    # 单价立刻生效（群里下一行账单就按新价算）
    assert pricing.estimate("mimo-v2.6-flash", 1_000_000, 0) == 2.0


def test_set_limits_can_change_one_thing_only(client):
    c, _ = client
    before = m.cfg.model.max_iterations
    r = c.post("/api/v1/settings/limits", json={"pricing": {"m": {"in": 1.0, "out": 2.0}}})
    assert r.status_code == 200
    assert m.cfg.model.max_iterations == before, "只改单价时不该动步数上限"
    r2 = c.post("/api/v1/settings/limits", json={"max_iterations": 30})
    assert r2.status_code == 200 and m.cfg.model.max_iterations == 30
    assert "m" in m.cfg.pricing, "只改步数时不该把单价表清掉"


def test_out_of_range_step_budget_is_rejected(client):
    c, _ = client
    for bad in (0, -1, 501):
        r = c.post("/api/v1/settings/limits", json={"max_iterations": bad})
        assert r.status_code == 422, (bad, r.status_code)


def test_empty_pricing_says_it_plainly(client):
    """空单价表 ⇒ 如实说明"只报 token，不报钱"（不编数字）。"""
    c, _ = client
    r = c.post("/api/v1/settings/limits", json={"pricing": {}})
    assert r.status_code == 200
    assert "只报 token" in r.json()["note"], r.json()


def test_config_still_has_pricing_field_with_empty_default():
    """配置结构向后兼容（add-only）：老配置文件没有 pricing 段也能读。"""
    assert AppConfig(version=1).pricing == {}
    assert "pricing" in AppConfig.model_fields
    assert load_config().model.max_iterations >= 1


def test_official_price_wins_over_third_party_conversion(client, monkeypatch):
    """★★ 2026-10-06 用户批评："中国是中国定价，美国是美国定价，不能拿美元换算" ✓

    他说得对 ✓：OpenRouter 那类第三方标的是**它自己渠道的美元价** ✗，
    跟账单上的**官方人民币价**不是一回事 ✓。所以：**官方价优先** ✓（从官方定价页抄，
    带来源链接与更新日期 ✓，可核对 ✓）；第三方只作参考、并写明"不是官方价" ✓。
    """
    import httpx

    called = {"n": 0}

    class _R:
        def raise_for_status(self): return None
        def json(self): return {"data": [{"id": "xiaomi/mimo-v2.6-flash",
                                          "pricing": {"prompt": "0.00000014",
                                                      "completion": "0.00000028"}}]}

    class _C:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **k):
            called["n"] += 1
            return _R()

    monkeypatch.setattr(httpx, "AsyncClient", lambda **k: _C())
    c, _ = client
    r = c.post("/api/v1/settings/pricing/fetch", json={"model": "mimo-v2.6-flash"})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["official"] is True, b
    assert b["pricing"] == {"in": 1.0, "out": 2.0, "cached": 0.02}, b   # 官方人民币价
    assert "mimo.mi.com" in b["source"], b
    assert "官方价" in b["note"], b["note"]
    assert called["n"] == 0, "有官方价时**不该**去第三方查（省一次联网、也避免混口径）"


def test_third_party_lookup_is_labelled_as_reference_only(client, monkeypatch):
    """官方表里没有的模型 ⇒ 才去第三方查，且**必须**写明"不是官方价、仅供参考" ✓。"""
    import httpx

    class _R:
        def raise_for_status(self): return None
        def json(self): return {"data": [{"id": "somevendor/some-model",
                                          "pricing": {"prompt": "0.0000002",
                                                      "completion": "0.0000008"}}]}

    class _C:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **k): return _R()

    monkeypatch.setattr(httpx, "AsyncClient", lambda **k: _C())
    c, _ = client
    b = c.post("/api/v1/settings/pricing/fetch",
               json={"model": "somevendor-some-model", "fx": 7.0}).json()
    assert b["official"] is False, b
    assert "第三方渠道参考价" in b["note"] and "不是官方价" in b["note"], b["note"]


def test_auto_fetch_pricing_converts_and_reports_source(client, monkeypatch):
    """★ 2026-10-06（用户提的）："价格随时会变，不能让我手填吧？"

    **先说实话**：服务商自己的 API **不提供价格** ✗（实测 /models 只有 id/object/owned_by）。
    能查的是 **OpenRouter 公开表**（无需密钥、带单价、单位是**美元/token**）⇒
    换算成**元/百万**填回来，并把**来源 + 时间 + 汇率**一起返回 ✓。
    """
    import httpx

    fake = {"data": [
        {"id": "deepseek/deepseek-chat", "pricing": {"prompt": "0.0000002",
                                                     "completion": "0.0000008",
                                                     "input_cache_read": "0.00000005"}},
    ]}

    class _R:
        def raise_for_status(self): return None
        def json(self): return fake

    class _C:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **k): return _R()

    monkeypatch.setattr(httpx, "AsyncClient", lambda **k: _C())
    c, _ = client
    r = c.post("/api/v1/settings/pricing/fetch", json={"model": "deepseek-chat", "fx": 7.0})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["found"] is True, b
    # 0.0000002 美元/token × 1e6 × 7.0 = 1.4 元/百万（输入）
    assert b["pricing"]["in"] == pytest.approx(1.4), b
    assert b["pricing"]["out"] == pytest.approx(5.6), b
    assert b["pricing"]["cached"] == pytest.approx(0.35), b
    assert "openrouter" in b["source"] and b["fetched_at"], b
    assert b["official"] is False, b
    assert "以你服务商的官方账单为准" in b["note"], b["note"]


def test_auto_fetch_pricing_says_not_found_instead_of_inventing(client, monkeypatch):
    """查不到就**如实说查不到** ✗（绝不编一个"看起来对"的价 ✓ —— 那是会被当账单的）。"""
    import httpx

    class _R:
        def raise_for_status(self): return None
        def json(self): return {"data": [{"id": "openai/gpt-4o", "pricing": {"prompt": "0.000005"}}]}

    class _C:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **k): return _R()

    monkeypatch.setattr(httpx, "AsyncClient", lambda **k: _C())
    c, _ = client
    # 用一个"官方表里没有、公开表里也没有"的名字，才能走到"如实说查不到"这条 ✓
    r = c.post("/api/v1/settings/pricing/fetch", json={"model": "totally-unknown-model-xyz"})
    assert r.status_code == 200
    b = r.json()
    assert b["found"] is False, b
    assert "没有" in b["note"] and "官方定价页" in b["note"], b["note"]


def test_official_table_covers_mimo_and_deepseek_with_sources():
    """★ 2026-10-06：官方表要用**官方页抄来的**价（含来源 + 更新日期 ✓），不许拿美元换算 ✗。

    已核实的两家：小米 MiMo（页面标注更新于 2026-09-22）与 DeepSeek（2026-10-06 抄录，
    它有**时段价** ⇒ 表里存**高峰价**，注释写明空闲半价 ✓）。
    """
    from app import pricing as P

    mimo = P.official_for("mimo-v2.6-flash")
    assert mimo and mimo["in"] == 1.00 and mimo["out"] == 2.00 and mimo["cached"] == 0.02, mimo
    assert "mimo.mi.com" in mimo["source"] and mimo["updated"], mimo

    ds = P.official_for("deepseek-flash")
    assert ds and ds["in"] == 2.00 and ds["out"] == 8.00 and ds["cached"] == 0.04, ds
    assert "api-docs.deepseek.com" in ds["source"], ds
    assert "空闲" in ds["note"] and "半价" in ds["note"], ds["note"]

    pro = P.official_for("deepseek-v4-pro")
    assert pro and pro["in"] == 9.00 and pro["out"] == 27.00, pro


def test_official_table_has_qwen_domestic_price_only():
    """★ 2026-10-06：阿里云百炼官方页**自己就分"华北2（北京）/ 新加坡 / 美国"三套价** ✓
    —— 正是用户那句"中国是中国定价，美国是美国定价" ✓。表里**只收国内价** ✓。"""
    from app import pricing as P

    q = P.official_for("qwen3-14b")
    assert q and q["in"] == 1.00 and q["out"] == 4.00, q
    assert "华北2" in q["region"] or "国内" in q["region"], q
    assert "help.aliyun.com" in q["source"], q
    assert "只抄了国内" in q["note"] and "新加坡" in q["note"], q["note"]


def test_official_table_covers_kimi_and_glm_domestic():
    """★ 2026-10-06 补全：Kimi 与智谱的**国内人民币价** ✓

    抓取技巧（本班实测）：Mintlify 系的文档站**在地址后加 `.md`** 就能拿到纯文本版 ✓；
    直接抓页面只会得到前端模板、**没有表格数字** ✗（先踩了这个坑 ✓）。
    ★ 且必须用**国内站**：Kimi 国际站同款模型是美元价（$3/$15）✗，国内站才是 ¥20/¥100 ✓
    —— 正是用户那句"中国是中国定价，美国是美国定价" ✓。
    """
    from app import pricing as P

    k3 = P.official_for("kimi-k3")
    assert k3 and k3["in"] == 20.00 and k3["out"] == 100.00 and k3["cached"] == 2.00, k3
    assert "platform.kimi.com" in k3["source"], k3        # 国内站，不是 .ai
    k26 = P.official_for("kimi-k2.6")
    assert k26 and k26["in"] == 6.50 and k26["out"] == 27.00, k26

    g = P.official_for("glm-5.3")
    assert g and g["in"] == 8.00 and g["out"] == 28.00 and g["cached"] == 2.00, g
    assert "docs.bigmodel.cn" in g["source"], g
    gf = P.official_for("glm-5.3-flash")
    assert gf and gf["in"] == 0.80 and gf["out"] == 2.80, gf
    free = P.official_for("glm-4.7-flash")
    assert free and free["in"] == 0.0 and "免费" in free["note"], free


def test_unknown_model_points_at_the_official_pricing_page(client, monkeypatch):
    """查不到价时，别只说"查不到" ✗ —— 把这家**官方定价页**给出来 ✓（目录见 PRICING_PAGES）。"""
    import httpx

    from app import pricing as P

    class _R:
        def raise_for_status(self): return None
        def json(self): return {"data": []}

    class _C:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **k): return _R()

    monkeypatch.setattr(httpx, "AsyncClient", lambda **k: _C())
    monkeypatch.setattr(m.cfg.model, "provider", "deepseek")
    c, _ = client
    b = c.post("/api/v1/settings/pricing/fetch", json={"model": "deepseek-未来的模型"}).json()
    assert b["found"] is False
    assert "官方定价页" in b["note"] and "api-docs.deepseek.com" in b["note"], b["note"]
    assert P.page_for("kimi", "") and "kimi" in P.page_for("kimi", "")["url"], P.PRICING_PAGES


def test_ui_exposes_both_fields():
    """界面锚点：设置页里必须有这两项，且调的是新端点。"""
    root = __import__("pathlib").Path(__file__).resolve().parents[2]
    ui = (root / "frontend" / "src" / "components" / "SettingsPanel.tsx").read_text("utf-8")
    assert "单任务最大步数" in ui, "设置页没有步数上限输入框"
    assert "单价" in ui and "元/百万" in ui, "设置页没有单价表"
    assert "setLimits" in ui, "没接上新端点"
    api = (root / "frontend" / "src" / "api.ts").read_text("utf-8")
    assert "/settings/limits" in api
