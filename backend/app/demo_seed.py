# -*- coding: utf-8 -*-
"""示例数据 —— 让第一次打开的人（和拍演示视频的人）看到"它到底长什么样" ✓

## 为什么需要

新装的应用**任务列表是空的** ✗ ⇒ 新用户不知道能干什么 ✓ 拍视频也没素材 ✓
但**绝对不能往用户已有的数据里塞东西** ✗（那是他的真实工作记录 ✓）

## 安全边界（★ 这个模块的全部设计都围绕它 ✓）

  · ★ **只在"一个任务都没有"时才允许种** ✗ —— 有任何一个任务（哪怕已删除 ✓）就拒绝 ✓
    防的是：用户装了新版手滑点一下 ⇒ 真数据里混进 4 条假任务 ✗
  · ★ 每条标题都以「**【示例】**」开头 ✓ 一眼能认出 ✓ 且可**直接删**（走回收站 ✓ 可恢复 ✓）
  · ★ **不碰模型、不花钱** ✓ —— 只写任务记录 + 一小段事件，让界面有东西可看 ✓
  · ★ **纯本地** ✓ 不写任何用户目录之外的东西 ✓
"""
from __future__ import annotations

import json
import pathlib
from datetime import datetime, timedelta, timezone

MARK = "【示例】"

#: 四条示例任务（覆盖四种典型能力 ✓ 拍视频时一条一个镜头 ✓）
_DEMO = [
    ("把下载文件夹里的图片按类型分类，并产出一份清单",
     "先扫描目录 ⇒ 按扩展名归类 ⇒ 移动文件 ⇒ 生成清单表格。全程只动你指定的目录。"),
    ("调研三个国产开源语音模型，给出带来源的对比结论",
     "并行开三条线分别查资料 ⇒ 汇总成对比表 ⇒ 每条结论都带原始链接。"),
    ("把这段会议录音整理成纪要，标出待办与负责人",
     "分片转写 ⇒ 按时间线归并 ⇒ 提炼待办与负责人 ⇒ 输出纪要文件。"),
    ("做一个记账与记事二合一的小网页，交付前自己点一遍",
     "生成单文件 HTML ⇒ ★ 交付前用真浏览器把每个按钮点一遍 ⇒ 点了没反应的算缺陷。"),
]


def has_any_task(store) -> bool:
    """★ 只要有任何任务（含回收站里的 ✓）就返回 True ⇒ 禁止种示例 ✗"""
    try:
        if store.load_index():
            return True
    except Exception:  # noqa: BLE001 —— 读不出来时**保守当有** ✓（宁可不种 ✓）
        return True
    try:
        trash = pathlib.Path(store.tasks_dir) / "_deleted_"
        return trash.is_dir() and any(trash.iterdir())
    except Exception:  # noqa: BLE001
        return True


def seed(store, *, now: datetime | None = None) -> dict:
    """种入示例任务 ✓ 返回 {ok, created, reason} ✓ **永不抛** ✗（接口层要好回话 ✓）。"""
    if has_any_task(store):
        return {"ok": False, "created": [], "reason": "已有任务数据 ⇒ 拒绝种示例（不往你的数据里掺东西 ✓）"}

    from .schemas import TaskSummary

    base = now or datetime.now(timezone.utc)
    created: list[str] = []
    tasks: list[TaskSummary] = []
    for i, (title, detail) in enumerate(_DEMO):
        ts = (base - timedelta(minutes=len(_DEMO) - i)).isoformat(timespec="seconds")
        tid = f"task_demo_{base.strftime('%Y%m%d')}_{i + 1}"
        tasks.append(TaskSummary(id=tid, title=MARK + title, status="done",
                                 created_at=ts, updated_at=ts))
        created.append(tid)
        try:
            ws = pathlib.Path(store.workspace_dir(tid))
            ws.mkdir(parents=True, exist_ok=True)
            (ws / "说明.txt").write_text(
                f"{MARK}{title}\n\n{detail}\n\n"
                "★ 这是一条**示例任务** ✓ 随时可以删掉（会进回收站 ✓ 可恢复 ✓）\n",
                encoding="utf-8")
        except Exception:  # noqa: BLE001 —— 建目录失败也继续 ✓ 任务记录本身就是价值 ✓
            pass

    try:
        store.save_index(tasks)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "created": [], "reason": f"写入失败：{type(e).__name__}: {e}"}
    return {"ok": True, "created": created, "reason": f"已种入 {len(created)} 条示例任务 ✓"}


def sample_json() -> str:
    """给测试/文档用的一份样例（不落盘 ✓）"""
    return json.dumps([t for t, _ in _DEMO], ensure_ascii=False, indent=2)
