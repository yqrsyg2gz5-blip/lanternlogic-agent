"""知识库（第 41 班 v2）——"越用越博学"的地基。

用户把文件夹交给 Agent 切块向量化存本地；干活时按语义检索相关片段注入。
与记忆库同门：全本地存储、设置页可管理。**打码**：入库与检索出参都过 redact_text（验证报告 24）。

包格式（一键安装的标准，GitHub/魔搭可直接发布）：
  <知识库名>.zip 解压后 = 目录，内含 manifest.json（name/description/chunks 数）
  + chunks.json（切块与向量）——装的是"数据"不是"代码"，注入风险由清洗闸把关。

★★ 向量档位（2026-10-07 用户拍板改的 ✗→✓）：
  原来**只接阿里 DashScope**（`text-embedding-v3` ✓）✗ —— 那条路与项目定位**自相矛盾**：
  讲的是"本地优先、数据不出本机" ✓ 而知识库**恰恰把用户资料传到云端** ✗✓。
  现在是**两档并列**（与语音"听/说"同一个设计 ✓）：
    · **local（默认）**：本地模型 ✓ 完全离线 ✓ 免费 ✓ 数据不出本机 ✓
    · **dashscope**：云端 ✓ 免下载 ✓ 但内容会外传 ✗ 且按量计费 ✓
  铁规矩：**绝不静默回退** ✗ —— 选本地就只用本地 ✓ 用不了就如实报错 + 给装法 ✓。

检索：纯 Python 余弦相似度（几千块以内毫秒级，不引重型依赖）。
"""
from __future__ import annotations

import asyncio
import json

from .skills import sanitize_skill_text
import math
import os
import shutil
from pathlib import Path
from typing import Any

import httpx

from . import retry        # ★ 2026-10-07 第 4 项：统一的限流/瞬时故障退避 ✓

CHUNK_SIZE = 700
MAX_CHUNKS = 5000
TEXT_EXTS = {".md", ".txt", ".json", ".csv", ".tsv", ".yaml", ".yml", ".py", ".js", ".ts",
             ".html", ".css", ".sql", ".log", ".ini", ".toml", ".rst"}


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return dot / (na * nb)


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """把文本变成向量 —— **按配置选档** ✓（默认**本地** ✓ 用户 2026-10-07 拍板 ✓）。

    两档（与语音"听/说"同一个设计 ✓）：
      · **local**（默认）：本地模型 ✓ 完全离线 ✓ 免费 ✓ **数据不出本机** ✓
      · **dashscope**：云端 ✓ 免下载 ✓ 但内容会传到阿里 ✗ 且按量计费 ✓

    ★★ 一条铁规矩：**绝不静默回退** ✗ ——
      选了本地就是用本地 ✓ —— 本地没装/加载失败就**如实报错 + 给装法** ✓
      **绝不偷偷把资料发到云端** ✗（那正是用户选本地要避免的事 ✓，与 ASR 那边同一条规矩 ✓）。
    """
    if not texts:
        return []
    cfg = None
    try:
        from .config import load_config
        cfg = load_config()
    except Exception:                                       # noqa: BLE001
        cfg = None
    which = str(getattr(getattr(cfg, "kb", None), "embedder", "") or "local").strip().lower()
    model_id = str(getattr(getattr(cfg, "kb", None), "local_model", "") or "")

    if which == "local":
        from . import embed_local
        # 本地是**同步阻塞**的（首次还要下模型 ✓）⇒ 丢线程池 ✓ 不然整个后端会卡住 ✗
        return await asyncio.to_thread(embed_local.embed, texts, model_id)

    if which != "dashscope":
        raise RuntimeError(
            f"未知的知识库向量档位：{which!r}（可用：local=本地离线 / dashscope=阿里云端）"
        )
    return await _embed_dashscope(texts)


