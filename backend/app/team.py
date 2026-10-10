"""团队（群聊）——"AI 公司"地基（第 41 班）。

员工卡（名字/部门/职位/大脑 BYOK/人设）→ 建群 → @点派 → 成果回流群聊。
派发 = 用该员工自己的大脑实例起一个常规任务（system_extra 注入人设），
完成监视器把交付卡片回写群消息流（复用宽调研的监视模式）。

存储：data/team/{employees.json, groups.json, feed_<群id>.json}——全本地。
"""
from __future__ import annotations

import json
import re
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .lessons import LessonStore

MAX_MEMBERS = 12       # 单个【群聊】的成员上限（并发与费用考虑）
# ★ 二十六轮第 7 批第 7b 处：团队成员总数此前也卡在 MAX_MEMBERS=12 ——
#   那是把"一个群里坐得下几个人"的限制，错误地套到了"整个团队能有多少人"上。
#   专家模式 / 自由人设会持续加人，12 人显然不够；两者语义不同，必须拆开。
#   MAX_EMPLOYEES 是【防呆上限】（防误操作把内存/文件撑爆），不是产品限制。
MAX_EMPLOYEES = 200

# ★★★ 2026-10-06「交付正文的长度上限」——**一个常量，别再多处各写一个数** ✓✓
#
# 为什么专门立它（这是今天大半失败的单一根因 ✗✗）：
#   交付正文原本被**三把刀**各砍一段：看门 `[:800]` ✓ 存计划项 `[:800]` ✓ 验收提示词 `[:500]` ✓ ——
#   而交付的**小节顺序**是「改动文件 → 自测命令 → **真实输出**」✓
#   ⇒ 那段**唯一的实证**（真跑过的输出）永远落在刀口之前被丢掉 ✓
#   ⇒ 验收人每次都说"交付摘要为空 / 没贴输出" ✓ **而它说的是实话** ✓
#   ⇒ 于是它按规矩打回 ✗ 打回 3 次 ⇒ 整项判失败 ✓✓
#   （我们前几轮还一直以为"是验收人太苛刻" ✗ —— 冤枉了它好几轮 ✓）
#
# 4000 字的来由：实测一份真实交付正文 **2558 字**（含文件清单 + 命令 + 测试输出）✓，留一倍余量 ✓。
DELIVERY_TEXT_MAX = 4000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class TeamStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self._lock = threading.Lock()
        # ★ 2026-10-06 经验库（"这类活上次怎么栽的" ✓）—— 派活时按相关度注入 ✓
        self.lessons = LessonStore(self.root)

    def _read(self, f: Path, default: Any) -> Any:
        # 复审：坏文件改名保留而非静默当空（防下一次 _save 基于空列表覆写历史）
        try:
            return json.loads(f.read_text("utf-8"))
        except FileNotFoundError:
            return default
        except Exception:
            try:
                import time
                f.replace(f.with_suffix(f".corrupt-{int(time.time())}"))
            except OSError:
                pass
            return default

    def _write(self, f: Path, data: Any) -> None:
        # 复审：原子写——团队/群/聊天记录此前撕裂写 + _read 容错叠加 = 一次崩溃可清空群记录
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
        import os
        os.replace(tmp, f)

    # ---------- 员工卡 ----------

    def employees(self) -> list[dict[str, Any]]:
        return self._read(self.root / "employees.json", [])

    def add_employee(self, card: dict[str, Any]) -> dict[str, Any]:
        card = {
            "id": f"emp_{secrets.token_hex(3)}",
            "name": str(card.get("name") or "").strip()[:20],
            "dept": str(card.get("dept") or "综合部").strip()[:12],
            "role": str(card.get("role") or "通用助理").strip()[:20],
            "mode": ("free" if str(card.get("mode") or "expert") == "free" else "expert"),
            "persona": ("" if str(card.get("mode") or "expert") == "expert" else str(card.get("persona") or "").strip()[:300]),
            "provider": str(card.get("provider") or "").strip() or None,
            "model_name": str(card.get("model_name") or "").strip() or None,
            "base_url": str(card.get("base_url") or "").strip() or None,
            "api_key_env": str(card.get("api_key_env") or "").strip() or None,
            "color": str(card.get("color") or "#6ba3f5"),
            "created": _now(),
        }
        if not card["name"]:
            raise ValueError("员工必须有名字")
        with self._lock:
            emps = self.employees()
            if len(emps) >= MAX_EMPLOYEES:
                raise ValueError(
                    f"团队成员最多 {MAX_EMPLOYEES} 人（防呆上限；群聊的 {MAX_MEMBERS} 人上限另算）"
                )
            if any(e["name"] == card["name"] for e in emps):
                # ★★ 2026-10-06（用户实测提问："建一个群聊就得新建一个员工卡呀？" ✗）：
                #   产品**不是**那样设计的 ✓ —— 建群是"勾选已有员工" ✓。
                #   但这条报错原来只有一句「已有同名员工：小甲」✗ ⇒ 用户不知道该干嘛 ✓
                #   ⇒ 补上"那怎么办"（两条出路 ✓ 第一条通常才是他真正想干的事 ✓）。
                raise ValueError(
                    f"已有同名员工：{card['name']} —— 你**不用新建一张卡** ✓："
                    f"建群的时候直接勾选已有的「{card['name']}」就行 ✓；"
                    "如果确实要两个同名的，把名字改一下（比如加个后缀）再建 ✓"
                )
            emps.append(card)
            self._write(self.root / "employees.json", emps)
        return card

    def update_employee(self, emp_id: str, card: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            emps = self.employees()
            idx = next((i for i, e in enumerate(emps) if e["id"] == emp_id), None)
            if idx is None:
                raise ValueError("员工不存在")
            e = emps[idx]
            new_name = str(card.get("name") or e["name"]).strip()[:20]
            if any(x["name"] == new_name and x["id"] != emp_id for x in emps):
                raise ValueError(
                    f"已有同名员工：{new_name} —— 名字得换一个（群里的 @点名 靠名字找人的 ✓ "
                    "两个同名的话，@名字 就不知道派给谁了 ✓）"
                )
            mode = "free" if str(card.get("mode") or e.get("mode", "expert")) == "free" else "expert"
            e.update({
                "name": new_name,
                "dept": str(card.get("dept") or e.get("dept", "综合部")).strip()[:12],
                "role": str(card.get("role") or e.get("role", "通用助理")).strip()[:20],
                "mode": mode,
                "persona": (str(card.get("persona") or "").strip()[:300] if mode == "free" else ""),
                "provider": str(card.get("provider") or "").strip() or None,
                "model_name": str(card.get("model_name") or "").strip() or None,
                "base_url": str(card.get("base_url") or "").strip() or None,
                "api_key_env": str(card.get("api_key_env") or "").strip() or None,
            })
            self._write(self.root / "employees.json", emps)
            return e

    def delete_employee(self, emp_id: str) -> bool:
        with self._lock:
            emps = self.employees()
            remain = [e for e in emps if e["id"] != emp_id]
            if len(remain) == len(emps):
                return False
            self._write(self.root / "employees.json", remain)
            # 从所有群里移除该员工（审计 §6.5：改完 members 却写 feed 文件——
            # groups.json 没落盘，鬼影成员永存。改回写 groups.json）
            groups = self.groups()
            changed = False
            for g in groups:
                new_members = [m for m in g["members"] if m != emp_id]
                if new_members != g["members"]:
                    g["members"] = new_members
                    changed = True
            if changed:
                self._write(self.root / "groups.json", groups)
            return True

    def get_employee(self, emp_id: str) -> dict[str, Any] | None:
        return next((e for e in self.employees() if e["id"] == emp_id), None)

    # ---------- 群 ----------

    # ★ 群派发模式（2026-10-05 扩两种：接力 / 开会）
    #   manual   点名派：@谁谁做
    #   broadcast 广播：全员各领同一件事
    #   leader   组长拆解：组长自己拆 → 并行分给各人
    #   relay    接力：一个做完，产出**交给下一个**接着做（串行，不是并行）
    #   meeting  开会：围绕议题来回讨论若干轮 → 收口成结论（不产出代码/文件）
    MODES = ("manual", "broadcast", "leader", "relay", "meeting")

    def groups(self) -> list[dict[str, Any]]:
        return self._read(self.root / "groups.json", [])

    def create_group(self, name: str, members: list[str], leader: str | None = None,
                     mode: str = "manual") -> dict[str, Any]:
        """建群。leader = 组长员工 id（组长模式：你说目标，它拆解分派）；
        mode: manual（点名派）/ broadcast（全员广播认领）。"""
        name = str(name).strip()[:30]
        if not name:
            raise ValueError("群名不能为空")
        if mode not in self.MODES:
            # 复审 P2：任意字符串此前静默接受——大写"Leader"会让派发分支永不命中且无报错
            raise ValueError(f"mode 必须是 {'/'.join(self.MODES)}，得到：{mode}")
        if leader and leader not in members:
            members = [leader, *members]  # 组长必须在自己群里
        members = list(dict.fromkeys(m for m in members if self.get_employee(m)))
        # 复审 P2：重复 id 去重——广播模式下同一员工被派两遍 = 双倍烧钱
        if not members:
            raise ValueError("群里至少要有一名员工")
        if len(members) > MAX_MEMBERS:
            raise ValueError(f"群成员最多 {MAX_MEMBERS} 人")
        if leader and leader not in members:
            raise ValueError("组长必须在群成员中")
        g = {
            "id": f"grp_{secrets.token_hex(3)}", "name": name, "members": members,
            "leader": leader, "mode": mode, "created": _now(),
            "claim_enabled": mode == "broadcast",  # 广播认领开关（组长模式自动关）
        }
        with self._lock:
            gs = self.groups()
            gs.append(g)
            self._write(self.root / "groups.json", gs)
        return g

    def set_leader(self, gid: str, leader: str | None) -> dict[str, Any]:
        # 复审 P2：读-校验-写全程持锁（此前锁外读 g，锁内整组覆写可复活已删成员）
        with self._lock:
            gs = self.groups()
            idx = next((i for i, x in enumerate(gs) if x["id"] == gid), None)
            if idx is None:
                raise ValueError("群不存在")
            g = gs[idx]
            if leader and leader not in g["members"]:
                raise ValueError("组长必须在群成员中")
            g["leader"] = leader
            # 设了组长就切组长模式；原本是 relay/meeting 的群，这一步不该顺手改掉它的模式
            if leader:
                g["mode"] = "leader"
            else:
                g.setdefault("mode", "manual")
            gs[idx] = g
            self._write(self.root / "groups.json", gs)
        return g

    def set_mode(self, gid: str, mode: str) -> dict[str, Any]:
        """★ 二十六轮第 7 批第 7c 处：建群后也能改派发模式。

        此前 `mode` 只能在 create_group 那一刻定（那儿有合法性校验），建完就改不了
        —— 用户想换模式只能删群重建（聊天记录一起没了），或者忍着用错的模式。
        用户原话：「这几个模式……如果创建群聊之后，然后在群聊里都可以选择可不可以」。

        语义与 create_group 保持完全一致（避免两套口径）：
          · MODES 里那几个（manual/broadcast/leader/relay/meeting），大小写不敏感（统一小写存储）；
          · 切到 leader 必须【已经有组长】—— 否则会留下"组长模式却没有组长"的空壳，
            那种群在派发时会静默走 manual 分支，是最难查的一类问题；
          · claim_enabled 跟着 mode 走（只有 broadcast 开认领）。
        """
        with self._lock:
            gs = self.groups()
            idx = next((i for i, x in enumerate(gs) if x["id"] == gid), None)
            if idx is None:
                raise ValueError("群不存在")
            g = gs[idx]
            m = str(mode or "").strip().lower()
            if m not in self.MODES:
                raise ValueError(f"mode 必须是 manual/broadcast/leader，得到：{mode}")
            if m == "leader" and not g.get("leader"):
                raise ValueError("切到组长模式前，先给这个群指定一名组长")
            g["mode"] = m
            g["claim_enabled"] = (m == "broadcast")
            gs[idx] = g
            self._write(self.root / "groups.json", gs)
        return g

    def delete_group(self, gid: str) -> bool:
        with self._lock:
            gs = self.groups()
            remain = [g for g in gs if g["id"] != gid]
            if len(remain) == len(gs):
                return False
            self._write(self.root / "groups.json", remain)
            (self.root / f"feed_{gid}.json").unlink(missing_ok=True)
            return True

    def get_group(self, gid: str) -> dict[str, Any] | None:
        return next((g for g in self.groups() if g["id"] == gid), None)

    # ---------- 群消息流 ----------

    def persona_for(self, emp: dict[str, Any]) -> str:
        """派发时的人设解析：专家模式→后端角色库完整人设（+用户附加要求）；自由模式→用户自写。"""
        from .roles import ROLE_LIBRARY, persona_for as _pf

        role = emp.get("role", "通用助理")
        if emp.get("mode", "expert") == "expert" and role in ROLE_LIBRARY:
            return _pf(role, emp.get("persona"))
        return str(emp.get("persona") or "") or f"你是团队的{role}，尽职完成交给你的任务。"

    def leader_dispatch_prompt(self, g: dict[str, Any], goal: str) -> str:
        """组长模式分解提示词：给组长看名册（职位/能力），输出分工单 JSON。"""
        roster = []
        for m in g["members"]:
            e = self.get_employee(m)
            if e:
                roster.append(f"- {e['name']}（{e.get('dept','')}·{e.get('role','')}）：{e.get('persona','')[:100]}")
        return (
            "你是团队组长。下面是目标和你的组员名册（职位与能力）。" + chr(10)
            + "把目标拆成子任务并分配。**每个人只做自己那一步**，边界要写清楚，别让两个人做同一件事。" + chr(10)
            # ★★ 2026-10-06（用户问"并发那么多为什么还慢"→ 真跑量出来的）：
            #   小活也被拆成"架构师写契约 → 程序员实现 → 测试验收" ✓ ⇒ 光"仪式"就吃掉一两轮 ✓
            #   （实测：写个 wordcount.py 也要先派架构师写 docs/spec.md ✓ 全程 25 分钟 / ¥0.4 ✓）。
            #   ⇒ 规矩：**判断活的大小** —— 一个人一次能做完的 ✓ **就只派一项** ✓
            #     不要契约文档 ✗ 不要为拆而拆 ✗。
            + "★★ **先判断这活有多大**（这条很省钱，也省时间 ✓）：" + chr(10)
            + "· **一个人一次就能做完的小活**（写个小脚本 ✓ 改个小 bug ✓ 做个单文件网页 ✓ "
              "写份文档 ✓ 查个资料）⇒ **只派一项** ✓ 直接让最合适的那个人做完 ✓；" + chr(10)
            + "· 这种情况**不要**先派「架构师写契约」✗、**不要**拆成三步走流程 ✗ —— "
              "那是**为拆而拆**，白白多花一两轮 ✓（用户实测：「小活为什么要等架构师」✓）；" + chr(10)
            + "· 只有**真的多块活、别人要并行做、或者要交接产物**时才拆成多项 ✓；" + chr(10)
            + "· 拿不准就**少拆** ✓（拆多了一定更慢更贵 ✓ 拆少了最多慢一点点 ✓）。" + chr(10)
            + "★ 如果某项活**必须等别人的产物**才能开工（比如「程序员要按架构师的接口契约写代码」），"
            "必须写进 `depends_on`；能并行的就别写依赖（并行更快）。" + chr(10)
            + chr(10) + "★★ 输出格式（不遵守就等于没派活，系统会退回给你）：" + chr(10)
            + "· **只输出一个 JSON 数组**，第一个字符必须是 `[`，最后一个字符必须是 `]`；" + chr(10)
            + "· **不要**写标题、排期表、风险分析、总结、寒暄，**不要**用 Markdown 表格或代码围栏；" + chr(10)
            + "· 每项四个字段：" + chr(10)
            + '  {"name": "组员名", "task": "做什么（含边界，别与他人重叠）", '
            + '"output": "交付什么（产物/格式/写到哪里）", "depends_on": ["必须等谁的产物"]}' + chr(10)
            + '· 例：[{"name": "架构师", "task": "定订单服务的 API 契约", "output": "docs/api.md", "depends_on": []},'
            + ' {"name": "程序员", "task": "按契约实现订单接口", "output": "代码 + 自测结果", "depends_on": ["架构师"]}]' + chr(10)
            + "· `depends_on` 只能写名册里的名字；没有依赖就写 `[]`。" + chr(10) + chr(10)
            + "★★ 产物要**结构化**（下游靠产物干活，不是靠聊天记录）：" + chr(10)
            + "· **设计/架构**类：`output` 写成一份**设计文档**，里面必须有"
            "「文件清单（要建哪些文件、各自干什么）」「接口定义（函数签名 / 命令 / HTTP 路由）」「数据结构」；" + chr(10)
            + "· **实现**类：`output` 写明「改动/新增了哪些文件」+「怎么自测（命令）」；" + chr(10)
            + "· **测试**类：`output` 写明「用例清单」+「实际运行结果」。" + chr(10) + chr(10)
            # ★★★ 2026-10-06 用户实测（"我不知道在哪打开，看代码我不懂"）：
            #   我们做出来的是**命令行工具** ✗ —— 用户根本不敲命令，等于白做 ✓。
            #   所以：**工具类产物优先做成"双击就能用的单文件网页"**（浏览器打开即用、数据存浏览器里 ✓）。
            + "★★★ 产物要**用户能直接用**（这条最重要）：" + chr(10)
            + "· 如果做出来的是「工具 / 小应用」，**优先做成一个单文件网页**"
            "（`index.html`：双击就用、数据存在浏览器里）——**别只交命令行脚本** ✗（用户不敲命令）；" + chr(10)
            + "· 网页要能**离线打开**：不引用外部 CDN、不要求安装任何东西；" + chr(10)
            + "· 命令行脚本可以**顺带**给（算加分项），但不能是唯一产物。" + chr(10)
            # ★★★ 2026-10-06（用户接着问："写微信小程序/App 也能这么看吗？"）：
            #   他问得对 —— **不同产物有不同的打开方式** ✗，不是"一律双击网页"能解决的 ✓。
            #   所以每条交付都必须回答一句：**用户拿到它，怎么打开 / 怎么用** ✓。
            + "★★★ 不管做什么，交付里**必须有一节「怎么打开 / 怎么用」**" + chr(10)
            + "· 网页类 ⇒ 写「双击或浏览器打开 index.html」；" + chr(10)
            + "· **微信小程序** ⇒ 写明「用**微信开发者工具**导入本目录」（免费的官方工具），"
            "**并且尽量同时给一份能双击打开的网页预览**（`preview.html`），方便直接看效果；" + chr(10)
            + "· **桌面应用** ⇒ 写明怎么构建（命令）与产物在哪（安装包路径）；" + chr(10)
            + "· **手机 App** ⇒ 写明怎么跑（`flutter run` / `npx react-native run-android` 等）与产物；" + chr(10)
            + "· **后端/服务** ⇒ 写明怎么启动、端口是多少、打开哪个地址；能给「双击启动脚本」最好 ✓；" + chr(10)
            + "· 一句话标准：**照你写的那节做，用户能自己把它跑起来/打开** ✓（写不出来就说明交付不完整 ✗）。" + chr(10) + chr(10)
            + f"【目标】{goal}" + chr(10) + chr(10)
            + "【组员名册】" + chr(10) + chr(10).join(roster)
        )

    # ═══ 广播批次 + 项目终验门（★ 2026-10-06）═══
    #
    # 现场（用户问"其他模式都试没试"）：**只有组长模式有"项目终验门"** ✗ ——
    # 广播模式里"每个人都交了"就算完成 ✓，**没人回答"这东西到底能不能跑"** ✗。
    # 而那句教训（"做完 ≠ 能跑"）**与模式无关** ✓。
    #
    # 做法（不新增机器 ✓）：广播派活时记一个**批次**（发了哪些任务）；
    # 每个任务交付就划掉一个；**最后一个交付时** ⇒ 派一道项目终验（走现成的任务+看门者）✓；
    # 终验交付时由看门者宣布 ✅/❌ ✓。状态放群上（`broadcast_batch`），重启也不丢 ✓。

    def broadcast_begin_batch(self, gid: str, goal: str, task_ids: list[str]) -> dict[str, Any]:
        """记下这一批广播任务（`left` = 还没交付的个数 ✓）。"""
        with self._lock:
            gs = self.groups()
            idx = next((i for i, x in enumerate(gs) if x["id"] == gid), None)
            if idx is None:
                raise ValueError("群不存在")
            ids = [str(t) for t in task_ids if t]
            batch = {"goal": str(goal)[:300], "ids": ids, "left": ids,
                     "gate_task": None, "started": _now()}
            gs[idx]["broadcast_batch"] = batch
            self._write(self.root / "groups.json", gs)
            return batch

    def broadcast_settle(self, gid: str, task_id: str) -> dict[str, Any] | None:
        """某个广播任务交付了。**返回批次对象当且仅当"这是最后一个"** ✓（该派终验了）。

        非批次里的任务、或还没轮到最后 ⇒ 返回 None ✓（看门者据此决定要不要派门 ✓）。
        """
        with self._lock:
            gs = self.groups()
            idx = next((i for i, x in enumerate(gs) if x["id"] == gid), None)
            if idx is None:
                return None
            b = gs[idx].get("broadcast_batch")
            if not isinstance(b, dict) or task_id not in (b.get("left") or []):
                return None
            # ★ 只划掉**一个** ✓ —— 第一版写成"过滤掉所有等于它的项" ✗：
            #   万一两个任务 id 相同（或重复投递），一次交付就把整批划光 ⇒ **终验提前开跑** ✗
            #   （本班的测试用重复 id 当场逮到了这个 bug ✓）
            left = list(b["left"])
            left.remove(task_id)
            b["left"] = left
            self._write(self.root / "groups.json", gs)
            return dict(b) if not left else None

    def broadcast_gate_done(self, gid: str, task_id: str) -> dict[str, Any] | None:
        """终验这道门交付了 ⇒ 记下它的任务 id（看门者据此宣布结论 ✓）。"""
        with self._lock:
            gs = self.groups()
            idx = next((i for i, x in enumerate(gs) if x["id"] == gid), None)
            if idx is None:
                return None
            b = gs[idx].get("broadcast_batch")
            if not isinstance(b, dict) or b.get("gate_task") != task_id:
                return None
            b["gate_done"] = True
            self._write(self.root / "groups.json", gs)
            return dict(b)

    def broadcast_attach_gate(self, gid: str, task_id: str) -> None:
        """把"这道终验属于哪个广播批次"记上（看门者交付时才知道该宣布什么 ✓）。"""
        with self._lock:
            gs = self.groups()
            idx = next((i for i, x in enumerate(gs) if x["id"] == gid), None)
            if idx is None:
                return
            b = gs[idx].get("broadcast_batch")
            if isinstance(b, dict):
                b["gate_task"] = str(task_id)
                self._write(self.root / "groups.json", gs)

    # ═══ 接力模式（relay）：串行交接 ═══
    #   与"组长拆解"的区别是**意图**：组长是把一个大目标切成**并行**的独立子任务；
    #   接力是同一件事**一个人做完交给下一个**（写作→评审→定稿、原型→实现→测试）。
    #   状态放群上：relay_pos = 当前轮到第几棒（0 = 没在跑；1..N = 第几棒进行中）。

    def relay_order(self, g: dict[str, Any]) -> list[str]:
        """接力顺序：群里显式排过就用排的，否则按成员顺序（建群时的顺序）。"""
        order = [m for m in (g.get("relay_order") or []) if m in g.get("members", [])]
        return order or list(g.get("members", []))

    def relay_begin(self, gid: str, goal: str = "") -> dict[str, Any]:
        """开始一轮接力：记下总目标、把位置置到第 1 棒。

        ★ 必须把 goal 存到群上：后续每一棒的工作单都要带上它（`relay_prompt`），
          而交付回流发生在**另一个后台任务**里 —— 那时早就拿不到当初那句原话了。
        """
        with self._lock:
            gs = self.groups()
            idx = next((i for i, x in enumerate(gs) if x["id"] == gid), None)
            if idx is None:
                raise ValueError("群不存在")
            g = gs[idx]
            order = self.relay_order(g)
            if not order:
                raise ValueError("群里没有成员，无法接力")
            g["relay_pos"], g["relay_total"] = 1, len(order)
            g["relay_goal"], g["relay_done"] = str(goal or "").strip()[:2000], False
            g.pop("relay_stop_reason", None)
            gs[idx] = g
            self._write(self.root / "groups.json", gs)
        return {"emp_id": order[0], "pos": 1, "total": len(order)}

    def set_relay_order(self, gid: str, order: list[str]) -> dict[str, Any]:
        """显式指定接力顺序（不指定就按成员顺序）。

        过滤掉**已经不在群里**的成员：否则删了人之后，接力会在某一棒派给一个不存在的员工，
        表现成"接力跑着跑着停住"，很难查。
        """
        with self._lock:
            gs = self.groups()
            idx = next((i for i, x in enumerate(gs) if x["id"] == gid), None)
            if idx is None:
                raise ValueError("群不存在")
            g = gs[idx]
            keep = [str(x) for x in (order or []) if x in g["members"]]
            g["relay_order"] = list(dict.fromkeys(keep))
            gs[idx] = g
            self._write(self.root / "groups.json", gs)
        return g

    def relay_advance(self, gid: str, expect_pos: int) -> dict[str, Any] | None:
        """某一棒交付后推进到下一棒。

        ★ `expect_pos` 是**防串棒**的：看门任务带着自己那一棒的位置来，
          只有群上记的位置还等于它时才算数 —— 否则（重复回调、乱序、用户中途重置）
          会出现"同一棒被推进两次"，甚至在最后一位反复打转。
        返回 None 表示不再推进（已到最后一棒 / 位置对不上）。
        """
        with self._lock:
            gs = self.groups()
            idx = next((i for i, x in enumerate(gs) if x["id"] == gid), None)
            if idx is None:
                return None
            g = gs[idx]
            if int(g.get("relay_pos") or 0) != int(expect_pos):
                return None
            order = self.relay_order(g)
            nxt = int(expect_pos) + 1
            if nxt > len(order):
                g["relay_pos"], g["relay_done"] = 0, True
                gs[idx] = g
                self._write(self.root / "groups.json", gs)
                return {"finished": True, "total": len(order)}
            g["relay_pos"], g["relay_done"] = nxt, False
            gs[idx] = g
            self._write(self.root / "groups.json", gs)
        return {"emp_id": order[nxt - 1], "pos": nxt, "total": len(order)}

    def relay_stop(self, gid: str, why: str = "") -> None:
        """中断接力（某棒失败/超时、或用户手动停）。位置归零，群里能看到原因。"""
        with self._lock:
            gs = self.groups()
            idx = next((i for i, x in enumerate(gs) if x["id"] == gid), None)
            if idx is None:
                return
            g = gs[idx]
            g["relay_pos"], g["relay_done"] = 0, False
            if why:
                g["relay_stop_reason"] = why[:200]
            gs[idx] = g
            self._write(self.root / "groups.json", gs)

    # ═══ 开会模式（meeting）：围绕议题来回讨论 → 收口 ═══
    #   设计参考（2026-10-05 先查再定）：
    #     · dsh-group-chat（DSH 插件，AI 主持人驱动的多角色群聊）：**群内共享发言**
    #       （以「【角色名】内容」注入所有人上下文）、多轮轮流发言、**结论导出**
    #     · AutoGen GroupChatManager：**主持人动态选人 + 终止条件 + 总结**
    #   所以开会**不是**"每人说一遍就完"：每轮结束问主持人"够了没"，够就提前收口（省钱），
    #   最后统一产出「结论 / 分歧 / 待办」。
    MAX_MEETING_ROUNDS = 5
    DEFAULT_MEETING_ROUNDS = 2
    MEETING_MAX_TRANSCRIPT = 12000        # 采样上限：讨论越长越贵，超了截断（保留最近的）

    def meeting_begin(self, gid: str, topic: str, rounds: int = 0) -> dict[str, Any]:
        """起一场会：记议题与轮次，清空上一场的记录，返回参会顺序。"""
        with self._lock:
            gs = self.groups()
            idx = next((i for i, x in enumerate(gs) if x["id"] == gid), None)
            if idx is None:
                raise ValueError("群不存在")
            g = gs[idx]
            members = [m for m in g["members"] if self.get_employee(m)]
            if not members:
                raise ValueError("群里没有成员，开不了会")
            r = int(rounds or 0) or self.DEFAULT_MEETING_ROUNDS
            r = max(1, min(self.MAX_MEETING_ROUNDS, r))
            g["meeting_topic"] = str(topic or "").strip()[:2000]
            g["meeting_rounds"], g["meeting_round"] = r, 0
            g["meeting_state"], g["meeting_transcript"] = "running", []
            g["meeting_started"] = _now()
            gs[idx] = g
            self._write(self.root / "groups.json", gs)
        return {"order": members, "rounds": r}

    def meeting_append(self, gid: str, name: str, text: str, round_no: int) -> None:
        """把一条发言记进共享记录（**所有人**后续发言都能看到它 —— 开会与"各说各话"的分界）。"""
        with self._lock:
            gs = self.groups()
            idx = next((i for i, x in enumerate(gs) if x["id"] == gid), None)
            if idx is None:
                return
            g = gs[idx]
            tr = list(g.get("meeting_transcript") or [])
            tr.append({"name": name, "text": str(text or "")[:4000], "round": int(round_no)})
            g["meeting_transcript"], g["meeting_round"] = tr, int(round_no)
            gs[idx] = g
            self._write(self.root / "groups.json", gs)

    def meeting_finish(self, gid: str, summary: str, reason: str = "") -> None:
        with self._lock:
            gs = self.groups()
            idx = next((i for i, x in enumerate(gs) if x["id"] == gid), None)
            if idx is None:
                return
            g = gs[idx]
            g["meeting_state"] = "done"
            g["meeting_summary"] = str(summary or "")[:8000]
            if reason:
                g["meeting_stop_reason"] = str(reason)[:200]
            gs[idx] = g
            self._write(self.root / "groups.json", gs)

    @staticmethod
    def meeting_transcript_text(g: dict[str, Any]) -> str:
        """共享记录 → 提示词文本。格式「【名字】内容」：谁说的、说了什么一目了然，
        也让模型能直接"接着某人的话说"（dsh-group-chat 的做法）。"""
        tr = list(g.get("meeting_transcript") or [])
        lines = [f"【{x.get('name')}】{x.get('text')}" for x in tr]
        text = chr(10).join(lines)
        if len(text) > TeamStore.MEETING_MAX_TRANSCRIPT:
            text = "（前面较长的发言已省略）" + chr(10) + text[-TeamStore.MEETING_MAX_TRANSCRIPT:]
        return text

    @staticmethod
    def meeting_speak_prompt(g: dict[str, Any], emp: dict[str, Any], round_no: int) -> str:
        rounds = int(g.get("meeting_rounds") or 1)
        return (
            f"你们正在开会讨论一个议题。你是「{emp['name']}」（{emp.get('dept', '')}·{emp.get('role', '')}）。"
            + chr(10) + "规则：可以补充、质疑、反驳别人的观点，但**不要复述**别人已说过的；"
            + "有分歧就直说分歧在哪；说人话，控制在 200 字内。" + chr(10)
            + f"【议题】{g.get('meeting_topic') or ''}" + chr(10)
            + f"【第 {round_no}/{rounds} 轮 · 已有发言】" + chr(10)
            + (TeamStore.meeting_transcript_text(g) or "（你是第一个发言的）") + chr(10) + chr(10)
            + "只输出你的发言内容，不要写「名字：」前缀。"
        )

    @staticmethod
    def meeting_moderator_prompt(g: dict[str, Any], round_no: int) -> str:
        """每轮结束问主持人一次：**够了没**（提前收口就是省钱）。"""
        rounds = int(g.get("meeting_rounds") or 1)
        return (
            "你是这场讨论的主持人。判断讨论是否已经足够产出结论。" + chr(10)
            + f"【议题】{g.get('meeting_topic') or ''}" + chr(10)
            + f"【已进行 {round_no}/{rounds} 轮 · 记录】" + chr(10)
            + (TeamStore.meeting_transcript_text(g) or "（还没有发言）") + chr(10) + chr(10)
            + "如果已经能下结论了，只输出：DONE + 一句话理由；"
            + "如果还缺关键信息、值得再讨论一轮，只输出：CONTINUE + 一句话说明还缺什么。"
        )

    @staticmethod
    def meeting_summary_prompt(g: dict[str, Any]) -> str:
        """收口：结论 / 分歧 / 待办（谁做什么）—— 用户要的"最后总结"。"""
        return (
            "你是这场讨论的主持人。把下面的讨论收口成一份**可执行的会议纪要**。" + chr(10)
            + f"【议题】{g.get('meeting_topic') or ''}" + chr(10)
            + "【讨论记录】" + chr(10) + (TeamStore.meeting_transcript_text(g) or "（没有发言）") + chr(10) + chr(10)
            + "按这三段输出（Markdown、简洁、不要客套）：" + chr(10)
            + "1. **结论**：讨论达成的共识是什么（没达成也要写明「未达成共识」）" + chr(10)
            + "2. **分歧**：谁和谁在什么点上不一致（没有就写「无」）" + chr(10)
            + "3. **待办**：谁做什么、什么时候交（没有就写「无」）"
        )

    def has_notice(self, gid: str, task_id: str, needle: str) -> bool:
        """群里这条任务的这类提示**是不是已经发过**了。

        ★ 2026-10-05 用户实测：同一个任务的"⏳ 超过 30 分钟未结束"在群里刷了 15–43 条
        （那个群总共 297 条同类消息）。7a 当初把"每 5 秒一条"改成了"只提醒一次"，
        但**每个看门任务各发各的**：后端重启后 `_recover_group_deliveries` 会再挂一个看门，
        于是同一条任务会有多个看门者、各提醒一次 ⇒ 用户看到刷屏。
        落盘去重是唯一可靠的做法（内存里的标记撑不过重启）。
        """
        if not task_id or not needle:
            return False
        for m in self.feed(gid):
            if str(m.get("task_id") or "") == str(task_id) and needle in str(m.get("text") or ""):
                return True
        return False

    # ═══ 依赖波次（2026-10-05 对标研究的 P0-1）═══
    #   依据：MAST 里"带着错误假设往下做、不问清楚"占 11.7%、"扣着关键信息不说"占 1.7%；
    #   Anthropic 复盘明确说"**彼此依赖很多的任务不适合并行**"，且委派时必须给
    #   "目标 + 输出格式 + 工具 + 边界"。我们此前是一把全并行 ⇒ 程序员在架构师交付前就开工，
    #   只能猜接口（用户实测踩到）。改成：分工单带前置 ⇒ **按波次派**，前置交付后才派下一批，
    #   并把前置的**产物路径 + 摘要**写进它的工作单（不塞全文 —— 免得玩"传话游戏"）。

    def leader_begin(self, gid: str, goal: str, plan: list[dict[str, Any]]) -> dict[str, Any]:
        """把组长的分工单落到群上，返回**第一批可开工的活**。"""
        with self._lock:
            gs = self.groups()
            idx = next((i for i, x in enumerate(gs) if x["id"] == gid), None)
            if idx is None:
                raise ValueError("群不存在")
            g = gs[idx]
            items: list[dict[str, Any]] = []
            # ★ 2026-10-06（真群实测：稍复杂需求那一轮）：**组长会给同一个人派两项** ✓
            #   ⇒ 分工单里两个同名项 ⇒ 后面全部"按名字找人"的地方就乱了 ✗：
            #     · 波次判定把两项当成同一项 ⇒ 出现"前置没交付就开工" ✗（那一轮真的发生了）
            #     · 派发/验收对不上号，同一个人被反复打回（程序员挂了 3 次被判失败）
            #   修法：**先给重复的名字定唯一名**（`名字·2`），再建项 —— 顺序很重要 ✗：
            #   先建项的话，第二项里 `depends_on=["程序员"]` 会被当"依赖自己"抹掉 ⇒ 依赖丢失 ✗
            #   （本班就踩了这个顺序）。派发边界认得 `名字·xxx` 后缀（验收项就是这么做的 ✓）。
            _seq: dict[str, int] = {}
            for it in plan:
                nm = str(it.get("name") or "").strip()
                task = str(it.get("task") or "").strip()
                if not nm or not task:
                    continue
                _seq[nm] = _seq.get(nm, 0) + 1
                uniq = nm if _seq[nm] == 1 else f"{nm}·{_seq[nm]}"
                deps = [str(d).strip().lstrip("@") for d in (it.get("depends_on") or []) if str(d).strip()]
                # 依赖里的旧名字**不改**：组长写"第二项接第一项"时，那个名字指的就是**第一项** ✓
                # （若改成 ·2 反而会变成"依赖自己"被抹掉 ⇒ 依赖丢失 ✗，本班踩过）
                items.append({
                    "name": uniq, "task": task[:1000],
                    "output": str(it.get("output") or "").strip()[:400],
                    "depends_on": [d for d in dict.fromkeys(deps) if d != uniq],
                    "status": "pending", "reply": "", "attachments": [],
                })
            if not items:
                raise ValueError("分工单是空的")
            # 前置指向不在分工单里的名字 ⇒ 去掉（否则永远等不到，活活卡死）
            known = {i["name"] for i in items}
            for it in items:
                it["depends_on"] = [d for d in it["depends_on"] if d in known]
            g["leader_goal"] = str(goal or "").strip()[:2000]
            g["leader_plan"], g["leader_wave"] = items, 0
            g.pop("leader_deadlock_released", None)
            gs[idx] = g
            self._write(self.root / "groups.json", gs)
        return self.leader_ready(gid)

    def leader_ready(self, gid: str) -> dict[str, Any]:
        """算出"现在能开工的"与"还被挡着的"。

        ★ 防死锁：如果没有任何 pending 项可开工（前置成环、或前置名字对不上），
        就把剩下的**全部放行**并在群里标注原因 —— 宁可顺序不完美，也不能让整批活永远停着。
        """
        with self._lock:
            gs = self.groups()
            idx = next((i for i, x in enumerate(gs) if x["id"] == gid), None)
            if idx is None:
                return {"ready": [], "waiting": [], "blocked": [], "done": 0, "total": 0, "wave": 0}
            g = gs[idx]
            items = list(g.get("leader_plan") or [])
            if not items:
                return {"ready": [], "waiting": [], "blocked": [], "done": 0, "total": 0, "wave": 0}
            by_name = {i["name"]: i for i in items}
            ready: list[dict[str, Any]] = []
            waiting: list[dict[str, Any]] = []
            blocked: list[dict[str, Any]] = []
            for it in items:
                if it["status"] != "pending":
                    continue
                deps = [by_name[d] for d in it["depends_on"] if d in by_name]
                bad = [d["name"] for d in deps if d["status"] in ("failed", "blocked")]
                if bad:
                    it["status"] = "blocked"
                    blocked.append({"name": it["name"],
                                    "why": "前置 " + "、".join("@" + b for b in bad) + " 没做成"})
                    continue
                if all(d["status"] == "done" for d in deps):
                    ready.append(it)
                else:
                    waiting.append({"name": it["name"],
                                    "after": [d["name"] for d in deps if d["status"] != "done"]})
            pending_left = [i for i in items if i["status"] == "pending"]
            still_running = [i for i in items if i["status"] == "running"]
            # ★ 防死锁只在"**没有任何活在跑**、却也没有活能开工"时才触发
            #   （本班实测教训：第一版没看 running ⇒ 第一批还在跑、第二批在等前置时就被判成死锁，
            #    直接把依赖关系全部放行了 —— 那就等于没做波次）
            if not ready and pending_left and not still_running:
                released = [i["name"] for i in pending_left]
                ready.extend(pending_left)
                g["leader_deadlock_released"] = released
            for it in ready:
                it["status"] = "running"
            g["leader_plan"] = items
            if ready:
                g["leader_wave"] = int(g.get("leader_wave") or 0) + 1
            gs[idx] = g
            self._write(self.root / "groups.json", gs)
        return {"ready": ready, "waiting": waiting, "blocked": blocked,
                "done": sum(1 for i in items if i["status"] == "done"), "total": len(items),
                "wave": int(g.get("leader_wave") or 0),
                "released": list(g.get("leader_deadlock_released") or [])}

    def leader_finish_item(self, gid: str, name: str, status: str,
                           reply: str = "", attachments: list[str] | None = None) -> dict[str, Any]:
        """某一项交付（done）或失败（failed）⇒ 更新状态并重算下一批。"""
        with self._lock:
            gs = self.groups()
            idx = next((i for i, x in enumerate(gs) if x["id"] == gid), None)
            if idx is None:
                return {"ready": [], "waiting": [], "blocked": [], "done": 0, "total": 0, "wave": 0}
            g = gs[idx]
            items = list(g.get("leader_plan") or [])
            for it in items:
                if it["name"] == name and it["status"] in ("running", "pending"):
                    it["status"] = status
                    # ★★ 2026-10-06（**今天大半失败的单一根因，第三把刀** ✗✗）：
                    #   这里原来 `[:800]` —— 而**验收人的提示词读的正是这个 `it["reply"]`** ✓✓。
                    #   连同看门那边的 `[:800]` 与提示词里的 `[:500]`，交付正文被砍**三刀** ✗；
                    #   而小节顺序是「改动文件 → 自测命令 → **真实输出**」✓
                    #   ⇒ 输出**永远在刀口之前被丢掉** ✓ ⇒ 验收人每次都说"交付摘要为空" ✓
                    #   **而它说的是实话** ✓（前面几轮我们一直怪它太苛刻 ✗）。
                    #   ⇒ 统一走 `DELIVERY_TEXT_MAX` ✓（一个常量，杜绝"第四把刀" ✓）。
                    it["reply"] = str(reply or "")[:DELIVERY_TEXT_MAX]
                    it["attachments"] = list(attachments or [])[:20]
            g["leader_plan"] = items
            gs[idx] = g
            self._write(self.root / "groups.json", gs)
        return self.leader_ready(gid)

    def leader_pending_task_ids(self, gid: str) -> list[tuple[str, str]]:
        """还在跑、且已经派出任务的项 —— 给"补认领"用。

        ★ 为什么需要（Jev 判定指出的头号漏洞）：推进批次原本**只靠看门任务**，
        而后端一重启看门就没了 ⇒ 那一项的交付永远不被认领 ⇒ 依赖它的活永远不动。
        现在可以由调用方拿这份清单去核对任务终态，把漏掉的交付补上。
        """
        g = self.get_group(gid) or {}
        return [(i["name"], str(i.get("task_id") or ""))
                for i in (g.get("leader_plan") or [])
                if i.get("status") == "running" and i.get("task_id")]

    def leader_attach_task(self, gid: str, name: str, task_id: str) -> None:
        """记下"这一项派出去的任务 id"——看门任务靠它认领回来推进批次的。"""
        with self._lock:
            gs = self.groups()
            idx = next((i for i, x in enumerate(gs) if x["id"] == gid), None)
            if idx is None:
                return
            g = gs[idx]
            items = list(g.get("leader_plan") or [])
            for it in items:
                if it["name"] == name:
                    it["task_id"] = task_id
            g["leader_plan"] = items
            gs[idx] = g
            self._write(self.root / "groups.json", gs)

    def leader_item_by_task(self, gid: str, task_id: str) -> dict[str, Any] | None:
        g = self.get_group(gid) or {}
        for it in (g.get("leader_plan") or []):
            if it.get("task_id") == task_id:
                return it
        return None

    # ═══ 验收环节（P0-2）═══
    #   依据：MAST 里"任务验证"占 21.3%（没验证/验证不全 6.8% + 验证做错 6.7%），
    #   而论文干预实验显示"在低层检查之外**再加一道高层目标验收**"能带来 **+15.6%**
    #   （原文 Insight 2：需要多级验证）。所以这里强制：**由另一个成员**按两级验收，
    #   不合格**打回**给负责人并附意见（有轮次上限，防无限来回）。
    MAX_VERIFY_ROUNDS = 2

    @staticmethod
    def verification_prompt(goal: str, item: dict[str, Any], low: str) -> str:
        """验收提示词：讲清"两级验收"，并要求**打回时必须说清改什么**。"""
        art = "、".join(item.get("attachments") or []) or "（没给文件）"
        return (
            "你是验收人（**不是**干这活的人）。按**两级**验收下面这项交付：别客气、别走过场。" + chr(10)
            + f"【总目标】{goal}" + chr(10)
            + f"【这一项要求】{item.get('task')}" + chr(10)
            + f"【交付要求】{item.get('output') or '（未写）'}" + chr(10)
            + f"【交付的产物】{art}" + chr(10)
            # ★★ 2026-10-06（**今天大半失败的单一根因** ✗✗）：这里原来 `[:500]` ——
            #   加上看门那边的 `[:800]`，交付正文被砍两刀 ✓；而小节顺序是
            #   「改动文件 → 自测命令 → **真实输出**」✓ ⇒ 输出**正好落在刀口之后** ✗ ⇒
            #   验收人永远看不到输出 ✓ 于是每次都说"没贴输出" ✓ **它说的是实话** ✓✓。
            #   ⇒ 放宽到 2500 字（够放"文件清单 + 命令 + 一段真输出" ✓）。
            + f"【交付摘要】{' '.join(str(item.get('reply') or '').split())[:DELIVERY_TEXT_MAX]}" + chr(10)
            + f"【机器已查】{low}" + chr(10) + chr(10)
            + "★ 你**不需要动手**（不要去列目录、不要去跑命令）：文件在不在、是不是空文件，"
            "机器已经查过了（见上）。你只判断**高层**：这一步是否真的达成了**它自己该达成的**。" + chr(10)
            # ★★ 2026-10-06（**评测台连跑三轮抓到的结构性 bug** ✗✗）：
            #   现场：组长把总目标拆成"架构 / 实现 / 测试"三项 ✓，实现那项的**工作单范围**写着
            #   "只写主程序、不写测试" ✓ —— 可验收人拿**总目标**当尺子 ✗（总目标里写着"顺手写 test_xxx.py"）
            #   ⇒ **永远判"没写测试"** ⇒ 打回 3 次 ⇒ 判失败 ⇒ 依赖它的活被挡住 ✓✓
            #   实测连输三轮（46.5 万 / 12.4 万 / 12.8 万 tok ✓ 全栽在这上面 ✗）。
            #   ⇒ 规矩说死：**只按「这一项要求」判** ✓，总目标只作背景 ✓。
            + "★★ **只按「这一项要求」判**（上面那行）✓，总目标只作背景 —— "
            "**别拿总目标里属于别人那一步的东西来判这一步** ✗"
            "（典型：总目标写「顺手写测试」，而这一项的工作单写着「只写主程序」⇒ "
            "**没写测试不算这一步的错** ✓，别打回 ✗）。" + chr(10)
            + "★★ 但如果你发现**总目标里要求的某个东西，分工单里没有任何一项认领** ✓"
            "（谁都不做它 ⇒ 项目必然交不出来）—— 那是**组长拆解**的问题 ✓，"
            "写进 `defects` 指给**组长** ✓，**不要**因此打回当前这一步 ✗。" + chr(10)
            # ★★ 2026-10-06（第 10 轮真跑：**同一项被打回 3 次** ✗ 每次烧 11–16 万 tok ✓✓）：
            #   现场：最后一次验收意见写着"**在【交付摘要】中**列出改动文件…" ✗ ——
            #   交付里**明明写了**改动文件 ✓，只是**没写在它指定的那个位置/标题下** ✓✓。
            #   这和小节检查那个病**一模一样**（判格式不判内容 ✗），只是发生在高层验收人身上 ✓。
            #   ⇒ 规矩说死：**判"有没有这件事"，不判"写在哪、叫什么"** ✓✓。
            + "★★★ **判内容，不判位置**（这条最要紧 ✓）：交付里**只要有那个信息就算它交了** ✓ —— "
            "**别要求它写在某个标题下 / 某一节里 / 某个字段里** ✗，"
            "**别要求它重抄一遍** ✗（实测：交付里明明写了改动文件，验收人却因为"
            "「没写在【交付摘要】里」连打回 3 次 ⇒ 整项判失败 ✗✓）。" + chr(10)
            + "★★★ 打回之前先问自己一句：**「它到底缺了什么实质内容？」** ✓ —— "
              "如果你答不上来（只是「位置不对」「写法不标准」「没按模板」✗），那就**该判通过** ✓。" + chr(10)
            + "★ 如果你在**前面几步的产物**里发现缺陷（契约没写清、实现与契约不符…），"
            "**别让当前这步背锅** —— 写进 `defects`，系统会打回给那一步的负责人去修。" + chr(10)
            + "只输出一个 JSON 对象（不要调用任何工具）：" + chr(10)
            + '{"pass": true/false, "low": "低层结论一句话（可照抄上面机器已查）", '
            + '"high": "高层结论一句话", '
            + '"evidence": "**必须**引用交付里的原话或产物片段（照抄一小段，别转述）作为判据", '
            + '"defects": [{"owner": "要改的人名", "what": "具体要他改什么"}], '
            + '"fix": "不通过时必须写清要改什么（通过了写空字符串）"}'
        )

    @staticmethod
    def verdict_is_grounded(verdict: dict[str, Any], item: dict[str, Any], low: str) -> bool:
        """★ 验收结论要**引得出证据**，否则当"走过场"处理（Jev 判定指出的头号漏洞：同一个模型互相背书）。

        判据（任意一条成立即算接地）：
          · `evidence` 里有一段 ≥8 个字能在**交付摘要 / 产物清单 / 机器检查结果**里找到
          · 或者 `low`/`high` 里引用了产物名（比如 docs/api.md）
        拿不出证据 ⇒ 调用方按"没通过"处理（宁可严）。
        """
        ev = " ".join(str(verdict.get("evidence") or "").split())
        src = " ".join(str(item.get("reply") or "").split()) + " " + low + " " + \
              " ".join(item.get("attachments") or [])
        if ev:
            seed = "".join(ch for ch in ev if ch.isalnum())
            src_n = "".join(ch for ch in src if ch.isalnum())
            for i in range(0, max(1, len(seed) - 8)):
                if seed[i : i + 8] and seed[i : i + 8] in src_n:
                    return True
        joined = " ".join(str(verdict.get(k) or "") for k in ("low", "high", "evidence"))
        return any(a and a in joined for a in (item.get("attachments") or []))

    @staticmethod
    def parse_defects(verdict: dict[str, Any], known_names: list[str],
                      exclude: str = "") -> list[dict[str, str]]:
        """从验收结论里取出"要打回给谁"的缺陷清单（★ 缺陷回环）。

        现场（2026-10-05 第六轮真群回归）：测试工程师真跑出「36 通过 / 1 失败」，
        把缺陷写进了**自己的交付报告** —— 那 1 个失败是**程序员**产物的问题，
        但系统只验收了"测试这一步交付的报告"，于是**没人去修** ✗。
        所以：让验收人把"别人产物的问题"结构化写进 `defects`，这里取出 → 打回给对应的人。
        只认分工单里真实存在、且不是当前这一步的名字（防误伤、防自打回）。
        """
        out: list[dict[str, str]] = []
        raw = verdict.get("defects")
        if isinstance(raw, dict):
            raw = [raw]
        if not isinstance(raw, list):
            return out
        for d in raw:
            if not isinstance(d, dict):
                continue
            owner = str(d.get("owner") or d.get("name") or d.get("谁") or "").strip().lstrip("@")
            what = str(d.get("what") or d.get("fix") or d.get("要改什么") or "").strip()
            if not owner or not what or owner not in known_names or owner == exclude:
                continue
            out.append({"owner": owner, "what": what[:400]})
        merged: dict[str, list[str]] = {}
        for d in out:
            merged.setdefault(d["owner"], []).append(d["what"])
        return [{"owner": k, "what": "；".join(dict.fromkeys(v))[:600]} for k, v in merged.items()]

    def leader_mark_running(self, gid: str, name: str) -> dict[str, Any]:
        """★ 把某一项改回"进行中"（**续跑**用）。

        为什么需要：续跑是**同一个任务**重跑一遍，而那一项在分工单里可能已经是 failed/partial
        ⇒ 看门与对账会立刻判定"活已结束"并把整批收工 —— 用户看到"收工了"，实际它还在干
        （2026-10-05 全量试跑实测）。这里只改状态，**不加轮次、不碰下游**（续跑不是重做）。
        """
        with self._lock:
            gs = self.groups()
            idx = next((i for i, x in enumerate(gs) if x["id"] == gid), None)
            if idx is None:
                return {}
            g = gs[idx]
            items = list(g.get("leader_plan") or [])
            target = next((i for i in items if i["name"] == name), None)
            if target is None:
                return {}
            target["status"] = "running"
            target.pop("verdict", None)
            g["leader_plan"] = items
            gs[idx] = g
            self._write(self.root / "groups.json", gs)
        return {"ok": True, "name": name}

    def leader_reopen(self, gid: str, owner: str, what: str) -> dict[str, Any]:
        """★ 缺陷回环：把**别人的**那一项重新打开重做，并让它后面依赖它的项回到待办（复测/重做）。

        与 `leader_reroll`（验收打回**当前**这一项）的区别：这里是"下游发现上游有问题"。
        级联只走一层（依赖它的项回到 pending 并标注复测），防连锁雪崩。
        """
        with self._lock:
            gs = self.groups()
            idx = next((i for i, x in enumerate(gs) if x["id"] == gid), None)
            if idx is None:
                return {}
            g = gs[idx]
            items = list(g.get("leader_plan") or [])
            target = next((i for i in items if i["name"] == owner), None)
            if target is None:
                return {}
            reopened = [owner]
            target["status"] = "pending"
            target["reroll_advice"] = str(what or "")[:600]
            target["rounds"] = int(target.get("rounds") or 0) + 1
            target.pop("task_id", None)
            for it in items:
                if it is target:
                    continue
                if owner in (it.get("depends_on") or []) and it["status"] in ("done", "failed", "blocked"):
                    it["status"] = "pending"
                    it["reroll_advice"] = (
                        f"前置 @{owner} 因缺陷重修过，请**复核并复测**；" + str(it.get("reroll_advice") or "")
                    )[:600]
                    it.pop("task_id", None)
                    reopened.append(it["name"])
            g["leader_plan"] = items
            gs[idx] = g
            self._write(self.root / "groups.json", gs)
        ready = self.leader_ready(gid)
        return {**ready, "reopened": reopened}

    @staticmethod
    def parse_verdict(raw: str) -> dict[str, Any]:
        """解析验收结论。模型不听话时也尽量认；**认不出来一律当没通过**（宁可严）。"""
        text = (raw or "").strip()
        depth, start = 0, -1
        for i, ch in enumerate(text):
            if ch == "{":
                if depth == 0:
                    start = i
                depth += 1
            elif ch == "}" and depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    try:
                        obj = json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        continue
                    if isinstance(obj, dict) and "pass" in obj:
                        return {"pass": bool(obj.get("pass")),
                                "low": str(obj.get("low") or "")[:300],
                                "high": str(obj.get("high") or "")[:300],
                                # ★ evidence 一定要收：接地校验靠它（我第一版漏了 → 所有"通过"都被误判成没证据）
                                "evidence": str(obj.get("evidence") or "")[:400],
                                # ★ 缺陷回环：验收人指出"别人产物的问题"时，要把它带出来（打回给对应的人）
                                "defects": obj.get("defects") if isinstance(obj.get("defects"), list) else [],
                                "fix": str(obj.get("fix") or "")[:400]}
        low = text.lower()
        if any(k in low for k in ("不通过", "打回", "不合格", "fail", "false")):
            return {"pass": False, "low": "", "high": text[:200], "fix": text[:200]}
        if ("通过" in text or "合格" in text) or "pass" in low:
            return {"pass": True, "low": "", "high": text[:200], "fix": ""}
        # ★ 解析不出来 ≠ 活不行（真群实测：验收人有时返回一条 shell 命令 —— 它想自己去看看文件）。
        #   打上 unparsed 标记，调用方按"验收人没给出结论"处理（低层过了就放行，不让人白干）。
        return {"pass": False, "low": "", "high": "验收人没给出可解析的结论", "unparsed": True,
                "fix": "请重做并给出可验收的产物（文件或可运行结果）"}

    def leader_set_verdict(self, gid: str, name: str, passed: bool, note: str) -> dict[str, Any]:
        """记下验收结论；打回时轮次 +1（超上限由调用方判失败 ⇒ 依赖它的活会被挡住）。"""
        with self._lock:
            gs = self.groups()
            idx = next((i for i, x in enumerate(gs) if x["id"] == gid), None)
            if idx is None:
                return {}
            g = gs[idx]
            items = list(g.get("leader_plan") or [])
            for it in items:
                if it["name"] == name:
                    it["verdict"] = "pass" if passed else "reject"
                    it["verdict_note"] = str(note or "")[:600]
                    if passed:
                        it["status"] = "done"
                    else:
                        it["rounds"] = int(it.get("rounds") or 0) + 1
            g["leader_plan"] = items
            gs[idx] = g
            self._write(self.root / "groups.json", gs)
        return self.leader_ready(gid)

    def leader_reroll(self, gid: str, name: str, feedback: str) -> dict[str, Any]:
        """打回重做：把这一项放回 pending 并附验收意见（下一轮派发时带上）。"""
        with self._lock:
            gs = self.groups()
            idx = next((i for i, x in enumerate(gs) if x["id"] == gid), None)
            if idx is None:
                return {}
            g = gs[idx]
            items = list(g.get("leader_plan") or [])
            for it in items:
                if it["name"] == name:
                    it["status"] = "pending"
                    it["reroll_advice"] = str(feedback or "")[:600]
                    # ★ 2026-10-06：**记下被打回几次** ✓ —— 第 2 次起工作单要升级成
                    #   "只做这一件事 + 给出照抄格式"（见 `_leader_handoff` ✓）。
                    #   （此前没有这个计数，只有"打回 3 次就判失败"那道上限 ✓ 在 main.py 里 ✓）
                    it["rerolls"] = int(it.get("rerolls") or 0) + 1
                    it.pop("task_id", None)
            g["leader_plan"] = items
            gs[idx] = g
            self._write(self.root / "groups.json", gs)
        return self.leader_ready(gid)

    def leader_handoff_text(self, g: dict[str, Any], item: dict[str, Any]) -> str:  # noqa: D401
        """（见上：完整工作单 = 目标 + 交付要求 + 边界 + 前置产物）

        ★ 2026-10-06 加"经验注入" ✓：派活时把**这类活过去栽过的地方**带上 ✓ ——
          实测同一个坑（交付里不贴真实输出）反复出现 ✓ 每次都重烧十几万 tok ✗，
          而"上次为什么栽"系统本来就知道 ✓（验收意见白纸黑字 ✓）只是从没带给下一个人 ✗。
          相关性/条数/字数都有硬上限（见 `lessons.py` ✓）—— 没相关的就什么都不加 ✓。
        """
        text = TeamStore._leader_handoff(g, item)
        try:
            tip = self.lessons.render(f"{g.get('leader_goal') or ''} {item.get('task') or ''}")
        except Exception:                                   # noqa: BLE001
            tip = ""                                        # 经验库坏了不能拖垮派活 ✗
        if not tip:
            return text
        return text + chr(10) + tip

    def record_lesson(self, task_text: str, reason: str, who: str = "") -> bool:
        """记一条"这类活栽在哪" ✓（验收打回/判失败时调用 ✓ 见 `main._verify_delivery` ✓）。"""
        try:
            return self.lessons.record(task_text, reason, who=who)
        except Exception:                                   # noqa: BLE001
            return False

    @staticmethod
    def _leader_handoff(g: dict[str, Any], item: dict[str, Any]) -> str:
        """给这一项拼完整工作单：目标 + 交付要求 + 边界 + **前置交付**（路径与摘要，不塞全文）。"""
        lines = [f"【总目标】{g.get('leader_goal') or ''}", f"【你这一步】{item.get('task')}"]
        # ★ 2026-10-06 降本（终验跑实测：小项目 79 万 tok，大头是"打回后重做"）：
        #   把**交付格式**提到工作单最前面 —— 实测"缺必需小节"打回了 2 次，
        #   而要求原本写在最后，模型经常漏读 ⇒ 前置能省掉一整轮重做。
        # ★★ 2026-10-06：**分工单只有一项 = 一个人干完全程** ⇒ 两套小节都要 ✓
        #   （评测台第四轮：单干那项又实现又写测试，却只被要求"改动文件/自测命令" ✗
        #     ⇒ 它交的「用例清单」被判"没交" ⇒ 打回 3 次 ⇒ 失败 ✓✓）
        _solo = len([i for i in (g.get("leader_plan") or [])]) <= 1
        _need = TeamStore.required_sections(str(item.get("task") or ""),
                                            str(item.get("output") or ""), solo=_solo)
        if _need:
            lines.append("【交付格式（先看这个：必须按小节写，缺一节就整份打回）】")
            # ★★ 2026-10-06（第 12 轮稳定性复跑抓到的 ✗）：**一开始就把模板给它** ✓✓
            #   实测反复栽在同一处：它**确实跑了**测试 ✓（工作区里有 .pyc、sample.txt ✓）
            #   但交付里**只写"测试通过"** ✗，不贴终端原文 ⇒ 验收人连打回 3 次 ⇒ 整项失败 ✓✓
            #   （第 2 次打回时才给格式模板太晚了 ✗ 那两轮已经烧掉了 ✓）。
            _hint = {
                "自测命令": "（**必须连真实输出一起贴** —— 照抄终端里出现的，别总结、别转述 ✗）",
                "运行结果": "（**照抄终端原文**，别只写「通过/不通过」✗）",
                "改动文件": "（列出文件名 + 一句话说明改了什么 ✓）",
                "用例清单": "（每条一行：编号 + 场景 + 期望 ✓）",
            }
            for _t in _need:
                lines.append(f"· {_t}{_hint.get(_t, '')}")
            # 有"要贴输出"这类小节 ⇒ 直接给一份**照着填**的骨架 ✓（比讲道理管用 ✓）
            if any(_t in _need for _t in ("自测命令", "运行结果")):
                lines.append("【照这个骨架填（把尖括号换成你自己的真东西 ✓ 别删小节 ✗）】")
                lines.append("```")
                for _t in _need:
                    lines.append(f"## {_t}")
                    if _t in ("自测命令", "运行结果"):
                        lines.append("$ <你跑的那条命令>")
                        lines.append("<终端里原样出现的输出，几行就够，不要改写>")
                    elif _t == "改动文件":
                        lines.append("- <文件名>：<一句话说明改了什么>")
                    elif _t == "用例清单":
                        lines.append("1. <场景> → <期望>")
                    else:
                        lines.append("<照实写>")
                lines.append("```")
        if item.get("reroll_advice"):
            # ★ 打回重做：把验收意见顶到最前面，别让它又交一版一样的
            lines.append("【上一次被打回的原因 —— 这次必须改掉】" + str(item["reroll_advice"])[:400])
            # ★★ 2026-10-06（评测台第二轮真跑抓到的浪费 ✗）：
            #   同一个"贴原始输出"的要求**连打回 3 次** ✓ 每次都整份重做 ✗（那一步 11 次调用 / 9 万 tok ✓），
            #   因为三轮说的**是同一句话** —— 模型没懂"到底怎么贴" ✓。
            #   ⇒ 第 2 轮起把话说到底：**只做这一件事** + **给出照抄的格式** + **明确可以省掉什么** ✓。
            if int(item.get("rerolls") or 0) >= 2:
                lines.append(
                    "【这一轮只做这一件事（别重写别的）】" + chr(10)
                    + "· 只补上面那条被打回的东西 ✓，其余内容**照旧保留**（别整份重写 ✗）；" + chr(10)
                    + "· 要贴「实际输出」就**照这个格式贴**（命令 + 输出，原样复制）：" + chr(10)
                    + "  ```" + chr(10) + "  $ <你跑的命令>" + chr(10) + "  <终端里真实出现的输出>" + chr(10) + "  ```" + chr(10)
                    + "· **跑不起来/太麻烦的部分可以省掉** ✓（写一句「这一项没跑」就行 ✓）——"
                      "但**必须有一处**是原样粘贴的真实输出 ✓；" + chr(10)
                    + "· 别新增文件、别改设计、别顺便优化 ✗。"
                )
            # ★ 降本第二刀：**把上一版交付带回来**（截断）——否则它只能凭记忆重写一遍，
            #   既贵又容易丢掉上次做对的部分（实测：重做那一版烧的 token 常常翻倍）。
            _prev = str(item.get("reply") or "").strip()
            if _prev:
                lines.append("【你上一版交了什么（在它基础上改，别从头重写）】")
                lines.append(_prev[:1500] + ("…（略）" if len(_prev) > 1500 else ""))
        if item.get("output"):
            lines.append(f"【交付要求】{item['output']}（有产物就写进工作区，结尾给出文件路径）")
        else:
            lines.append("【交付要求】给出下一位能直接用的结果；有产物就写进工作区并给出路径。")
        ups = [i for i in (g.get("leader_plan") or [])
               if i["name"] in (item.get("depends_on") or []) and i.get("status") == "done"]
        if ups:
            lines.append("【上一步的交付（直接用，别再猜）】")
            for u in ups:
                paths = "、".join(u.get("attachments") or []) or "（没给文件，见下面摘要）"
                lines.append(f"· @{u['name']} 的产物：{paths}")
                # ★ 结构化交付（2026-10-05 第 4 条）：**先给固定小节**，再给一句摘要 ——
                #   依据 MetaGPT：agents communicate through documents rather than dialogue；
                #   大段白话在传递中会失真（电话游戏效应）。小节名是约定，模型照着填。
                secs = TeamStore.pick_sections(str(u.get("reply") or ""))
                for title, body in secs:
                    lines.append(f"  ▸ {title}：{body[:400]}")
                one = " ".join(str(u.get("reply") or "").split())
                if one and not secs:
                    lines.append(f"  摘要：{one[:200]}")
            lines.append("要求：**基于上面的交付继续推进**；若它缺了关键信息，结尾写明"
                         "「我需要 @某人 补充：…」，不要自己假设。")
        else:
            lines.append("【边界】只做你这一步，别替别人做他那步；不确定处写明「需要谁补充什么」。")
        # （交付格式要求已**前置到工作单开头** —— 2026-10-06 降本：写在末尾时模型常漏读，
        #   实测"缺小节"打回了 2 次，每次都得整份重做。这里不再重复。）
        return chr(10).join(lines)

    # ═══ 结构化交付（2026-10-05 第 4 条，依据 MetaGPT 的"结构化输出"）═══
    #   为什么：群聊式协作在传递中失真（电话游戏效应）；固定小节 = 可检查、可交接。
    _DESIGN_SECTIONS = ("文件清单", "接口定义", "数据结构")
    _IMPL_SECTIONS = ("改动文件", "自测命令")
    _TEST_SECTIONS = ("用例清单", "运行结果")

    @staticmethod
    def required_sections(task: str, output: str = "", solo: bool = False) -> list[str]:
        """按这一步的活，要求哪些小节（空列表 = 不强制）。

        ★ 顺序有讲究（2026-10-06 实测）：**先判"实现"再判"测试"** ——
          任务书常写成"按契约实现 todo.py，并真跑一遍自测"（两类关键词都有 ✗），
          而它的**主产物是代码** ⇒ 该要「改动文件 / 自测命令」，
          不该要「用例清单 / 运行结果」（那是测试那一步的活）。
        """
        hay = f"{task} {output}"
        # ★★ 2026-10-06（评测台第二轮抓到的**自相矛盾** ✗）：
        #   "确认性验收"的任务书里写着「**别新写验收脚本** ✗、≤5 条断言都不必 ✓」，
        #   底下却又按 `_TEST_SECTIONS` 要它交「用例清单」✗ ⇒ 它**必然**被"缺小节"打回 ✓
        #   （实跑现场：终验交付被判"缺必需小节：用例清单" ✓ 又白跑一轮 ✗）。
        #   ⇒ 确认性验收只要**一节：验收结论** ✓（它给的正是"跑了一下 + 一句话结论" ✓）。
        if "确认性" in hay and "项目验收" in hay:
            return ["验收结论"]
        # ★ 完整终验同理：它的任务书里既有"验收"又有"别顺手重写**实现**"这类字眼 ✓，
        #   按关键词排下去会被判成"实现类" ✗（本班验证时当场撞到 ✓）⇒ 验收类**整体提前**判 ✓。
        if "项目验收" in hay:
            return ["验收结论", "运行结果"]        # 它必须给出"实际命令 + 真实输出" ✓
        # ★★ 2026-10-06（评测台第四轮抓到的 ✗）：**一个人单干时，两套小节都要** ✓
        #   现场：小活只派了一项 ⇒ 同一个人**又实现又写测试** ✓，它交的是「用例清单（11 条）」✓
        #   而系统只按"实现类"要「改动文件/自测命令」✗ ⇒ 判它"交付摘要为空" ⇒ 打回 3 次 ⇒ 失败 ✓✓
        #   `solo` 由调用方给（分工单只有一项 = 一个人干完全程 ✓）。
        if solo:
            return list(dict.fromkeys(TeamStore._IMPL_SECTIONS + TeamStore._TEST_SECTIONS))
        # ★ 关键词要**先去掉否定短语** ✗ —— "（只写主程序、**不写测试**）"里带"测试" ⇒
        #   会把实现项误判成"两样都干"（本班验证时当场撞到 ✓）。
        for _neg in ("不写测试", "别写测试", "不要写测试", "不做测试", "不写用例", "不跑测试"):
            hay = hay.replace(_neg, "")
        _impl = any(k in hay for k in ("实现", "编码", "写代码", "开发", "改造", "修复"))
        _test = any(k in hay for k in ("测试", "用例", "自检", "自测", "真跑", "跑一遍", "验收"))
        if _impl and _test:
            return list(dict.fromkeys(TeamStore._IMPL_SECTIONS + TeamStore._TEST_SECTIONS))
        # 注意：**别把「脚本」算作实现关键词** —— "写自检脚本"是测试那一步的活 ✗
        if _impl:
            return list(TeamStore._IMPL_SECTIONS)
        if _test:
            return list(TeamStore._TEST_SECTIONS)
        if any(k in hay for k in ("设计", "架构", "契约", "接口", "方案", "数据")):
            return list(TeamStore._DESIGN_SECTIONS)
        return []

    @staticmethod
    def pick_sections(reply: str) -> list[tuple[str, str]]:
        """从一段交付里挑出"带标题的小节"（标题含关键字即可，`##`/`**`/`▸` 都认）。

        返回 [(标题, 正文摘要)]；挑不出来就返回空（调用方退回"给一句摘要"）。
        """
        text = str(reply or "")
        if not text:
            return []
        keys = ("文件清单", "接口定义", "数据结构", "改动文件", "新增文件", "自测命令",
                "运行结果", "用例清单", "验收", "结论")
        out: list[tuple[str, str]] = []
        lines = text.splitlines()
        for i, ln in enumerate(lines):
            bare = ln.strip().lstrip("#*->·▸ \t")
            for k in keys:
                if bare.startswith(k) or (k in bare and len(bare) < 40):
                    body = " ".join(x.strip() for x in lines[i + 1:i + 6] if x.strip())
                    out.append((k, body[:400] or "（见交付正文）"))
                    break
        seen: set[str] = set()
        uniq: list[tuple[str, str]] = []
        for t, b in out:
            if t not in seen:
                seen.add(t)
                uniq.append((t, b))
        return uniq[:6]

    @staticmethod
    @staticmethod
    def missing_sections(reply: str, need: list[str]) -> list[str]:
        """交付里**缺**哪些必需小节（给验收的低层用：缺了就是不合格，直接打回）。

        ★★ 2026-10-06 大改（**8 轮真跑逼出来的** ✗✗）：**从"查词"改成"查实质"** ✓。

        为什么改：这条检查一整天误判了 **4 次** ✓ ——
        实现类/测试类判错 ✓ 单干要不要并集 ✓ "不写测试"被当关键词 ✓
        最后一次更典型：工作区里 `wordcount.py`、`test_wordcount.py` **都在** ✓、
        交付里也写了改动与测试 ✓，可就因为它的小节标题**没逐字用我那四个词** ✗ ⇒
        判"没交" ⇒ 打回 3 次 ⇒ **整项失败** ✓✓。

        这也和公开经验一致 ✓：用评分标准验证智能体时，
        **"点名具体的工具 / 文件格式 / 运行时"正是被明确点名的错误做法** ✗
        （KDD AgenticAI Evaluation 那篇讲 rubric 验证的论文里就写着——判**实质**，不判**叫法** ✓）。

        ⇒ 现在的判据：**名字对得上 ✓ 或者 有那个"实质"** ✓ 都算交 ✓：
          · 改动文件   ⇒ 文本里出现**文件路径/文件名**（`a.py`、`docs/x.md`…）✓
          · 自测命令   ⇒ 有**命令行样式的行**（`$ …`、`python xxx.py`、`pytest`…）✓
          · 运行结果   ⇒ 有**输出样式的块**（代码块 + 非命令行，或"通过/失败/退出码"字样）✓
          · 用例清单   ⇒ 有**多条编号/列表项**（≥2 条）✓
          · 设计三节   ⇒ 有路径 / 有函数签名 / 有字段表 ✓
        """
        if not need:
            return []
        text = str(reply or "")
        if not text.strip():
            return list(need)

        has_path = bool(re.search(r"[\w./\\-]+\.(py|md|json|txt|html|js|ts|ya?ml|toml|cfg|ini|csv)\b",
                                  text, re.I))
        lines = [ln.strip() for ln in text.splitlines()]
        has_cmd = any(re.match(r"^(\$|>|PS>|>>>)?\s*(python|py|pytest|node|npm|npx|git|pip|uv|"
                               r"bash|sh|powershell|pwsh|\.\/|\.\\).{0,120}", ln, re.I)
                       for ln in lines if ln)
        has_output = bool(re.search(r"(退出码|exit\s*code|EXIT=|通过|失败|OK\b|PASS\b|Traceback|"
                                    r"输出[:：]|stdout|stderr|结果[:：])", text, re.I))
        has_list = len(re.findall(r"(?m)^\s*(?:[-*·▸]|\d+[.、)])\s*\S", text)) >= 2
        has_signature = bool(re.search(r"\w+\s*\([^)]*\)\s*(->|:)?", text)) or "def " in text

        substance = {
            "改动文件": has_path,
            "文件清单": has_path,
            "新增文件": has_path,
            "自测命令": has_cmd,
            "运行结果": has_output or has_cmd,
            "用例清单": has_list or has_output,
            # ★ 接口/数据结构**只认实质、不认路径** ✗ —— 本班第一版把"有 .py 路径"也算作
            #   "写了接口定义" ⇒ 老测试当场逮到（只列了文件清单却不算缺接口 ✗）⇒ 收紧 ✓
            "接口定义": has_signature,
            "数据结构": has_list or ("{" in text and "}" in text),
            "验收结论": True,          # 确认性验收：只要说了话就算（它本来就只有一句话 ✓）
        }
        miss = []
        for k in need:
            if k in text or substance.get(k, False):
                continue
            # 近义写法也认（模型不一定逐字照抄标题）
            alt = {"改动文件": ("新增文件", "修改文件", "文件清单", "涉及文件"),
                   "自测命令": ("自测", "验证命令", "运行命令", "测试命令"),
                   "运行结果": ("实测结果", "执行结果", "测试结果", "运行输出"),
                   "用例清单": ("测试用例", "用例", "断言"),
                   "接口定义": ("接口", "API", "签名"),
                   "文件清单": ("文件列表", "涉及文件"),
                   "验收结论": ("验收", "结论", "通过", "不通过"),
                   "数据结构": ("结构", "字段", "schema", "JSON")}.get(k, ())
            if any(a in text for a in alt):
                continue
            miss.append(k)
        return miss

    def leader_state(self, gid: str) -> dict[str, Any]:
        g = self.get_group(gid) or {}
        items = list(g.get("leader_plan") or [])
        def n(s: str) -> int:
            return sum(1 for i in items if i["status"] == s)
        return {"total": len(items), "done": n("done"), "failed": n("failed"),
                "blocked": n("blocked"), "running": n("running"), "pending": n("pending"),
                "items": items}          # ★ 带上明细：收口判定要用（项目验收门，2026-10-05）

    def leader_append_item(self, gid: str, name: str, task: str, output: str,
                           depends_on: list[str] | None = None,
                           kind: str = "") -> dict[str, Any] | None:
        """往分工单**追加一项**（★ 项目验收门用它：把"项目验收"作为最后一项）。

        为什么要走分工单而不是新造一套机制：追加进去之后，
        现成的**波次**（等前置交付才开工）与**两级验收**（低层查产物 / 高层判达成）全都能复用 ✓。
        """
        with self._lock:
            gs = self.groups()
            idx = next((i for i, x in enumerate(gs) if x["id"] == gid), None)
            if idx is None:
                return None
            g = gs[idx]
            items = list(g.get("leader_plan") or [])
            if any(i.get("kind") == kind and kind for i in items):
                return None                       # 同类项只加一次（幂等）
            item = {"name": name, "task": task, "output": output, "status": "pending",
                    "depends_on": [d for d in (depends_on or []) if d != name],
                    "rounds": 0, "kind": kind}
            items.append(item)
            g["leader_plan"] = items
            gs[idx] = g
            self._write(self.root / "groups.json", gs)
        return item

    @staticmethod
    def relay_prompt(goal: str, upstream_name: str, upstream_text: str, pos: int, total: int) -> str:
        """给下一棒的完整工作单：目标 + 上游交付（这就是"接力"与"并行"的根本区别）。"""
        tag = "先手" if pos == 1 else f"第 {pos}/{total} 棒"
        if pos == 1:
            return (
                f"【接力·{tag}】总目标：{goal}" + chr(10)
                + "你是第一棒：把这件事推进到你这一步该有的完成度，产出要能让下一位直接接手。"
            )
        return (
            f"【接力·{tag}】总目标：{goal}" + chr(10)
            + f"上一位（{upstream_name}）刚交付了：" + chr(10)
            + (upstream_text or "（没有文字交付）") + chr(10) + chr(10)
            + "你的任务：**在它的基础上继续推进**（不要重复它已经做完的部分），"
            + "完成后同样给出可直接交接的交付说明。"
        )

    @staticmethod
    def parse_leader_plan(raw: str) -> list[dict[str, str]]:
        """解析组长输出的分工单。**要能扛住模型不听话**（2026-10-05 实测的真问题）。

        真实现场：组长把计划写成了人话 ——
            「【单店外卖小程序 · 排期 v1.0】已拆出13项任务…T1需求→T2原型→T3设计…详见文档。」
        旧解析器只认"纯 JSON 数组"⇒ 判为无法解析 ⇒ **一个人都没派出去**
        （用户看到的就是"组长不干活"，而它其实写了满满一屏计划）。

        现在的四层容错（从规范到宽松）：
          ① 剥代码围栏 → 取**括号配对**的 JSON 数组（不是"第一个 [ 到最后一个 ]"，那样会跨段误取）
          ② 键名兼容：name/负责人/成员/assignee、task/任务/工作/内容/desc
          ③ **Markdown 表格**（`| 负责人 | 任务 |` 这种模型很爱用）
          ④ **行文兜底**：逐行找 `@某人`，把该行剩下的内容当任务
        都认不出来才返回空 —— 调用方会把原文贴进群里，让用户自己看，而不是只给一句"解析失败"。
        """
        if not raw or not raw.strip():
            return []
        text = raw.strip()
        if text.startswith("```"):
            text = text.split(chr(10), 1)[-1]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]

        # ① + ②
        for blob in TeamStore._json_arrays(text):
            try:
                data = json.loads(blob)
            except json.JSONDecodeError:
                continue
            if isinstance(data, list):
                out = TeamStore._plan_from_objects(data)
                if out:
                    return out
        # ③
        out = TeamStore._plan_from_table(text)
        if out:
            return out
        # ④
        return TeamStore._plan_from_mentions(text)

    @staticmethod
    def _json_arrays(text: str) -> list[str]:
        """挑出**括号配对**的 JSON 数组片段（深度计数；字符串里的括号不算）。"""
        out: list[str] = []
        depth, start = 0, -1
        in_str, esc = False, False
        for i, ch in enumerate(text):
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "[":
                if depth == 0:
                    start = i
                depth += 1
            elif ch == "]" and depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    out.append(text[start : i + 1])
        return out

    @staticmethod
    def _plan_from_objects(data: list[Any]) -> list[dict[str, str]]:
        """键名兼容：模型不一定会写 name/task（中文键很常见）；顺带收 `output` 与 `depends_on`。"""
        name_keys = ("name", "负责人", "成员", "组员", "assignee", "owner")
        task_keys = ("task", "任务", "工作", "工作单", "内容", "desc", "description", "详情")
        out_keys = ("output", "交付", "交付物", "产物", "deliverable", "outputs")
        dep_keys = ("depends_on", "依赖", "前置", "前置依赖", "depends", "after", "requires")
        result: list[dict[str, Any]] = []
        for it in data:
            if not isinstance(it, dict):
                continue
            nm = next((str(it[k]).strip() for k in name_keys if it.get(k)), "")
            task = next((str(it[k]).strip() for k in task_keys if it.get(k)), "")
            if not (nm and task):
                continue
            out = next((str(it[k]).strip() for k in out_keys if it.get(k)), "")
            raw_dep = next((it[k] for k in dep_keys if it.get(k) is not None), [])
            if isinstance(raw_dep, str):
                deps = [p.strip().lstrip("@") for p in re.split(r"[,，、;；/\s]+", raw_dep) if p.strip()]
            elif isinstance(raw_dep, (list, tuple)):
                deps = [str(p).strip().lstrip("@") for p in raw_dep if str(p).strip()]
            else:
                deps = []
            # 常见偷懒写法：依赖写成"@架构师 的接口契约" ⇒ 只留名字
            deps = [re.split(r"[\s的：:]", d)[0] for d in deps if d]
            result.append({"name": nm.lstrip("@").strip(), "task": task[:500],
                           "output": out[:400], "depends_on": [d for d in dict.fromkeys(deps) if d]})
        return result

    @staticmethod
    def _plan_from_table(text: str) -> list[dict[str, str]]:
        """`| 负责人 | 任务 |` 这类 Markdown 表格。"""
        rows: list[list[str]] = []
        for line in text.splitlines():
            s = line.strip()
            if not (s.startswith("|") and s.count("|") >= 3):
                continue
            cells = [c.strip() for c in s.strip("|").split("|")]
            if all(set(c) <= set("-: ") for c in cells):
                continue
            rows.append(cells)
        if len(rows) < 2:
            return []
        head = [c.lower() for c in rows[0]]
        ci_name = next((i for i, c in enumerate(head)
                        if any(k in c for k in ("负责人", "成员", "组员", "name", "assignee", "owner"))), -1)
        ci_task = next((i for i, c in enumerate(head)
                        if any(k in c for k in ("任务", "工作", "内容", "task", "desc"))), -1)
        if ci_name < 0 or ci_task < 0:
            return []
        out: list[dict[str, str]] = []
        for r in rows[1:]:
            if max(ci_name, ci_task) >= len(r):
                continue
            nm, task = r[ci_name].lstrip("@").strip(), r[ci_task].strip()
            if nm and task:
                out.append({"name": nm, "task": task[:500]})
        return out

    @staticmethod
    def _plan_from_mentions(text: str) -> list[dict[str, str]]:
        """兜底：逐行找 `@某人`，把该行剩下的内容当任务（组长常写成"@小李：做X"）。"""
        out: list[dict[str, str]] = []
        for line in text.splitlines():
            s = line.strip().lstrip("-*•").strip()
            s = re.sub(r"^(?:T\d+|[0-9]+[.、)]?)\s*", "", s)      # 去掉 T1 / 1. 这类序号
            m = re.search(r"@([^\s:：,，、)）|]+)", s)
            if not m:
                continue
            nm = m.group(1).strip()
            task = (s[: m.start()] + " " + s[m.end() :]).strip(" :：,，、-|")
            if nm and task:
                out.append({"name": nm, "task": task[:500]})
        return out

    def feed(self, gid: str) -> list[dict[str, Any]]:
        return self._read(self.root / f"feed_{gid}.json", [])

    def append(self, gid: str, **msg: Any) -> dict[str, Any]:
        m = {"seq": len(self.feed(gid)) + 1, "ts": _now(), **msg}
        with self._lock:
            feed = self.feed(gid)
            m["seq"] = len(feed) + 1
            feed.append(m)
            self._write(self.root / f"feed_{gid}.json", feed)
        return m

    def feed_after(self, gid: str, after_seq: int) -> list[dict[str, Any]]:
        return [m for m in self.feed(gid) if m.get("seq", 0) > after_seq]
