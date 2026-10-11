# -*- coding: utf-8 -*-
"""★ 2026-10-11「平台闸」修复 ①：**长文本被截断、且模型不知道** —— 观测这一侧。

## 现场（真实任务，跑到 60 分钟窗口结束仍未交付）

一份 2 万字左右的技能文档（`SKILL.md`），模型**只吃到 4000 字** ✗；
更要命的是**连"内容被截断了"这句提示也没吃到** ✗✓ ——
因为提示挂在正文**尾巴**上，正好被这第二道截断砍掉。
⇒ 模型**不知道自己看到的不是全文** ✓ 它照着残缺的技能做事 ✓
  在技能第 8.7 节那一步反复返工 ✓ 而它连 8.7 节之前的内容都没读全。

## 两道闸（都在这一条链上，缺一不可）

| 闸 | 位置 | 旧行为 |
|---|---|---|
| ① 技能注册表 | `skills.py` | 20000 字上限 ✓ 截断 ✓ 但提示在**尾巴** ✗ |
| ② 观测进 history | `loop.py` | 硬编码 `[:4000]` ✗ 无提示 ✗ |

## 这一组的判据

1. **截断必须留提示**（不静默 ✗）—— 而且提示要**在开头**，扛得住下游任何一次再截断 ✓
2. **提示要给能照抄的下一步**（"还有 M 字，用 load_skill/file_read 读" ✓）
3. **上限要跟着上下文预算走**（`max_context_tokens` 小的时候不许把观察撑爆 ✗）
4. **不许把上限调成"不截断"** ✗ —— 单条巨观察会把上下文顶爆，那是另一种把任务弄死 ✓
"""
from __future__ import annotations

from app.loop import (
    MAX_OBSERVATION_CHARS,
    TaskRun,
    _truncate_observation,
)

# ═══ ① 截断函数本身 ═══

def test_truncation_notice_goes_first_not_last():
    """★★ **提示必须在开头** ✓ —— 挂在尾巴上，下游任何一次再截断都会吃掉它 ✗。

    这正是本次事故的形态：`skills.py` 把提示放在尾巴 ✓ 这里再砍到 4000 字 ✓
    ⇒ 提示消失 ✓ 模型**不知道自己没看全** ✗✓。
    """
    long_text = "正" * 5000
    out = _truncate_observation(long_text, 1000)
    assert out.startswith("["), f"截断提示不在开头 ✗：{out[:80]!r}"
    assert "已截断" in out[:300], "开头没有「已截断」字样 ✗"


def test_truncation_notice_reports_real_numbers():
    """★ 提示要写**真数字**（原文多少字 / 这里给了多少字 / 还剩多少字 ✓）——
    模型据此才知道"我漏了多少"，也才知道值不值得再去读 ✓。"""
    long_text = "正" * 5000
    out = _truncate_observation(long_text, 1000)
    assert "5000" in out, f"没报出原文总长 ✗：{out[:200]!r}"
    assert "1000" in out, f"没报出这一份给了多少 ✗：{out[:200]!r}"
    assert "4000" in out, f"没报出还剩多少没给 ✗：{out[:200]!r}"


def test_truncation_notice_tells_it_how_to_get_the_rest():
    """★ 光说"被截断了"没用 ✗ —— 要给出**能照着做的下一步** ✓。"""
    out = _truncate_observation("正" * 5000, 1000)
    assert "offset=" in out, f"没给出续读参数 ✗：{out[:250]!r}"
    assert "file_read" in out or "load_skill" in out, "没说用哪个工具续读 ✗"


def test_short_observation_is_left_completely_alone():
    """★ 没超限就**一个字都不许动** ✓（多一句提示就是白烧 token + 稀释注意力 ✗）。"""
    text = "短的一条观察"
    assert _truncate_observation(text, 1000) == text


def test_truncation_never_exceeds_the_cap():
    """★ 提示本身也要**算进上限** ✓ —— 否则"上限"就是个假数字（每次多出几十字 ✗）。"""
    for cap in (200, 1000, 4000):
        out = _truncate_observation("正" * (cap * 3), cap)
        assert len(out) <= cap, f"上限 {cap} 被突破：实际 {len(out)} ✗"


def test_tiny_cap_does_not_crash_or_lose_the_notice():
    """★ 上限极小（配置给了个怪值）也要**兜住** ✓ 不许抛异常弄死任务 ✗。"""
    out = _truncate_observation("正" * 5000, 10)
    assert out and "截断" in out, f"极小上限下提示丢了 ✗：{out!r}"


# ═══ ② 上限怎么算（安全做法：跟着上下文预算走）═══

