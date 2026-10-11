"""存储层 —— 预留接口（storage.type=fs，将来可换 sqlite）。

落盘结构（契约一规则 4）：
  data/tasks/index.json                    任务索引（TaskSummary 列表）
  data/tasks/<task_id>/events.jsonl        事件流，每行一个信封 JSON
  data/tasks/<task_id>/workspace/          该任务的沙箱工作区

★★ 二十六轮第 7 批 A1：**本模块是"落盘"的唯一入口**，所以打码放在这里做。
   （真实事故：data/tasks/task_20261001_be1d/ 明文躺着 6 处密钥形状串、0 打码——
    用户把 Key 粘进聊天框、或 agent 跑 `env` 把它打出来，都会原样进
    history.json 与 events.jsonl。）
   分层原则（关键，别改）：
     · 内存里的 history / 上下文【保持真值】—— agent 干活要用（打码它的记忆会
       让它认不出自己的路径与取值，这个坑我们踩过并专门回退过）；
     · 只有【写盘的那份副本】被打码，而且只打【对话正文 user/assistant】这两个面
       （见下面 _MESSAGE_ROLES 的说明：其它事件各有自己的打码面，不能在这里推翻）。
   ⇒ 代价（如实声明）：重启后续聊时，读回的是打码后的历史，密钥位置显示为
     [已隐藏-疑似密钥]；界面在重新加载后同样显示打码结果。这是有意的安全属性。
"""
from __future__ import annotations

import json
import os
import shutil
import threading
from pathlib import Path
from typing import Any

from .redact import redact_text
from .schemas import EventEnvelope, TaskSummary

# 只打码【对话正文】这两个面（user / assistant）。
# ★ 为什么不在这里无脑套 redact_deep（本班实测踩过）：
#   observation / action / knowledge / usage 这些事件**各自已有自己的打码面**——
#   工具观察面走的是 `redact_text_tool`（弱一档，刻意保留"版本名 sk-model-v2 /
#   编号 SK-2026-001"两类结构上确定不是密钥的形态，好让 agent 能认出自己的东西），
#   action 的 params 走 `_redact_deep`（强）。在落盘口再套一层强规则，会
#   **推翻工具通道的既有决定**：实测 `test_observation_filename_shape_preserved_e2e`
#   当场变红（`SKU: SK-2026-001` 被打成 `[已隐藏-疑似密钥]`）。
#   而 A1 要修的是"用户消息 / assistant 文本【从没过任何打码】"——
#   真实事故目录 task_20261001_be1d 里 6 处明文正是这两个面。所以做外科式，不做全量。
_MESSAGE_ROLES = ("user", "assistant")


def _redact_message_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """对话消息事件的 payload → 返回打码后的**新 dict**（不改原对象）。"""
    if str(payload.get("role") or "") not in _MESSAGE_ROLES:
        return payload
    out = dict(payload)
    for k in ("text", "content"):
        if isinstance(out.get(k), str):
            out[k] = redact_text(out[k])
    return out


