# -*- coding: utf-8 -*-
"""★「一键清干净」—— 2026-10-07 用户点名要的那条：

> "**一键清干净**（⚠️破坏性 ⇒ 必须二次确认 ✓）"

## 三条设计（每条都在回答"万一用户手滑了怎么办" ✓）

1. **先备份、再清** ✓ —— 不删，是**改名挪到** `data/_cleared_<时间戳>/` ✓
   （同盘瞬时完成 ✓ 而且**可回滚** ✓ —— 用户那套规矩里"每步可回滚"✓）
2. **二次确认是真确认** ✗ —— 确认词要**原样打出来** ✓（点两下按钮不算 ✓）
   **有任务在跑就拒绝** ✗（否则正干活的 Agent 脚下的文件被抽走 ✓）
3. **说不清就不清** ✓ —— 逐条列出"会清什么 / **不会**碰什么" ✓
   设置、Key 名字、已下的模型（1.8GB ✗）、授权 —— 这些删了要重配重下 ✓

## 本文件还钉住一个**真踩到的**坑（第 ④ 组）

清完**必须把目录骨架建回来** ✗ —— 否则 `_save_index()` 写 `tasks/index.json` 时目录已经没了
⇒ `FileNotFoundError` ⇒ **清完当场 500** ✓
（本班在隔离目录里真跑那条链时抓到的 ✓ **光看代码看不出来** ✓）
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import main as m

PHRASE = "清空我的数据"


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    """★ 造一个**独立**的 data 目录（**绝不碰用户真数据** ✓ —— 本仓的硬规矩 ✓）。"""
    data = tmp_path / "data"
    (data / "tasks").mkdir(parents=True)
    (data / "memory").mkdir(parents=True)
    (data / "kb").mkdir(parents=True)
    (data / "asr_models").mkdir(parents=True)
    (data / "tasks" / "index.json").write_text("[]", "utf-8")
    (data / "tasks" / "junk.bin").write_text("任务产物", "utf-8")
    (data / "memory" / "memory.json").write_text("{}", "utf-8")
    (data / "asr_models" / "big.onnx").write_text("假装是 1.8G 的模型", "utf-8")
    (data / "license.json").write_text("{}", "utf-8")
    monkeypatch.setattr(m, "_DATA_DIR", data, raising=False)
    monkeypatch.setattr(m, "store", m.store.__class__(data), raising=False)
    monkeypatch.setattr(m, "tasks", {}, raising=False)
    monkeypatch.setattr(m, "runs", {}, raising=False)
    return data


def test_preview_lists_what_goes_and_what_stays(sandbox):
    """★ 清**之前**就得说清楚 ✓ —— 尤其"**不会**碰什么" ✓（那是用户最该知道、最容易误以为的一句 ✓）。"""
    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        d = c.get("/api/v1/data/clear").json()
    labels = {i["key"]: i for i in d["items"]}
    assert labels["tasks"]["exists"] is True and labels["tasks"]["bytes"] > 0
    keeps = " ".join(d["keeps"])
    for must in ("config.json", "访问密码", "ASR 模型", "授权"):
        assert must in keeps, f"没写清「不会碰」什么 ✗（缺 {must}）：{keeps}"
    assert d["phrase"] == PHRASE, "确认词没给界面 ✗（界面要显示给用户照打 ✓）"


def test_wrong_confirmation_is_refused(sandbox):
    """★★ **二次确认是真确认** ✗ —— 没打对确认词，一个字节都不许动 ✓。

    （前后空白会被 strip 掉 ✓ 那是看不见的输入噪声 ✓ 不算"打对了"之外的东西 ✓；
      但**内容**必须原样 ✓ —— 少一个字、换个说法都不行 ✓）
    """
    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        for bad in ("", "清空", "清空我的数据吧", "清空我的数据 谢谢", "随便打的", "DELETE"):
            r = c.post("/api/v1/data/clear", json={"confirm": bad, "parts": ["tasks"]})
            assert r.status_code == 422, f"「{bad}」竟然通过了 ✗"
    assert (sandbox / "tasks" / "junk.bin").exists(), "被拒了却动了文件 ✗✗"


def test_running_task_blocks_the_clear(sandbox):
    """★ 有任务在跑 ⇒ 拒绝清 ✗（不然正干活的 Agent 脚下的文件被抽走 ✓）。"""
    fake_run = SimpleNamespace(aio_task=SimpleNamespace(done=lambda: False))
    m.runs["task_running"] = fake_run                      # type: ignore[index]
    try:
        with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
            r = c.post("/api/v1/data/clear", json={"confirm": PHRASE, "parts": ["tasks"]})
        assert r.status_code == 409, r.text
        assert (sandbox / "tasks" / "junk.bin").exists(), "被拒了却动了文件 ✗"
    finally:
        m.runs.pop("task_running", None)


def test_clear_moves_not_deletes_and_keeps_the_important_stuff(sandbox):
    """★★ **先备份再清** ✓ + **不碰设置/模型/授权** ✓ —— 这条是本功能的安全底线 ✓。"""
    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        r = c.post("/api/v1/data/clear", json={"confirm": PHRASE, "parts": ["tasks", "memory"]})
        assert r.status_code == 200, r.text
        d = r.json()
    bak = __import__("pathlib").Path(d["backup"])
    assert bak.is_dir(), f"没有备份目录 ✗：{d}"
    assert (bak / "tasks" / "junk.bin").exists(), "原件没被挪进备份 ⇒ 清 = 真删了 ✗（不可回滚 ✗）"
    assert (bak / "memory" / "memory.json").exists()
    # **明确不碰**的那些：一个都不能少 ✓
    assert (sandbox / "asr_models" / "big.onnx").exists(), "把已下的模型删了 ✗（要重下几个 G ✓）"
    assert (sandbox / "license.json").exists(), "把授权文件删了 ✗"
    assert (sandbox / "kb").exists(), "没选中的那类被顺手清了 ✗"
    assert "没动" in d["note"], d["note"]


def test_directories_are_rebuilt_so_the_store_still_works(sandbox):
    """★★ 本班真踩到的坑：清完不建回目录骨架 ⇒ `_save_index()` 当场 `FileNotFoundError` ✗✗
    （**清完就 500** ✓ 而且用户会以为"清坏掉了" ✓）。

    回滚实验：把 `mkdir(parents=True, exist_ok=True)` 那段去掉 ⇒ 本组必红 ✓
    """
    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        r = c.post("/api/v1/data/clear", json={"confirm": PHRASE, "parts": ["tasks", "memory"]})
        assert r.status_code == 200, r.text
        assert (sandbox / "tasks").is_dir(), "任务目录没建回来 ⇒ 下一次写索引就炸 ✗"
        # 真写一次：清完之后这套东西还得能正常用 ✓
        t = c.post("/api/v1/tasks", json={"input": "清完之后再建一个任务"}).json()
        assert t.get("id", "").startswith("task_"), t
        assert (sandbox / "tasks" / "index.json").exists(), "清完写不了索引 ✗"


def test_memory_is_reset_in_memory_too(sandbox):
    """★ 文件清了、**内存里那份**也得清 ✓ —— 否则界面照旧显示，
    而且下一次保存会把文件**又写回来** ✗（"看着清了其实没清"✓）。"""
    from app.schemas import TaskSummary
    m.tasks["task_20261007_ghost"] = TaskSummary(          # type: ignore[index]
        id="task_20261007_ghost", title="幽灵任务",
        created_at="2026-10-07T00:00:00Z", updated_at="2026-10-07T00:00:00Z")
    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        d = c.post("/api/v1/data/clear", json={"confirm": PHRASE, "parts": ["tasks"]}).json()
    assert m.tasks == {}, "内存里的任务没清 ⇒ 界面照旧显示 ✗"
    assert "任务索引" in d["memory_reset"], d


def test_partial_clear_only_touches_what_you_picked(sandbox):
    """★ 只清选中的 ✓ —— 没选的那几类**一个字节都不许动** ✗。"""
    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        d = c.post("/api/v1/data/clear", json={"confirm": PHRASE, "parts": ["memory"]}).json()
    assert d["cleared"] == ["memory"], d
    assert (sandbox / "tasks" / "junk.bin").exists(), "没选任务，任务却被清了 ✗"
    assert not (sandbox / "memory").exists() or not any((sandbox / "memory").iterdir())


def test_backup_keeps_original_bytes(sandbox):
    """★ 备份是**原件**（不是"另存一份空的"✓）—— 真要找回时得能拿回来 ✓。"""
    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        d = c.post("/api/v1/data/clear", json={"confirm": PHRASE, "parts": ["tasks", "memory"]}).json()
    bak = __import__("pathlib").Path(d["backup"])
    assert (bak / "tasks" / "junk.bin").read_text("utf-8") == "任务产物"
    assert json.loads((bak / "tasks" / "index.json").read_text("utf-8")) == []
