# -*- coding: utf-8 -*-
"""知识库**专属测试** —— 2026-10-07 功能体检补的（此前 **0 个专属测试** ✗）。

## 为什么补（用户原话）

> "你把这个有 7 块确实没有专属测试的这个，然后给测试一下，
>  因为这些东西你不测试的话，你没法去去开放"

知识库是"**越用越博学**"的地基 ✓ 而它此前**没有任何专属测试** ✗ ——
只有零散的关键词命中（审批矩阵 / 打码 ✓）✓ 覆盖不到它自己的逻辑 ✓。

## 体检时读出来的真隐患（已修 ✓ 有测试钉住）

`embed_texts` 原来**不校验返回条数** ✗ —— 而 `ingest_folder` 是
`zip(texts, embeddings)` **按顺序配对**的 ✓ ⇒ 服务端少返一条 ✓
后面所有块的**文本与向量整体错位** ✗✓ 且**毫无报错** ✓
⇒ 表现为"检索结果张冠李戴"✗（问 A 文档、捞出 B 文档的句子 ✓）而**查不出来** ✓。
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app import kb as K  # noqa: E402


def run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def store(tmp_path, monkeypatch):
    """把向量化换成**假**的（不花钱、不联网 ✓）—— 向量做成"按文本可预测"的 ✓ 便于断言排序 ✓。"""
    def fake_embed(texts: list[str]) -> list[list[float]]:
        # 词表：把文本映射成一个可比较的小向量（不同词 → 不同方向 ✓）
        vocab = ["苹果", "香蕉", "汽车", "轮胎"]
        out = []
        for t in texts:
            v = [float(t.count(w)) for w in vocab] or [0.0, 0.0, 0.0, 0.0]
            out.append(v)
        return out

    async def _fake(texts: list[str]):
        return fake_embed(texts)

    monkeypatch.setattr(K, "embed_texts", _fake)
    return K.KBStore(tmp_path / "kb")


# ═══ ① 切块 ═══

def test_chunk_merges_paragraphs_up_to_the_target_size():
    text = "\n\n".join(["A" * 300, "B" * 300, "C" * 300])
    chunks = K.chunk_text(text, size=700)
    assert len(chunks) == 2, f"700 上限下 3 段共 900 字应切成 2 块，实际 {len(chunks)}"
    assert all(len(c) <= 700 + 2 for c in chunks), [len(c) for c in chunks]


def test_chunk_hard_splits_a_single_huge_paragraph():
    """**单段超长必须硬切** ✓ —— 否则一块 10 万字会直接撑爆向量接口的输入上限 ✗。"""
    chunks = K.chunk_text("X" * 2500, size=700)
    assert len(chunks) == 4, chunks and [len(c) for c in chunks]
    assert "".join(chunks) == "X" * 2500, "硬切不该丢字 ✗"


def test_chunk_ignores_blank_input():
    assert K.chunk_text("") == []
    assert K.chunk_text("\n\n   \n\n") == []


# ═══ ② 入库 ═══

def test_ingest_reads_text_files_and_records_sources(store, tmp_path):
    src = tmp_path / "docs"
    src.mkdir()
    (src / "a.md").write_text("# 标题\n\n" + "苹果" * 40, "utf-8")
    (src / "b.txt").write_text("香蕉" * 40, "utf-8")
    (src / "c.png").write_bytes(b"\x89PNG")            # 非文本 → 跳过 ✓
    (src / "sub").mkdir()
    (src / "sub" / "d.py").write_text("轮胎 = 1\n" + "# " + "轮胎" * 30, "utf-8")   # 子目录也要收 ✓

    man = run(store.ingest_folder("测试库", src))
    assert man["name"] == "测试库" and man["chunks"] >= 3, man
    assert man["files"] == 3, f"应收到 3 个文本文件（png 跳过），实际 {man['files']}"
    chunks = store._load_chunks("测试库")
    sources = {c["source"] for c in chunks}
    assert sources == {"a.md", "b.txt", "sub/d.py"}, sources
    assert all(c["embedding"] for c in chunks), "有块没拿到向量 ✗"


def test_ingest_drops_tiny_files_silently(store, tmp_path):
    """★ **碎屑过滤**：短于 30 字的块不入库 ✓ —— 这是个**有意的取舍** ✓ 但它会让
    `manifest.files` 比实际文本文件数**少** ✗（用户可能疑惑"我明明放了 3 个文件"✗）。
    这条测试把这个行为**写下来** ✓（将来要改就改这条 ✓ 而不是当成 bug 到处找 ✓）。
    """
    src = tmp_path / "d"; src.mkdir()
    (src / "big.md").write_text("苹果" * 60, "utf-8")
    (src / "tiny.txt").write_text("太短了", "utf-8")          # 3 字 → 被过滤 ✓
    man = run(store.ingest_folder("碎屑库", src))
    assert man["files"] == 1, f"碎屑文件不该入库，实际 files={man['files']}"


def test_ingest_rejects_empty_or_non_text_folder(store, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ValueError, match="没有可入库"):
        run(store.ingest_folder("空库", empty))
    with pytest.raises(ValueError, match="文件夹不存在"):
        run(store.ingest_folder("没有的", tmp_path / "nope"))


def test_ingest_same_name_overwrites_and_clears_the_cache(store, tmp_path):
    """**同名覆盖** ✓ —— 而且必须让检索缓存失效 ✗（否则问的是新库、捞的是旧块 ✓）。"""
    d1 = tmp_path / "v1"; d1.mkdir(); (d1 / "x.md").write_text("苹果" * 60, "utf-8")
    run(store.ingest_folder("同一个库", d1))
    first = store._load_chunks("同一个库")
    assert len(first) >= 1

    d2 = tmp_path / "v2"; d2.mkdir(); (d2 / "y.md").write_text("汽车" * 60, "utf-8")
    run(store.ingest_folder("同一个库", d2))
    again = store._load_chunks("同一个库")
    assert {c["source"] for c in again} == {"y.md"}, "覆盖后还留着旧库的内容 ✗"


def test_ingest_failure_does_not_corrupt_the_existing_library(store, tmp_path, monkeypatch):
    """**入库失败不能毁掉已有库** ✓ —— 向量化中途出错时，旧库必须原样还在 ✓。"""
    good = tmp_path / "good"; good.mkdir(); (good / "a.md").write_text("苹果" * 60, "utf-8")
    run(store.ingest_folder("别毁我", good))
    before = store._load_chunks("别毁我")

    async def _boom(texts):
        raise RuntimeError("假装向量服务挂了")

    monkeypatch.setattr(K, "embed_texts", _boom)
    bad = tmp_path / "bad"; bad.mkdir(); (bad / "b.md").write_text("香蕉" * 60, "utf-8")
    with pytest.raises(RuntimeError, match="假装向量服务挂了"):
        run(store.ingest_folder("别毁我", bad))
    assert store._load_chunks("别毁我") == before, "入库失败把旧库写坏了 ✗"


# ═══ ③ 检索 ═══

def test_search_ranks_by_similarity_and_keeps_sources(store, tmp_path):
    src = tmp_path / "d"; src.mkdir()
    (src / "fruit.md").write_text("苹果 苹果 苹果\n\n" + "苹果" * 30, "utf-8")
    (src / "car.md").write_text("汽车 轮胎 轮胎\n\n" + "汽车" * 30, "utf-8")
    run(store.ingest_folder("混库", src))

    hits = run(store.search("苹果", top_k=5))
    assert hits, "检索空 ✗"
    assert hits[0]["source"] == "fruit.md", f"最相关的没排第一 ✗：{hits[0]}"
    assert hits[0]["score"] >= hits[-1]["score"], "分数没排序 ✗"
    assert hits[0]["kb"] == "混库" and "苹果" in hits[0]["text"]


def test_search_top_k_is_clamped(store, tmp_path):
    src = tmp_path / "d"; src.mkdir()
    for i in range(15):
        (src / f"f{i}.md").write_text(f"苹果 {i}\n\n" + "苹果" * 30, "utf-8")
    run(store.ingest_folder("大库", src))
    assert len(run(store.search("苹果", top_k=999))) <= 10, "top_k 没夹到 10 ✗"
    assert len(run(store.search("苹果", top_k=0))) == 1, "top_k=0 至少给 1 条 ✗"


def test_search_without_any_library_says_what_to_do(store):
    with pytest.raises(RuntimeError, match="还没有已安装的知识库"):
        run(store.search("随便问"))


def test_search_skips_a_corrupted_library_instead_of_crashing(store, tmp_path):
    """**一个库的 chunks.json 坏了，不能带着所有库一起死** ✓。"""
    src = tmp_path / "d"; src.mkdir(); (src / "a.md").write_text("苹果" * 60, "utf-8")
    run(store.ingest_folder("好库", src))
    bad = store.root / "坏库"; bad.mkdir(parents=True)
    (bad / "manifest.json").write_text(json.dumps({"name": "坏库", "chunks": 1}), "utf-8")
    (bad / "chunks.json").write_text("{ 这不是合法 json", "utf-8")
    hits = run(store.search("苹果", top_k=5))
    assert hits and hits[0]["kb"] == "好库", "坏库把检索整个搞死了 ✗"


def test_search_output_is_sanitized_and_redacted(store, tmp_path):
    """**入库与出库都要过清洗/打码管线** ✓（docstring 声称的"清洗闸"必须真存在 ✓）。"""
    src = tmp_path / "d"; src.mkdir()
    (src / "leak.md").write_text("苹果\u200b秘密 sk-ABCDEFGHIJKLMNOPQRSTUVWX\n\n" + "苹果" * 30, "utf-8")
    run(store.ingest_folder("打码库", src))
    hits = run(store.search("苹果", top_k=5))
    joined = " ".join(h["text"] for h in hits)
    assert "\u200b" not in joined, "不可见控制字符没被剥掉 ✗"
    assert "sk-ABCDEFGHIJKLMNOPQRSTUVWX" not in joined, "密钥没被打码 ✗"


@pytest.fixture()
def cloud(monkeypatch):
    """把档位**明确钉到云端** ✓ —— 因为默认已经改成"本地"了 ✓（用户 2026-10-07 拍板 ✓）
    ⇒ 测阿里那条路时必须显式指定 ✗ 否则测的其实是本地路 ✓（那正是下面 `test_kb_local_embed.py` 的活 ✓）。
    """
    from app import config as C
    monkeypatch.setattr(C, "load_config", lambda *a, **kw: type("X", (), {
        "kb": type("K", (), {"embedder": "dashscope", "local_model": ""})()})())


# ═══ ④ 向量化契约（体检时发现的真隐患 ✓）═══

def test_embed_texts_refuses_a_short_response(monkeypatch, cloud):
    """★★ **服务端少返一条就必须报错** ✓ —— 这条是本次体检读出来的隐患 ✓。

    真事：原来直接 `extend` 不校验条数 ✗ ⇒ `zip(texts, embeddings)` 把后面所有块
    **文本与向量整体错位** ✗✓ 而且**没有任何报错** ✓ ⇒ 检索结果张冠李戴且查不出来 ✓。
    """
    import httpx
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test")

    def handler(req: httpx.Request) -> httpx.Response:
        n = len(json.loads(req.content)["input"])
        # 故意少返一条（模拟截断/限流/某条被拒）
        return httpx.Response(200, json={"data": [{"index": i, "embedding": [1.0, 0.0]}
                                                  for i in range(max(0, n - 1))]})

    real = httpx.AsyncClient

    class _Fake(real):                       # type: ignore[misc,valid-type]
        def __init__(self, *a, **kw):
            kw["transport"] = httpx.MockTransport(handler)
            super().__init__(*a, **kw)

    monkeypatch.setattr(K.httpx, "AsyncClient", _Fake)
    with pytest.raises(RuntimeError, match="条数不对"):
        run(K.embed_texts(["a", "b", "c"]))


def test_embed_texts_without_key_says_how_to_fix(monkeypatch, cloud):
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="DASHSCOPE_API_KEY"):
        run(K.embed_texts(["a"]))


def test_embed_texts_batches_and_keeps_order(monkeypatch, cloud):
    """一批 10 条 ✓ 多批要按顺序拼起来 ✓（顺序错了同样会张冠李戴 ✗）。"""
    import httpx
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test")
    seen: list[int] = []

    def handler(req: httpx.Request) -> httpx.Response:
        batch = json.loads(req.content)["input"]
        seen.append(len(batch))
        return httpx.Response(200, json={"data": [{"index": i, "embedding": [float(len(t))]}
                                                  for i, t in enumerate(batch)]})

    real = httpx.AsyncClient

    class _Fake(real):                       # type: ignore[misc,valid-type]
        def __init__(self, *a, **kw):
            kw["transport"] = httpx.MockTransport(handler)
            super().__init__(*a, **kw)

    monkeypatch.setattr(K.httpx, "AsyncClient", _Fake)
    texts = [f"t{i}" for i in range(23)]                 # 10 + 10 + 3
    out = run(K.embed_texts(texts))
    assert seen == [10, 10, 3], f"分批不对：{seen}"
    assert len(out) == 23, "条数不对 ✗"
    assert [v[0] for v in out] == [float(len(t)) for t in texts], "顺序错了会张冠李戴 ✗"


# ═══ ⑤ 库管理 ═══

def test_delete_removes_the_library_and_its_cache(store, tmp_path):
    src = tmp_path / "d"; src.mkdir(); (src / "a.md").write_text("苹果" * 60, "utf-8")
    run(store.ingest_folder("要删的", src))
    assert store.get("要删的") is not None
    assert store.delete("要删的") is True
    assert store.get("要删的") is None
    assert store._load_chunks("要删的") == [], "删了还从缓存里捞得到 ✗"
    assert store.delete("要删的") is False, "删不存在的库该返回 False ✗"


def test_cache_invalidates_when_the_file_changes(store, tmp_path):
    """mtime 变了必须重载 ✓（否则用户重新入库后，检索还在用旧块 ✗）。"""
    src = tmp_path / "d"; src.mkdir(); (src / "a.md").write_text("苹果" * 60, "utf-8")
    run(store.ingest_folder("缓存库", src))
    first = store._load_chunks("缓存库")
    (store.root / "缓存库" / "chunks.json").write_text("[]", "utf-8")
    import os
    os.utime(store.root / "缓存库" / "chunks.json", (1, 1))     # 强制改 mtime ✓
    assert store._load_chunks("缓存库") == [], "mtime 变了却没重载 ✗"
    assert first, "第一次读就不该是空的 ✗"


def test_cache_invalidates_even_when_mtime_does_not_change(store, tmp_path):
    """★★ **mtime 没变但文件变了，也要重载** ✓ —— 这条是体检时**测试自己抓出来的真 bug** ✗✗。

    真事：缓存原来**只比 mtime** ✗ ⇒ 在同一个时间粒度内重新入库同名知识库 ✓
    会命中旧缓存 ⇒ 用户"明明重新入库了，检索还在用老内容"✗ 而且**毫无提示** ✓。
    （它平时看不出来 ✓ 只在跑得快的时候偶发 ✓ —— 最容易活到线上的那类 ✓。）

    现在缓存键是 **(mtime, size)** ✓ ⇒ 内容一变大小基本必变 ✓ 就会重载 ✓。
    这条测试故意**只改内容、把 mtime 改回去** ✓ 精确复现那个场景 ✓。
    """
    import os
    src = tmp_path / "d"; src.mkdir(); (src / "a.md").write_text("苹果" * 60, "utf-8")
    run(store.ingest_folder("同刻库", src))
    f = store.root / "同刻库" / "chunks.json"
    before_stat = f.stat()
    assert store._load_chunks("同刻库"), "第一次读就不该是空的 ✗"

    # 换掉内容，然后把 mtime **改回原来那一刻**（模拟"同一时间粒度内重新入库"）
    f.write_text(json.dumps([{"text": "换过的内容", "source": "b.md", "embedding": [1.0]}],
                            ensure_ascii=False), "utf-8")
    os.utime(f, (before_stat.st_atime, before_stat.st_mtime))
    now = store._load_chunks("同刻库")
    assert [c["text"] for c in now] == ["换过的内容"], \
        f"mtime 没变就读了旧缓存 ⇒ 用户重新入库后检索还用老内容 ✗：{now}"