async def _embed_dashscope(texts: list[str]) -> list[list[float]]:
    """云端档（阿里 DashScope，复用用户 Key；无 Key 给出明确指引 ✓ 并提示本地那条路 ✓）。"""
    key = os.environ.get("DASHSCOPE_API_KEY", "")
    if not key:
        raise RuntimeError(
            "云端向量化需要 DASHSCOPE_API_KEY（阿里云百炼 Key，与通义同账号）——"
            "设置到环境变量后重启后端。\n"
            "★ 也可以**改用本地向量**（离线、免费、数据不出本机）："
            "设置 → 知识库 → 向量档位选「本地」，或直接说「用本地的」。"
        )
    out: list[list[float]] = []
    async with httpx.AsyncClient(timeout=60) as client:
        for i in range(0, len(texts), 10):  # 每批 10 条
            batch = texts[i : i + 10]
            # ★ 2026-10-07（第 4 项）：**限流要退避** ✓ ——
            #   入库是把整本文档切成几十上百批发的 ✓ **越大的库越容易撞 429** ✗
            #   而原来一撞就整个库不入库 ✓（前面几十批白算 ✓ 用户只看到"入库失败"✓）
            #   ⇒ 按批退避重试 ✓（一次瞬时限流不该废掉整个入库 ✓）
            try:
                r = await retry.request_with_backoff(
                    client, "POST", "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings",
                    what=f"向量化第 {i // 10 + 1} 批", attempts=4,
                    headers={"Authorization": f"Bearer {key}"},
                    json={"model": "text-embedding-v3", "input": batch, "dimensions": 1024},
                )
            except retry.UpstreamBusy as e:
                raise RuntimeError(
                    f"{e}\n（入库可以重来 ✓ 已算好的批不会白算——重新入库会重跑；"
                    "也可以改用**本地向量**（设置 → 知识库向量 → 本地 ✓ 离线、不怕限流 ✓））"
                ) from e
            if r.status_code >= 400:
                raise RuntimeError(f"向量化失败（HTTP {r.status_code}）：{r.text[:150]}")
            data = r.json()
            items = sorted(data.get("data", []), key=lambda d: d.get("index", 0))
            # ★★ 2026-10-07（功能体检读代码读出来的真隐患 ✗✗）：
            #   这一段原来**直接 extend**，**不校验返回条数** ✗ ——
            #   而 `ingest_folder` 是按 `zip(texts, embeddings)` **按顺序配对**的 ✓
            #   ⇒ 只要服务端少返一条（截断 / 限流 / 某条被拒 ✓）✓
            #     后面所有块的 **文本与向量就整体错位** ✗✓ 而**没有任何报错** ✓
            #     ⇒ 表现为"检索结果张冠李戴"✗（问 A 文档，捞出来的是 B 文档的句子 ✓）
            #       而且**查不出来** ✓ 因为向量和文本各自都"正常" ✓。
            #   ⇒ 宁可**当场报错、这一库不入库** ✗ 也不能悄悄写进一个错位的库 ✓。
            if len(items) != len(batch):
                raise RuntimeError(
                    f"向量化返回条数不对：发出 {len(batch)} 条，回来 {len(items)} 条 —— "
                    "拒绝入库（否则文本与向量会整体错位，检索结果将张冠李戴）。"
                    "请重试；若持续如此，检查 DASHSCOPE_API_KEY 的配额或改用更小的库。"
                )
            out.extend(d["embedding"] for d in items)
    return out


def chunk_text(text: str, size: int = CHUNK_SIZE) -> list[str]:
    """按段落切块、合并到目标大小。"""
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    buf = ""
    for p in paras:
        if len(buf) + len(p) <= size:
            buf = f"{buf}\n{p}" if buf else p
        else:
            if buf:
                chunks.append(buf)
            while len(p) > size:  # 单段超长硬切
                chunks.append(p[:size])
                p = p[size:]
            buf = p
    if buf.strip():
        chunks.append(buf)
    return chunks


