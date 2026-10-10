"""第 9 批（C7）：任务 id 必须防碰撞 —— 撞了会静默毁数据。

真实现状（改前）：`main.py` 的 `_new_task_id()` 是
    f"task_{datetime.now():%Y%m%d}_{secrets.token_hex(2)}"     # 每天只有 65536 种
按用户真实数据（217 个任务 / 5 天，日均 43.4、最忙一天 77）算：

    日均 43 个/天  → 碰撞概率 1.43% / 天
    最忙 77 个/天  → 4.42% / 天
    若一天 200 个  → 26.3% / 天
    按一年累积    → ≈ 99.5% 至少撞一次

撞了的后果不是"报个错"，而是**两个任务共用同一个目录**：
`events.jsonl` / `history.json` 混在一起、索引里互相覆盖 ⇒ **静默毁数据**。

修法：① 随机位加倍到 8 位 hex（42.9 亿种/天）② **仍然显式防碰撞**（撞了就重试）。
本文件把两半都钉住：格式要对、量级要够、**碰撞时必须重试而不是照发**。
"""
from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from app import main as m

ID_RE = re.compile(r"^task_\d{8}_[0-9a-f]{8}$")


def _client() -> TestClient:
    return TestClient(m.app, base_url="http://127.0.0.1:8642")


# ══════════════════ 格式与量级 ══════════════════


def test_task_id_format_is_8_hex():
    """★ 核心锚点：随机部分必须是 8 位 hex（原来是 4 位，每天只有 65536 种）。"""
    tid = m._new_task_id()
    assert ID_RE.match(tid), f"id 格式不对（应为 task_<8位日期>_<8位hex>）：{tid}"


def test_one_day_has_at_least_a_billion_ids():
    """量级锚点：**空间要足够大** —— 用"同一天能生成多少互不相同的 id"来量。

    4 位 hex 时这里只能有 65536 种；8 位 hex 是 42.9 亿种。
    取 3000 个样本，若还是 4 位 hex，按鸽巢/生日问题几乎必然出现重复
    （3000 个样本落进 65536 个格子的重复概率 ≈ 1）。
    """
    tids = [m._new_task_id() for _ in range(3000)]
    assert len(set(tids)) == len(tids), "出现重复 id（随机空间疑似仍只有 4 位 hex）"
    assert all(ID_RE.match(t) for t in tids)


# ══════════════════ 防碰撞（这一半才是关键） ══════════════════


def test_retries_when_id_already_on_disk(tmp_path, monkeypatch):
    """★ 核心锚点：**磁盘上已存在同名目录时必须换一个**，不能照发。

    做法：把随机源钉死成同一个值 → 第一次必然"撞" → 断言最终返回的不是那个被占用的 id。
    """
    from app.store import FsStore

    store = FsStore(tmp_path / "data")
    monkeypatch.setattr(m, "store", store)
    monkeypatch.setattr(m, "tasks", {})

    calls = {"n": 0}

    def fake_hex(n: int) -> str:
        calls["n"] += 1
        return "deadbeef" if calls["n"] == 1 else "cafebabe"

    monkeypatch.setattr(m.secrets, "token_hex", fake_hex)

    taken = f"task_{m.datetime.now():%Y%m%d}_deadbeef"
    (store.tasks_dir / taken).mkdir(parents=True, exist_ok=True)   # 造一个"已占用"的

    tid = m._new_task_id()
    assert tid != taken, "★ 撞了已存在的目录却照发 —— 两个任务会共用一个目录、数据混在一起"
    assert tid.endswith("cafebabe"), tid
    assert calls["n"] >= 2, "应该重试过一次"


def test_retries_when_id_already_in_index(tmp_path, monkeypatch):
    """索引里已有同 id（还没来得及建目录）也必须换 —— 否则索引里互相覆盖。"""
    from app.store import FsStore

    monkeypatch.setattr(m, "store", FsStore(tmp_path / "data"))
    taken = f"task_{m.datetime.now():%Y%m%d}_0badc0de"
    monkeypatch.setattr(m, "tasks", {taken: object()})

    calls = {"n": 0}

    def fake_hex(n: int) -> str:
        calls["n"] += 1
        return "0badc0de" if calls["n"] == 1 else "11112222"

    monkeypatch.setattr(m.secrets, "token_hex", fake_hex)
    assert m._new_task_id().endswith("11112222")


def test_gives_up_loudly_instead_of_looping_forever(tmp_path, monkeypatch):
    """一直撞就一直换？不行 —— 要有上限且**大声报错**，不能静默死循环或照发。"""
    from app.store import FsStore

    monkeypatch.setattr(m, "store", FsStore(tmp_path / "data"))
    monkeypatch.setattr(m, "tasks", {})
    monkeypatch.setattr(m.secrets, "token_hex", lambda n: "aaaaaaaa")
    taken = f"task_{m.datetime.now():%Y%m%d}_aaaaaaaa"
    (m.store.tasks_dir / taken).mkdir(parents=True, exist_ok=True)

    with pytest.raises(RuntimeError, match="连续 50 次碰撞"):
        m._new_task_id()


# ══════════════════ 老 id 不受影响 ══════════════════


def test_old_4hex_ids_still_resolve(tmp_path, monkeypatch):
    """改动只影响**新生成**的 id；老任务（4 位 hex）必须照旧能读能列。

    用户现有 217 个任务全是老格式，这条防的是"改 id 生成把老数据弄丢"。
    """
    from app.store import FsStore
    from app.schemas import TaskSummary

    store = FsStore(tmp_path / "data")
    old = "task_20261004_5227"                     # 用户真实存在的老 id 形态
    (store.tasks_dir / old).mkdir(parents=True, exist_ok=True)
    store.save_index([TaskSummary(id=old, title="老任务", created_at="2026-10-04T00:00:00Z",
                                  updated_at="2026-10-04T00:00:00Z")])
    monkeypatch.setattr(m, "store", store)
    monkeypatch.setattr(m, "tasks", {old: TaskSummary(id=old, title="老任务",
                                                      created_at="2026-10-04T00:00:00Z",
                                                      updated_at="2026-10-04T00:00:00Z")})

    with _client() as c:
        d = c.get("/api/v1/tasks").json()
    assert any(t["id"] == old for t in d), f"老格式任务列不出来：{d}"