def _redact_history(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """写盘前的 history → 打码后的**新列表**（role=user/assistant 的正文过强规则）。

    role=tool 的结果**不动**：工具通道已按自己的口径（弱一档）打过，再套强规则
    会把 agent 需要的真名打掉（同上）。
    """
    out: list[dict[str, Any]] = []
    for m in history:
        if isinstance(m, dict) and str(m.get("role") or "") in _MESSAGE_ROLES:
            mm = dict(m)
            if isinstance(mm.get("content"), str):
                mm["content"] = redact_text(mm["content"])
            out.append(mm)
        else:
            out.append(m)
    return out


class FsStore:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self.tasks_dir = Path(data_dir) / "tasks"
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        self._index_path = self.tasks_dir / "index.json"
        self._lock = threading.Lock()  # 追加/索引写互斥（多任务并发 + SSE 读）

    # ---------- 任务索引 ----------

    def load_index(self) -> list[TaskSummary]:
        # 复审修正：index 损坏时告警回退 []，不再让后端启动即崩（数据仍在 tasks/ 子目录）
        if not self._index_path.exists():
            return []
        try:
            raw = json.loads(self._index_path.read_text("utf-8"))
            return [TaskSummary.model_validate(t) for t in raw]
        except Exception as e:
            print(f"[store] ⚠️ index.json 损坏（{type(e).__name__}: {e}），以空索引启动；"
                  f"损坏文件保留为 index.json.corrupt", flush=True)
            try:
                self._index_path.replace(self._index_path.with_suffix(".json.corrupt"))
            except OSError:
                pass
            return []

    @staticmethod
    def _atomic_write(path: Path, data: str) -> None:
        """复审修正：写临时文件再 os.replace——写一半崩溃不再留下损坏的 JSON
        （此前 index.json 损坏会让后端启动即崩）。"""
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(data, "utf-8")
        os.replace(tmp, path)

    def save_index(self, tasks: list[TaskSummary]) -> None:
        payload = json.dumps(
            [t.model_dump() for t in tasks], ensure_ascii=False, indent=2
        )
        with self._lock:
            self._atomic_write(self._index_path, payload)

    # ---------- 事件流 ----------

    def events_file(self, task_id: str) -> Path:
        return self.tasks_dir / task_id / "events.jsonl"

    def append_event(self, ev: EventEnvelope) -> None:
        f = self.events_file(ev.task_id)
        f.parent.mkdir(parents=True, exist_ok=True)
        # ★ A1：落盘前只对【对话消息】的正文打码（见本模块顶部说明）。
        #   dump 是新对象 ⇒ ev 本身及其内存引用不被改动，调用方拿到的仍是真值。
        dump = ev.model_dump()
        if ev.type == "message":
            dump["payload"] = _redact_message_payload(dump.get("payload") or {})
        line = json.dumps(dump, ensure_ascii=False)
        with self._lock:
            with f.open("a", encoding="utf-8") as fp:
                fp.write(line + "\n")

    def read_events(self, task_id: str, after_seq: int = 0) -> list[EventEnvelope]:
        """增量读取（after_seq=0 即全量）；损坏行跳过（JSONL 天然容忍坏行）。"""
        f = self.events_file(task_id)
        if not f.exists():
            return []
        out: list[EventEnvelope] = []
        for line in f.read_text("utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = EventEnvelope.model_validate(json.loads(line))
            except Exception:
                continue
            if ev.seq > after_seq:
                out.append(ev)
        return out

    def last_seq(self, task_id: str) -> int:
        """重启后恢复 seq 游标（否则会从 1 重复——和前端修过的同一个坑）。"""
        events = self.read_events(task_id)
        return events[-1].seq if events else 0

    # ---------- 工作区 ----------

    def usage_inflight_path(self, task_id: str) -> Path:
        return self.tasks_dir / task_id / "usage_inflight.json"

    def write_usage_inflight(self, task_id: str, data: dict[str, Any]) -> None:
        """★ 2026-10-07：把"**这次运行到目前为止**"的用量落一份快照 ✓（防被强杀丢账 ✗）。

        为什么需要它（第 1 项查证量出来的 ✓）：用量原本只在**跑完那一刻**记一笔 ✓
        而进程被**强杀**（`restart-backend.ps1` 就是强杀 ✓）⇒ 那一笔**永远发不出来** ✗
        实测：202 个任务里有 **12 个**是这么死的，它们跑过（合计 175 次动作）却**没留下账** ✗
        （粗估丢了约 230 万 tok ✓）—— 而**重启后端是这个项目的日常动作** ✓ 这个洞会一直漏 ✓

        ★ 不会重复计：快照里带 `run_id` ✓ 正式事件也带同一个 `run_id` ✓
          聚合时"有同 run_id 的正式事件 ⇒ 忽略快照" ✓（见 `main._task_usage` ✓）。
        """
        p = self.usage_inflight_path(task_id)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            self._atomic_write(p, json.dumps(data, ensure_ascii=False))
        except OSError:
            pass          # 记不下就算了 ✓（这只是"防丢"的兜底，绝不能因为写不了它而影响任务 ✗）

    def read_usage_inflight(self, task_id: str) -> dict[str, Any]:
        """读那份"跑到一半"的用量快照（没有/坏了都返回空 dict ✓ 绝不抛 ✗）。"""
        try:
            p = self.usage_inflight_path(task_id)
            if not p.exists():
                return {}
            d = json.loads(p.read_text("utf-8"))
            return d if isinstance(d, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def clear_usage_inflight(self, task_id: str) -> None:
        """跑完了（正式事件已落盘）⇒ 把快照删掉 ✓（留着就会被当成"还在跑"✓）。"""
        try:
            self.usage_inflight_path(task_id).unlink(missing_ok=True)
        except OSError:
            pass

    def set_task_workdir(self, task_id: str, path: str | Path) -> None:
        """★ 把某个任务的工作区**指到别处**（2026-10-05 真群回归暴露的坑）。

        原来每个任务一个独立工作区 ⇒ **同群的人互相看不到对方的产物** ⇒
        架构师写的 `docs/todo-api.md` 在它自己的工作区里，程序员在自己的空工作区里
        根本看不到 ⇒ **任何"按文件交接"都不可能成功**（实测里程序员只能回一句
        "工作区为空，未找到契约，我需要 @架构师 补充"）。

        现在：**同一个群共享一个工作区**（派发时指过去），交接才真的能用。
        映射落盘（`task_workdir.json`）：文件接口 `/tasks/{id}/files/raw` 与验收的
        低层检查都要靠它把"相对路径"解析到正确位置 —— 重启后也得认得。
        """
        f = self.data_dir / "task_workdir.json"
        try:
            data = json.loads(f.read_text("utf-8")) if f.exists() else {}
        except (OSError, json.JSONDecodeError):
            data = {}
        data[str(task_id)] = str(Path(path).resolve())
        f.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")

    def set_identity(self, task_id: str, role: str) -> None:
        """★ 2026-10-07：记下"这个任务现在以哪个身份在跑" ✓（`permissions` 靠它限权 ✓）。

        为什么必须落盘（而不是像原来那样只往 history 塞一条 `[身份切换]` 消息 ✗）：
          · **限权要在"谁在干"这件事上做判断** ✗ 而那条消息只是给模型看的文字 ✓
            后端自己并不知道"现在是谁" ⇒ 没法拦 ✓
          · 群里的续跑/接力是**新开一次运行** ✓ 身份必须跟着走 ✓（否则第二轮就"恢复默认"了 ✗）
        空 = 恢复默认助手 ✓（那时不受限 ✓）。
        """
        f = self.data_dir / "identities.json"
        try:
            data = json.loads(f.read_text("utf-8")) if f.exists() else {}
        except (OSError, json.JSONDecodeError):
            data = {}
        role = str(role or "").strip()
        if role:
            data[str(task_id)] = role
        else:
            data.pop(str(task_id), None)
        f.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")

    def identity(self, task_id: str) -> str:
        """这个任务当前的身份（没设过 = 空串 = 默认助手 ✓ 不受限 ✓）。"""
        f = self.data_dir / "identities.json"
        if not f.exists():
            return ""
        try:
            data = json.loads(f.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            return ""
        return str(data.get(str(task_id)) or "")

    def _task_workdir_override(self, task_id: str) -> Path | None:
        f = self.data_dir / "task_workdir.json"
        if not f.exists():
            return None
        try:
            data = json.loads(f.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        p = data.get(str(task_id))
        return Path(p) if p else None

    def group_workspace(self, gid: str) -> Path:
        """一个群**共享**的工作区（同群所有成员的任务都写这里，交接才成立）。"""
        d = self.data_dir / "groups" / gid / "workspace"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def workspace_dir(self, task_id: str) -> Path:
        override = self._task_workdir_override(task_id)
        if override is not None:
            override.mkdir(parents=True, exist_ok=True)
            return override
        d = self.tasks_dir / task_id / "workspace"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def snapshots_dir(self, task_id: str) -> Path:
        """交付快照目录（第六轮复验：必须在**工作区之外**——放工作区里会与交付物
        同树，`rm -rf .[!.]* *` 一并删掉，保护半径≈0）。"""
        d = self.tasks_dir / task_id / "snapshots"
        d.mkdir(parents=True, exist_ok=True)
        return d

    # ---------- 删除任务文件（第 7d 处） ----------

    def delete_task_files(self, task_id: str, trash: Path | None = None, stamp: str = "") -> dict[str, Any]:
        """★ 二十六轮第 7 批第 7d 处：真正删掉一个任务的落盘文件。

        为什么要有它（实测，不是推测）：
          `DELETE /api/v1/tasks/{id}` 此前只 `tasks.pop()` + 存索引，**一个文件都不删**。
          后果：`data/tasks/` 里积了 **213 个"界面上已删、硬盘还在"的目录共 470.5 MB**，
          其中包含一个明文出现过 API Key 的任务（用户以为删掉了，其实原封不动躺在盘上）。
          这既是隐私问题也是磁盘问题。

        ★ 这是**不可逆**操作，所以护栏逐条写死（少一条都可能删到别处）：
          1. task_id 必须是**纯名字**：无路径分隔符、非 `.`/`..`、非空；
          2. 目标必须是 `tasks_dir` 的**直接子目录**（resolve 后校验，防 `..` 与符号链接逃逸）；
          3. 拒绝删除符号链接 / Junction 本身（宁可报错也不"顺着链接删到别处"）；
          4. 拒绝删 `tasks_dir` 自己；
          5. 目录不存在 → `deleted=False`（幂等，不抛错）。
        返回 `{'deleted': bool, 'bytes': int, 'path': str}`，供接口层回报"释放了多少"。
        """
        root = self.tasks_dir.resolve()
        if not task_id or task_id in (".", "..") or "/" in task_id or "\\" in task_id:
            raise ValueError(f"非法 task_id：{task_id!r}")

        target = self.tasks_dir / task_id
        if target.is_symlink():
            raise ValueError(f"拒绝删除符号链接/Junction：{target}")

        real = target.resolve()
        if real == root or root not in real.parents:
            raise ValueError(f"拒绝越界的删除目标：{real}")
        if not real.is_dir():
            return {"deleted": False, "bytes": 0, "path": str(real)}

        size = sum(p.stat().st_size for p in real.rglob("*") if p.is_file())
        # ══════════════════════════════════════════════════════════════════════════
        # ★★ 2026-10-07（用户真机实测揪出）：**最后这一步从"真删"改成"挪走"** ✗✗
        #
        # 上面那次修复让"叉一下 = rmtree" ✓ —— 于是出现**轻重倒挂** ✗：
        #   · 「一键清干净」听着更吓人 ⇒ 反而**挪到 `_cleared_*` 能回滚** ✓
        #   · 侧栏那个**小叉** ⇒ **永久销毁 · 无确认 · 无备份 · 无留痕** ✗
        # 用户在真机上就是这么理解的（他的原话："我以为只是表面删除"）✗
        # 结果 198 个任务真的没了 ✓ 而且是 `rmtree` ⇒ **回收站里也没有** ✗
        #
        # ⇒ 现在**默认也挪走**（`trash=` 给目录 ✓ 同盘 rename 瞬时 ✓ 想找回就挪回来 ✓）
        #   「同一个动作在系统里只该有一种做法」✓ 与「一键清干净」对齐 ✓
        #   `trash=None` 仍是老行为（真删 ✓）—— 留给"确实要彻底销毁"的场合 ✓
        # ══════════════════════════════════════════════════════════════════════════
        if trash is None:
            shutil.rmtree(real)                              # 老行为：彻底销毁 ✓
            return {"deleted": True, "bytes": size, "path": str(real), "trashed": False}

        # ★ 护栏 6（新加的一闸 ✓）：回收目录也不许"顺着它跑到别处"✓
        tdir = Path(trash).resolve()
        if tdir == real or tdir in real.parents:
            raise ValueError(f"回收目录不能是目标自己或它的子目录：{tdir}")
        sub = tdir / stamp if stamp else tdir                # 每次删除一个时间戳子目录 ✓
        sub.mkdir(parents=True, exist_ok=True)
        dst = sub / task_id
        if dst.exists():                                     # 同名（删过又建过）⇒ 加时间戳 ✓ 不覆盖 ✗
            import time as _t
            dst = sub / f"{task_id}.{int(_t.time())}"
        try:
            real.rename(dst)                                 # 同盘 ⇒ 瞬时 ✓ 可回滚 ✓
        except OSError:                                      # 跨盘/被占用 ⇒ 退回复制删除 ✓
            shutil.move(str(real), str(dst))
        return {"deleted": True, "bytes": size, "path": str(dst), "trashed": True}

    def orphan_task_dirs(self) -> list[dict[str, Any]]:
        """列出**不在索引里**却仍占着盘的任务目录（第 7d 处：清理用，只读）。

        只列出、绝不删 —— 删不删由人决定（见 scripts/cleanup_orphan_tasks.py）。

        ★★ 失败方向必须朝"安全"那一侧（本班第一版写错过，被测试抓住）：
          第一版用了 `self._read(...)` —— FsStore 根本没这个方法（那是 TeamStore 的），
          抛出的 AttributeError 又被 `except Exception: keep = set()` 吞掉 ⇒ keep 变空集
          ⇒ **所有任务（包括正在用的）都被判成"孤儿"**。照那份清单清理会把在用的任务删掉。

        ★★ 第二道护栏（同样致命，别删）：
          `load_index()` 在 index.json **缺失或损坏**时会【静默返回 []】（损坏文件被改名）。
          若此时盘上还有任务目录，"孤儿 = 全部任务" 就是**误判** ——
          照这份清单清理等于**删光所有任务**。
          所以口径是：**索引为空却有任务目录 ⇒ 拒绝产出清单**（宁可什么都不列）。
        """
        try:
            index = self.load_index()
        except Exception:
            return []
        dirs = [p for p in (self.tasks_dir.iterdir() if self.tasks_dir.is_dir() else [])
                if p.is_dir() and not p.is_symlink()]
        if not index and dirs:
            print(f"[store] ⚠️ 索引为空但盘上有 {len(dirs)} 个任务目录"
                  f"（index.json 可能缺失/损坏已被改名）—— 拒绝生成孤儿清单，防误判为全量孤儿",
                  flush=True)
            return []

        keep = {t.id for t in index}
        out: list[dict[str, Any]] = []
        for p in sorted(dirs):
            if p.name in keep:
                continue
            size = sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
            out.append({"id": p.name, "bytes": size, "path": str(p)})
        return out

    # ---------- 对话历史（Phase 3：跨运行续聊记忆） ----------

    def history_file(self, task_id: str) -> Path:
        return self.tasks_dir / task_id / "history.json"

    def save_history(self, task_id: str, history: list[dict[str, Any]]) -> None:
        f = self.history_file(task_id)
        f.parent.mkdir(parents=True, exist_ok=True)
        # ★ A1：写盘的是【打码后的副本】——_redact_history 返回新对象，
        #   所以入参 history（也就是 agent 的内存上下文）保持真值、不被改动。
        safe = _redact_history(history)
        with self._lock:
            self._atomic_write(f, json.dumps(safe, ensure_ascii=False))

    def load_history(self, task_id: str) -> list[dict[str, Any]]:
        """续聊恢复上一轮对话；文件损坏视为无历史（事件流仍是权威记录）。"""
        f = self.history_file(task_id)
        if not f.exists():
            return []
        try:
            data = json.loads(f.read_text("utf-8"))
            return data if isinstance(data, list) else []
        except Exception:
            return []

    # ---------- Projects（配置注入） ----------

    def projects_file(self) -> Path:
        return self.tasks_dir.parent / "projects.json"

    def load_projects(self) -> list[dict[str, Any]]:
        f = self.projects_file()
        if not f.exists():
            return []
        try:
            data = json.loads(f.read_text("utf-8"))
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def save_projects(self, projects: list[dict[str, Any]]) -> None:
        with self._lock:
            self._atomic_write(self.projects_file(), json.dumps(projects, ensure_ascii=False, indent=2))

    # ---------- Automations（定时 / Webhook 触发） ----------

    def automations_file(self) -> Path:
        return self.tasks_dir.parent / "automations.json"

    def load_automations(self) -> list[dict[str, Any]]:
        f = self.automations_file()
        if not f.exists():
            return []
        try:
            data = json.loads(f.read_text("utf-8"))
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def save_automations(self, automations: list[dict[str, Any]]) -> None:
        with self._lock:
            self._atomic_write(self.automations_file(), json.dumps(automations, ensure_ascii=False, indent=2))

    # ---------- Wide Research（并行子 Agent 编排） ----------

    def wide_file(self) -> Path:
        return self.tasks_dir.parent / "wide.json"

    def load_wide(self) -> list[dict[str, Any]]:
        f = self.wide_file()
        if not f.exists():
            return []
        try:
            data = json.loads(f.read_text("utf-8"))
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def save_wide(self, wides: list[dict[str, Any]]) -> None:
        with self._lock:
            self._atomic_write(self.wide_file(), json.dumps(wides, ensure_ascii=False, indent=2))
