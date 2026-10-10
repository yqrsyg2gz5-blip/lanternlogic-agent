# -*- coding: utf-8 -*-
"""记忆库**专属测试** —— 2026-10-07 功能体检补的（此前 **0 个专属测试** ✗）。

## 为什么补

用户原话："你把这个有 7 块确实没有专属测试的这个，然后给测试一下，
因为这些东西你不测试的话，你没法去去开放"

记忆库是"**越用越懂你**"的地基 ✓ 它的**价值全在两条集成路径上** ✗✓：
  · **写**：任务交付后自动提取（`main._extract_memory` ✓）
  · **读**：新任务开始时注入系统提示（`_memory_block` ✓ 由 loop 塞进 history ✓）
所以这里的测试**不只测存储类** ✓ 还要测**那两条路** ✓ ——
光测 `add/list` 是测不出"它到底会不会记住你"的 ✗。

## 体检结论（这次读代码核过 ✓）

· 开关**确实生效** ✓（`main.py:293` 注入前判 ✓ `main.py:302` 提取前判 ✓）—— 我起初怀疑它没判 ✗ 核对后**是我错了** ✓
· 群任务**不写全局记忆** ✓（员工人设/群内格式是临时身份 ✓ 不该污染"你是谁" ✓）
· 坏文件会**改名保留** ✓（不基于空列表覆写 ⇒ 历史不丢 ✓）
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app import main as m  # noqa: E402
from app.memory import MemoryStore, parse_extraction  # noqa: E402


@pytest.fixture()
def store(tmp_path):
    return MemoryStore(tmp_path / "memory.json", max_entries=5)


# ═══ ① 写：去重 / 上限 / 打码 ═══

def test_add_appends_and_returns_the_real_count(store):
    n = store.add([{"type": "preference", "content": "喜欢简洁回复"}], "task_1")
    assert n == 1
    got = store.all()
    assert got[0]["content"] == "喜欢简洁回复"
    assert got[0]["type"] == "preference"
    assert got[0]["source"] == "task_1", "没记下这条记忆来自哪个任务 ✗（用户要能追溯 ✓）"
    assert got[0]["ts"].endswith("Z"), f"时间戳格式不对：{got[0]['ts']}"


def test_add_dedupes_by_content(store):
    store.add([{"type": "fact", "content": "用 Windows"}], "t1")
    again = store.add([{"type": "fact", "content": "用 Windows"}], "t2")
    assert again == 0, "同样的话记了两遍 ✗（记忆库会越用越臃肿 ✓）"
    assert len(store.all()) == 1


def test_add_skips_empty_content(store):
    assert store.add([{"type": "fact", "content": "   "}, {"type": "fact"}], "t") == 0
    assert store.all() == []


def test_add_keeps_only_the_newest_within_the_cap(store):
    """**上限到了淘汰最旧的** ✓（FIFO ✓ 防无限膨胀 ✓）。"""
    for i in range(8):
        store.add([{"type": "fact", "content": f"第 {i} 条"}], "t")
    kept = [e["content"] for e in store.all()]
    assert len(kept) == 5, kept
    assert kept == ["第 3 条", "第 4 条", "第 5 条", "第 6 条", "第 7 条"], kept


def test_add_redacts_secrets_before_saving(store):
    """★ **写入前必须过打码** ✓ —— 密钥绝不能进记忆 ✓（它会**每次任务都注入提示词** ✗✗）。"""
    store.add([{"type": "fact", "content": "我的 key 是 sk-ABCDEFGHIJKLMNOPQRSTUVWX"}], "t")
    saved = store.all()[0]["content"]
    assert "sk-ABCDEFGHIJKLMNOPQRSTUVWX" not in saved, f"密钥写进记忆了 ✗：{saved}"
    # 而且**文件里**也不能有 ✓（别只改内存 ✓）
    raw = store.path.read_text("utf-8")
    assert "sk-ABCDEFGHIJKLMNOPQRSTUVWX" not in raw, "落盘文件里还有明文密钥 ✗"


def test_add_truncates_a_very_long_memory(store):
    store.add([{"type": "fact", "content": "长" * 999}], "t")
    assert len(store.all()[0]["content"]) <= 200, "单条记忆没截断 ✗（会把提示词撑爆 ✓）"


def test_clear_returns_how_many_were_removed(store):
    store.add([{"type": "fact", "content": "a"}], "t")
    store.add([{"type": "fact", "content": "b"}], "t")
    assert store.clear() == 2
    assert store.all() == []


# ═══ ② 读：注入提示词的那一块 ═══

def test_format_for_prompt_is_empty_when_there_is_nothing(store):
    assert store.format_for_prompt() == ""


def test_format_for_prompt_prefers_the_newest_and_keeps_chronological_order(store):
    for i in range(4):
        store.add([{"type": "fact", "content": f"事实{i}"}], "t")
    block = store.format_for_prompt(max_chars=60)
    assert block.startswith("[长期记忆"), block
    body = block.split("\n", 1)[1].splitlines()
    # 预算内取**最新**的几条 ✓ 但块内按**时间正序**排 ✓（读起来自然 ✓）
    assert any("事实3" in ln for ln in body), f"最新的没进来 ✗：{body}"
    idx = [next((i for i, ln in enumerate(body) if f"事实{k}" in ln), None) for k in range(4)]
    present = [i for i in idx if i is not None]
    assert present == sorted(present), f"块内顺序乱了 ✗：{body}"


def test_format_for_prompt_respects_the_char_budget(store):
    for i in range(5):
        store.add([{"type": "fact", "content": "很长的记忆内容" * 10}], "t")
    block = store.format_for_prompt(max_chars=200)
    assert len(block) <= 200 + 60, f"超出预算太多（记忆注入会吃掉缓存前缀）✗：{len(block)}"


# ═══ ③ 坏文件保护 ═══

def test_corrupted_memory_file_is_preserved_not_overwritten(store):
    """★ 坏文件**改名保留** ✓ —— 否则下次 `_save` 会基于空列表覆写，**历史记忆被永久抹掉** ✗。"""
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text("{ 这不是合法 JSON", "utf-8")
    assert store.all() == []
    kept = list(store.path.parent.glob("memory.corrupt-*"))
    assert kept, "坏文件被直接丢了 ✗（历史记忆就找不回来了 ✓）"
    assert "这不是合法 JSON" in kept[0].read_text("utf-8"), "保留的备份内容不对 ✗"


def test_non_list_json_is_treated_as_empty(store):
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text('{"a": 1}', "utf-8")
    assert store.all() == []


# ═══ ④ 提取器输出的解析（模型爱加围栏/废话 ✓）═══

def test_parse_extraction_handles_code_fences_and_chatter():
    raw = '好的，这是结果：\n```json\n[{"type":"preference","content":"只要代码"}]\n```\n完毕'
    got = parse_extraction(raw)
    assert got == [{"type": "preference", "content": "只要代码"}], got


def test_parse_extraction_survives_junk():
    assert parse_extraction("") == []
    assert parse_extraction("没有值得记的") == []
    assert parse_extraction("[不是json") == []
    assert parse_extraction('["字符串不算"]') == []
    assert parse_extraction('[{"content":"   "}]') == []
    assert parse_extraction('[{"type":"fact","content":"ok"}]') == [{"type": "fact", "content": "ok"}]


def test_parse_extraction_caps_at_five_and_defaults_the_type():
    raw = json.dumps([{"content": f"第{i}条"} for i in range(9)], ensure_ascii=False)
    got = parse_extraction(raw)
    assert len(got) == 5, f"没夹到 5 条 ✗：{len(got)}"
    assert all(it["type"] == "fact" for it in got), "没给 type 时应默认 fact ✗"


# ═══ ⑤ 真集成：交付后自动提取（这才是"越用越懂你" ✓）═══

class _FakeTurn:
    def __init__(self, text):
        self.text = text


class _FakeProvider:
    def __init__(self, text):
        self.text = text
        self.seen: list[str] = []

    async def next_turn(self, _sys, msgs, _tools):        # noqa: ANN001
        self.seen.append(msgs[0]["content"])
        return _FakeTurn(self.text)


def _seed_task(task_id: str, user_text: str, reply: str) -> None:
    """往真 store 里写一个最小任务（`_extract_memory` 就是从**事件流**里读的 ✓）。

    ★ 照 `FsStore` 的真实 API 写 ✓（第一版我凭印象写成 `create_task` ✗ 它没有这个方法 ✓
      —— 正好说明"凭印象写测试"会立刻被真实接口打脸 ✓ 这也是体检的价值 ✓）。
    """
    from app.schemas import EventEnvelope
    for seq, (role, text) in enumerate((("user", user_text), ("assistant", reply)), start=1):
        m.store.append_event(EventEnvelope(
            id=f"evt_{seq:06d}", seq=seq, task_id=task_id, type="message",
            ts="2026-10-07T00:00:00Z", payload={"role": role, "text": text}))


def test_extract_memory_saves_what_the_model_returns(tmp_path, monkeypatch):
    mem = MemoryStore(tmp_path / "memory.json")
    monkeypatch.setattr(m, "_memory_store", mem)
    tid = "task_20261007_a001"
    _seed_task(tid, "以后回复简短点", "好的")
    prov = _FakeProvider('[{"type":"preference","content":"用户喜欢简短回复"}]')
    asyncio.run(m._extract_memory(tid, prov))
    got = [e["content"] for e in mem.all()]
    assert got == ["用户喜欢简短回复"], got
    assert mem.all()[0]["source"] == tid, "没记来源任务 ✗"
    assert "以后回复简短点" in prov.seen[0], "提取提示里没带上任务输入 ✗"


def test_extract_memory_skips_group_tasks(monkeypatch, tmp_path):
    """★ **群任务不进全局记忆** ✓ —— 员工人设与群内格式是**临时身份** ✓
    写进全局记忆会污染"你是谁"✗（下次你单聊它，它以为自己是"评测乙"✗）。"""
    mem = MemoryStore(tmp_path / "memory.json")
    monkeypatch.setattr(m, "_memory_store", mem)
    tid = "task_20261007_a002"
    _seed_task(tid, "【群任务·来自员工 评测乙】写个报告", "写完了")
    prov = _FakeProvider('[{"type":"fact","content":"不该被记住"}]')
    asyncio.run(m._extract_memory(tid, prov))
    assert mem.all() == [], "群任务被写进全局记忆了 ✗"
    assert prov.seen == [], "群任务根本不该去调提取器 ✗（白花钱 ✓）"


def test_extract_memory_never_breaks_the_task(monkeypatch, tmp_path):
    """★ **记忆是增强、不是依赖** ✓ —— 提取炸了不能影响任务本身 ✓。"""
    mem = MemoryStore(tmp_path / "memory.json")
    monkeypatch.setattr(m, "_memory_store", mem)
    tid = "task_20261007_a003"
    _seed_task(tid, "随便", "随便")

    class _Boom:
        async def next_turn(self, *a, **kw):
            raise RuntimeError("模型挂了")

    asyncio.run(m._extract_memory(tid, _Boom()))          # 不该抛 ✗
    assert mem.all() == []


def test_extract_memory_redacts_again_before_saving(monkeypatch, tmp_path):
    """★ 提取器**复述**了密钥也要拦在落盘前 ✓（纵深防御 ✓ 两道 ✓）。"""
    mem = MemoryStore(tmp_path / "memory.json")
    monkeypatch.setattr(m, "_memory_store", mem)
    tid = "task_20261007_a004"
    _seed_task(tid, "记一下", "好")
    prov = _FakeProvider('[{"type":"fact","content":"他的 key 是 sk-ABCDEFGHIJKLMNOPQRSTUVWX"}]')
    asyncio.run(m._extract_memory(tid, prov))
    joined = json.dumps(mem.all(), ensure_ascii=False)
    assert "sk-ABCDEFGHIJKLMNOPQRSTUVWX" not in joined, f"密钥进了记忆 ✗：{joined}"
