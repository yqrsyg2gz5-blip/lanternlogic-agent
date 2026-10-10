"""接力模式（relay）**界面接线**的锚点。

后端语义在 tests/test_team_relay.py 里钉着；这里只钉"界面上能不能选到、能不能看出跑到哪一棒"：
  · 建群/改群模式的下拉里必须有"接力"（不然功能做了用户也够不着 —— 本会话在语音上吃过这个亏）
  · 群卡片上要显示"第 N/M 棒"（接力最怕的是"看着像卡住了"）
"""
from __future__ import annotations

import pathlib

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SRC = _ROOT / "frontend" / "src"
_TV = (_SRC / "components" / "TeamView.tsx").read_text("utf-8")
_TEAM = (_ROOT / "backend" / "app" / "team.py").read_text("utf-8")
_MAIN = (_ROOT / "backend" / "app" / "main.py").read_text("utf-8")


def test_mode_dropdown_offers_relay():
    assert "'manual' | 'broadcast' | 'leader' | 'relay'" in _TV, "模式状态的类型里没有 relay"
    assert '<option value="relay">接力：一个做完交给下一个（串行）</option>' in _TV, \
        "下拉里没有「接力」这一项（功能做了也够不着）"
    assert "relay: '接力'" in _TV, "群卡片上的模式名没加接力"
    # ★ 两个下拉都要有（新建群 + 群内改模式）—— 本班实测：只加了前者，后者仍是三项，
    #   于是"建群时能选接力、建完却改不回来"（验证脚本抓到的正是这个下拉）。
    assert _TV.count('value="relay"') == 2, \
        f"接力的下拉项应当有 2 处（新建群 + 群内改模式），实际 {_TV.count('value=\"relay\"')} 处"


def test_group_card_shows_the_relay_leg():
    assert "g.relay_pos ?? 0) > 0" in _TV, "没显示接力棒次"
    assert "第 {g.relay_pos}/{g.relay_total} 棒" in _TV, "棒次文案不对"


def test_backend_modes_are_single_sourced():
    """★ 模式取值只该有一处定义（此前 create_group 与 set_mode 各写一份校验，改一处漏一处）。"""
    assert 'MODES = ("manual", "broadcast", "leader", "relay", "meeting")' in _TEAM, "模式清单不是唯一来源"
    assert _TEAM.count("if mode not in self.MODES") == 1, "create_group 没走统一校验"
    assert _TEAM.count("if m not in self.MODES") == 1, "set_mode 没走统一校验"


def test_relay_handoff_is_wired_into_the_delivery_watcher():
    """派发时带上棒次、交付时按棒次交接 —— 两处**都**要接上，只接一处等于没接。"""
    assert "def _relay_after_delivery(" in _MAIN, "没有交接函数"
    assert "relay_pos=relay_pos" in _MAIN, "看门任务没带棒次（防串棒全靠它）"
    assert "_team_store.relay_advance(gid, pos)" in _MAIN, "交付后没推进接力"
    assert 'mode") == "relay"' in _MAIN, "say 端点里没有接力分支"
    # 失败/超时都要**停下并说明**，而不是默默接着往下传
    assert "_team_store.relay_stop(gid" in _MAIN, "没有中断路径"
    assert "接力停在第" in _MAIN, "中断时没告诉用户停在哪一棒"