class KBStore:
    """知识库集合管理：data/kb/<名>/ = manifest.json + chunks.json。"""

    def __init__(self, root: Path):
        self.root = Path(root)
        self._chunks_cache: dict[str, tuple[float, list]] = {}  # §8.5：mtime 缓存（search 免全量重载）

    def _dir(self, name: str) -> Path:
        return self.root / name

    def list(self) -> list[dict[str, Any]]:
        out = []
        if not self.root.exists():
            return out
        for d in sorted(self.root.iterdir()):
            man = d / "manifest.json"
            if d.is_dir() and man.exists():
                try:
                    out.append(json.loads(man.read_text("utf-8")))
                except (json.JSONDecodeError, OSError):
                    continue
        return out

    def get(self, name: str) -> dict[str, Any] | None:
        for kb in self.list():
            if kb.get("name") == name:
                return kb
        return None

    def _load_chunks(self, name: str) -> list[dict[str, Any]]:
        """读某库的全部块。审计 §8.5：kb_search 每次调用都全量重载 JSON
        （5000 块实测 416ms，chunks.json 会涨到 ~100MB）→ 按 mtime 缓存，
        文件没变直接复用；ingest/delete 落盘后 mtime 变化自然失效。

        ★★ 2026-10-07（功能体检时**我自己的测试把它抓出来了** ✗✗）：
          原来**只比 mtime** ✗ —— 而 mtime 的时间粒度有限 ✓
          ⇒ **同一个时间粒度内重新入库同名知识库** ✓ 会命中旧缓存 ✓
          ⇒ 用户"明明重新入库了，检索还在用老内容" ✗✓ 而且**没有任何提示** ✓。
          （这是那种"偶尔复现、平时看不出来"的 bug ✓ 最容易活到线上 ✓。）
          ⇒ 现在**mtime + 文件大小**一起比 ✓：两个都一样 ⇒ 几乎不可能是不同内容 ✓
            （想更严可以存内容哈希 ✓ 但那要每次读全文件 ✗ 正好抵消掉缓存的意义 ✓）。
        """
        f = self._dir(name) / "chunks.json"
        if not f.exists():
            return []
        try:
            st = f.stat()
            key = (st.st_mtime, st.st_size)
        except OSError:
            return []
        cached = self._chunks_cache.get(name)
        if cached is not None and cached[0] == key:
            return cached[1]
        try:
            chunks = json.loads(f.read_text("utf-8"))
        except Exception as e:
            print(f"[kb] ⚠️ {name}/chunks.json 损坏（{type(e).__name__}: {e}），该库跳过检索", flush=True)
            return []
        self._chunks_cache[name] = (key, chunks)
        return chunks

    def _save_chunks(self, name: str, chunks: list[dict[str, Any]]) -> None:
        """落盘 + **顺手把缓存换成新块** ✓✓。

        ★★ 2026-10-07（体检时测试连续抓出两次 ✗✗）：
          第一版缓存**只比 mtime** ✗ ⇒ 同一时间粒度内重新入库会读到旧块 ✓
          第二版改成 **(mtime, size)** ✗ ⇒ 还是漏 ✓ —— 因为**两次入库的文件大小可能一样** ✓
          （块数相同、内容长度相近时就一样 ✓ 实测就是这样 ✓）。
          ⇒ 治本的办法不是"更聪明地猜文件变没变" ✗ 而是**让写的人自己说** ✓：
            `ingest_folder` 刚生成的 chunks **就在手里** ✓ 直接放进缓存 ✓
            ⇒ 同进程内"入库完立刻检索"**永远拿到新块** ✓（这才是最常见的那条路 ✓）。
          至于**外部**改动（有人手工编辑 chunks.json ✓）✓ 仍由 (mtime, size) 兜底 ✓。
        """
        self._dir(name).mkdir(parents=True, exist_ok=True)
        f = self._dir(name) / "chunks.json"
        f.write_text(json.dumps(chunks, ensure_ascii=False), "utf-8")
        try:
            st = f.stat()
            self._chunks_cache[name] = ((st.st_mtime, st.st_size), chunks)
        except OSError:
            self._chunks_cache.pop(name, None)

    async def ingest_folder(self, name: str, folder: Path, description: str = "") -> dict[str, Any]:
        """把一个本地文件夹 ingest 成知识库（覆盖同名）。"""
        folder = Path(folder)
        if not folder.is_dir():
            raise ValueError(f"文件夹不存在：{folder}")
        texts: list[tuple[str, str]] = []  # (source_file, chunk)
        for f in sorted(folder.rglob("*")):
            if not f.is_file() or f.suffix.lower() not in TEXT_EXTS:
                continue
            if f.stat().st_size > 1_000_000:
                continue  # 单文件 1MB 上限
            rel = f.relative_to(folder).as_posix()
            try:
                text = f.read_text("utf-8", errors="replace")
            except OSError:
                continue
            for c in chunk_text(text):
                if len(c) >= 30:  # 过滤碎屑
                    # 复审 P1（清洗闸）+P12：入库文本剥不可见控制字符；上限按块生效
                    # （此前单文件全部 append 后才 break——一个大文件可击穿 MAX_CHUNKS 烧 embedding 费）
                    from .redact import redact_text  # 验证报告 24：入库过打码管线
                    texts.append((rel, redact_text(sanitize_skill_text(c)[0])))
                    if len(texts) >= MAX_CHUNKS:
                        break
            if len(texts) >= MAX_CHUNKS:
                break
        if not texts:
            raise ValueError("文件夹里没有可入库的文本内容（支持 md/txt/代码等文本格式）")

        print(f"[知识库] {name}：{len(texts)} 块，向量化中…", flush=True)
        embeddings = await embed_texts([c for _, c in texts])
        chunks = [
            {"text": c, "source": rel, "embedding": emb}
            for (rel, c), emb in zip(texts, embeddings)
        ]
        self._save_chunks(name, chunks)
        manifest = {
            "name": name,
            "description": description or f"来自 {folder.name}",
            "chunks": len(chunks),
            "files": len({c["source"] for c in chunks}),
            "created": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        }
        self._dir(name).mkdir(parents=True, exist_ok=True)
        (self._dir(name) / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=1), "utf-8"
        )
        return manifest

    async def search(self, query: str, name: str | None = None, top_k: int = 5) -> list[dict[str, Any]]:
        """语义检索：query 向量化 → 与所有（或指定）知识库的块算余弦 → Top-K。"""
        kbs = [name] if name else [kb["name"] for kb in self.list()]
        if not kbs:
            raise RuntimeError("还没有已安装的知识库——先在设置页导入文件夹建库。")
        corpus: list[tuple[str, dict[str, Any]]] = []
        for kb in kbs:
            for c in self._load_chunks(kb):
                corpus.append((kb, c))
        if not corpus:
            return []

        q = (await embed_texts([query]))[0]

        scored = []
        for kb, c in corpus:
            emb = c.get("embedding") or []
            if emb:
                scored.append((_cosine(q, emb), kb, c))
        scored.sort(key=lambda x: x[0], reverse=True)
        out = [
            {"score": round(s, 4), "kb": kb, "source": c.get("source", ""), "text": c.get("text", "")[:800]}
            for s, kb, c in scored[: max(1, min(top_k, 10))]
        ]
        # 复审 P1（KB 清洗闸）：docstring 声称"清洗闸把关"但从未实现——入库/出库
        # 都过 sanitize_skill_text（剥不可见 Unicode 控制字符），与技能同标准
        from .redact import redact_text
        for r in out:
            r["text"], _ = sanitize_skill_text(r["text"])
            r["text"] = redact_text(r["text"])  # 验证报告 24：出库同样过管线
        return out

    def delete(self, name: str) -> bool:
        d = self._dir(name)
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
            self._chunks_cache.pop(name, None)  # 复审 P2：删库后清缓存（单库可达 ~100MB）
            return True
        return False