def test_default_cap_is_bigger_than_the_old_hardcoded_4000():
    """★ 4000 字对中文技能文档太小 ✗（2 万字文档只能吃到 1/5 ✓）。

    这一条**故意**钉住"调大了"这件事 ✓ —— 回滚成 4000 就该红 ✓。
    """
    assert MAX_OBSERVATION_CHARS > 4000, "上限没调大 ⇒ 长技能还是只能吃到一小截 ✗"


def _mk_run(tmp_path, **kw):
    from types import SimpleNamespace

    from app.approval import ApprovalManager
    from app.bus import EventBus
    from app.executors.local import LocalExecutor
    from app.providers.base import AssistantTurn, ModelProvider
    from app.schemas import TaskSummary
    from app.store import FsStore

    class _P(ModelProvider):
        name = "stub"

        async def next_turn(self, task_input, history, tools, on_delta=None):  # type: ignore[override]
            return AssistantTurn(text="结束")

    store = FsStore(tmp_path / "data")
    task = TaskSummary(id="task_20261011_obs01", title="观察截断",
                       created_at="2026-10-11T00:00:00Z",
                       updated_at="2026-10-11T00:00:00Z")
    ex_cfg = SimpleNamespace(workspace_root=tmp_path / "ws", timeout_seconds=5.0, shell="",
                            search_url="", browser_channel="", comfyui_url="",
                            image_checkpoint="", allowed_dirs=[])
    args = dict(store=store, bus=EventBus(), provider=_P(), executor=LocalExecutor(ex_cfg),
                approval=ApprovalManager(), tools=[], max_iterations=1, timeout_seconds=5.0,
                approval_required=[], on_finish=lambda r: None)
    args.update(kw)
    return TaskRun(task, "活", **args)


def test_cap_follows_the_context_budget(tmp_path):
    """★★ **不许把上下文预算撑爆** ✓ —— 上限必须是 `max_context_tokens` 的函数 ✓。

    依据：本项目自己的估算口径是「CJK 1 字 ≈ 1 token」✓（`loop._compact_history` ✓）。
    所以"一条观察最多占预算的 1/8"是个能算出来的安全线 ✓：
      · 65k 上下文 ⇒ 一条观察最多几千 token，压缩器还有充分余量 ✓
      · 8k 上下文（小模型）⇒ 上限自动收到很小 ✓ 不会一条观察就把窗口顶满 ✗
    """
    big = _mk_run(tmp_path / "big", max_context_tokens=65536)
    small = _mk_run(tmp_path / "small", max_context_tokens=8192)
    assert small._observation_cap < big._observation_cap, (
        "小上下文的观察上限没跟着收窄 ⇒ 一条巨观察就能把窗口顶爆 ✗"
    )
    # 一律不许超过上下文预算的 1/8（CJK 按 1 token/字算的最坏情形）
    assert big._observation_cap <= 65536 // 8, big._observation_cap
    assert small._observation_cap <= 8192 // 8, small._observation_cap


def test_cap_can_be_overridden_but_stays_bounded(tmp_path):
    """★ 可配置 ✓ 但**有兜底上限** ✗ —— 谁都不许把它设成"不截断"（那会把上下文顶爆 ✗）。"""
    run = _mk_run(tmp_path, max_context_tokens=65536, max_observation_chars=999_999)
    assert run._observation_cap <= 65536 // 8, "显式配置绕过了安全上限 ✗"
    assert run._observation_cap >= 1000, "收得过头 ⇒ 正常观察都会被砍 ✗"


def test_loop_actually_uses_the_cap_for_history():
    """★★ 接线锚点：`_iterate` 写 history 时**真的用这个上限** ✓ ——
    "写了个函数但没人调"是本项目已经踩过的坑 ✓（见 test_untrusted_content ✓）。"""
    import pathlib

    text = (pathlib.Path(__file__).resolve().parents[1] / "app" / "loop.py").read_text("utf-8")
    assert "_truncate_observation(output" in text, "写 history 时没走这个截断函数 ✗"
    assert "self._observation_cap" in text, "上限没接进循环 ✗"


def test_the_old_4000_hardcode_is_gone():
    """★ 旧行为是硬编码 `output[:4000]` ✗ —— 它必须消失（否则改了上限也没用 ✗）。"""
    import pathlib

    text = (pathlib.Path(__file__).resolve().parents[1] / "app" / "loop.py").read_text("utf-8")
    assert '"content": _wrap_untrusted(name, output[:4000])}' not in text, \
        "旧的 4000 硬编码还在 ✗（改了上限也不生效 ✗）"
