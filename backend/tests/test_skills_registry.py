# -*- coding: utf-8 -*-
"""技能库**专属测试** —— 2026-10-07 功能体检补的（此前 **0 个专属测试** ✗）。

## 为什么这块特别要紧

`SKILL.md` 是**直接进模型上下文**的东西 ✓ 官方注释自己写着它是"**语义控制面**、
供应链攻击面"✗ —— 也就是说：**能塞进 SKILL.md 的东西，就等于能对 Agent 下指令** ✓✓。
所以这里测的不是"功能好不好用" ✗ 而是**边界守没守住** ✓：

| 边界 | 体检结论 |
|---|---|
| **路径穿越**（`load_skill(name, resource="../../config.json")`）| ✅ **有防护** ✓（`is_relative_to` ✓ 会抛 PermissionError ✓）|
| **软链接逃逸**（技能目录里放个指向外面的 symlink）| ✅ 也拦得住 ✓（`resolve()` 后再比对 ✓）|
| **不可见字符**（零宽/双向控制/标签字符 —— 人眼看着无害、模型读到恶意）| ✅ 有清洗 ✓ |
| **大小上限** | 🔴 **原来一个都没有** ✗ ⇒ 已修 ✓（超限**截断并明说** ✓ 不静默截 ✗）|
"""
from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app import skills as S  # noqa: E402


@pytest.fixture()
def reg(tmp_path):
    d = tmp_path / "skills"
    (d / "写报告").mkdir(parents=True)
    (d / "写报告" / "SKILL.md").write_text(
        "---\nname: 写报告\ndescription: 帮用户写结构化报告\n---\n\n# 步骤\n1. 先列大纲\n",
        "utf-8")
    (d / "写报告" / "模板.md").write_text("# 报告模板\n\n背景 / 结论 / 建议\n", "utf-8")
    (d / "没清单").mkdir()                       # 没有 SKILL.md → 不算技能 ✓
    (d / "not_a_dir.txt").write_text("x", "utf-8")   # 文件不算 ✓
    return S.SkillRegistry(d)


# ═══ ① 列表（Level 1：常驻系统提示的那份）═══

def test_list_only_picks_folders_with_skill_md(reg):
    got = reg.list_skills()
    assert [s["name"] for s in got] == ["写报告"], got
    assert "结构化报告" in got[0]["description"]


def test_list_falls_back_to_the_folder_name(reg, tmp_path):
    d = tmp_path / "skills" / "无元数据"
    d.mkdir()
    (d / "SKILL.md").write_text("# 没写 frontmatter\n", "utf-8")
    got = {s["name"] for s in reg.list_skills()}
    assert "无元数据" in got, f"没 frontmatter 时应退回目录名 ✗：{got}"


def test_list_on_a_missing_dir_is_empty_not_an_error(tmp_path):
    assert S.SkillRegistry(tmp_path / "根本没有").list_skills() == []


# ═══ ② 加载（Level 2/3）═══

def test_load_returns_the_full_skill_body(reg):
    """Level 2 交付的是 **SKILL.md 全文** ✓（连 frontmatter 一起 ✓ 这是既定行为 ✓）。

    ★ 我第一版断言"frontmatter 不该进上下文" ✗ —— **是我错了** ✓：
      模块 docstring 明写 Level2 返回"SKILL.md 全文" ✓ 而 frontmatter 只有两三行 ✓
      顺带让模型知道这个技能**自称**叫什么、干什么 ✓ 无害 ✓（元数据本来就在 Level1 有 ✓）。
    """
    text = reg.load("写报告")
    assert "先列大纲" in text
    assert text.startswith("---"), "全文应含 frontmatter（既定行为 ✓）"


def test_load_frontmatter_costs_little(reg):
    """既然全文都进上下文 ✓ 那就钉一条：**frontmatter 不该喧宾夺主** ✓（几行以内 ✓）。"""
    text = reg.load("写报告")
    head = text.split("---", 2)[1]
    assert len(head.splitlines()) <= 6, f"frontmatter 太长了（会白烧 token）✗：{head!r}"


def test_load_by_folder_name_also_works(reg):
    assert "先列大纲" in reg.load("写报告")


def test_load_resource_file(reg):
    assert "报告模板" in reg.load("写报告", "模板.md")


def test_load_unknown_skill_lists_what_is_available(reg):
    with pytest.raises(FileNotFoundError, match="未找到技能"):
        reg.load("不存在的技能")
    try:
        reg.load("不存在的技能")
    except FileNotFoundError as e:
        assert "写报告" in str(e), f"报错没告诉有哪些可用 ✗：{e}"


# ═══ ③ 安全边界（**这块才是重点** ✓）═══

@pytest.mark.parametrize("bad", ["../../config.json", "..\\..\\config.json",
                                 "/etc/passwd", "C:\\Windows\\win.ini",
                                 "sub/../../外面.txt"])
def test_resource_cannot_escape_the_skill_folder(reg, tmp_path, bad):
    """★★ **资源路径不许跳出技能目录** ✓ —— 否则一个 SKILL.md 就能读走你的密钥配置 ✗✗。

    体检结论：防护**本来就存在** ✓（`resolve()` + `is_relative_to` ✓）；
    这条测试是把它**钉住** ✓ 免得以后有人"顺手简化"掉 ✓。
    """
    outside = tmp_path / "config.json"
    outside.write_text('{"api_key": "绝密"}', "utf-8")
    with pytest.raises((PermissionError, FileNotFoundError, IsADirectoryError)):
        reg.load("写报告", bad)


def test_resource_cannot_follow_a_symlink_outside(reg, tmp_path):
    """技能目录里放个**指向外面的软链接** ✗ —— 也要拦住 ✓（只看字符串前缀是挡不住的 ✓）。"""
    secret = tmp_path / "secret.txt"
    secret.write_text("外面的大秘密", "utf-8")
    link = reg.dir / "写报告" / "链接.md"
    try:
        link.symlink_to(secret)
    except (OSError, NotImplementedError):
        pytest.skip("本机不允许创建符号链接（Windows 需要权限）")
    with pytest.raises(PermissionError):
        reg.load("写报告", "链接.md")


def test_invisible_characters_are_stripped_from_body_and_metadata(tmp_path):
    """★ **零宽 / 双向控制 / 标签字符必须剥掉** ✓ —— 它们能把"人眼看到的"和
    "模型读到的"做成**两套内容** ✗（经典的隐藏指令手法 ✓）。

    ★ 我第一版这里写成 `"\\uE0041"` ✗ —— Python 的 `\\u` 只吃 **4 位** ✓
      实际得到的是 **U+E041（私用区）** ✗ 而**私用区是合法的图标字体区** ✓
      **本来就不该剥** ✓（剥了会把一些正常图标弄坏 ✓）。
      真正要测的"标签字符区"是 **U+E0000–U+E007F** ✓ 必须写 `\\U000E0041`（大写 U + 8 位）✓。
      ⇒ 这个坑值得留在注释里 ✓：**测安全边界时，连"我到底测了哪个码点"都得核准** ✓。
    """
    d = tmp_path / "skills" / "阴的技能"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\nname: 阴\u200b的技能\ndescription: 无害\u202e描述\n---\n\n"
        "正文\u2060\n\U000E0041隐藏指令\ufeff",
        "utf-8")
    reg = S.SkillRegistry(tmp_path / "skills")
    meta = reg.list_skills()[0]
    assert "\u200b" not in meta["name"] and "\u202e" not in meta["description"], meta
    body = reg.load("阴的技能")
    for bad, what in (("\u2060", "词接符"), ("\U000E0041", "标签字符"),
                      ("\ufeff", "BOM")):
        assert bad not in body, f"正文里还有{what}（U+{ord(bad):05X}）✗：{body!r}"
    assert "正文" in body and "\n" in body, "清洗把正常内容也弄坏了 ✗"


def test_private_use_area_is_kept(tmp_path):
    """★ **私用区（U+E000–U+F8FF）要保留** ✓ —— 图标字体（Nerd Font 等）就住在那儿 ✓
    把它一起剥了会把正常技能正文弄花 ✗（"清洗过头"和"清洗不够"一样是 bug ✓）。"""
    out, n = S.sanitize_skill_text("图标 \ue0b0 和 \uf8ff 都该留着")
    assert n == 0 and "\ue0b0" in out and "\uf8ff" in out, (out, n)


def test_sanitizer_keeps_legitimate_formatting():
    """清洗**不能误伤**正常内容 ✓（换行 ✓ 制表 ✓ emoji 变体选择符 ✓）。"""
    kept = "第一行\n\t缩进 🏔\ufe0f 结束"
    out, n = S.sanitize_skill_text(kept)
    assert out == kept and n == 0, (out, n)


# ═══ ④ 大小上限（体检发现的缺口 ✓ 已修 ✓）═══

def test_oversized_skill_is_truncated_and_says_so(tmp_path):
    """★★ **超大技能必须截断** ✓ —— 而且**要明说被截了** ✗✓。

    体检发现：docstring 写着"<5k token"✓ 而**代码从不管大小** ✗ ⇒
    一个几百 KB 的 SKILL.md 会被**整篇塞进上下文** ✗（烧钱 ✓ 甚至顶爆上下文 ✓）。
    修法：截断 + 附一句"共 N 字、只给了前 M 字、想看全请分段读资源" ✓
    —— **不静默截断** ✗（静默截断会让模型以为技能就这么点内容 ✓ 那是另一种骗人 ✓）。
    """
    d = tmp_path / "skills" / "巨无霸"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("哗" * (S.MAX_SKILL_CHARS + 5000), "utf-8")
    reg = S.SkillRegistry(tmp_path / "skills")
    text = reg.load("巨无霸")
    assert len(text) < S.MAX_SKILL_CHARS + 500, f"没截断，返回了 {len(text)} 字 ✗"
    assert "已截断" in text, "截断了却没告诉模型 ✗（它会以为技能就这么点内容 ✓）"
    # ★ 2026-10-11（平台闸修复 ①）：提示里写的是**一页多少字**（`DEFAULT_SKILL_PAGE` ✓）
    #   —— 而不是那个 20000 的兜底硬上限 ✓（旧断言钉的是后者 ✓ 口径变了跟着改 ✓）
    assert str(S.DEFAULT_SKILL_PAGE) in text, "没写清这一页给了多少字 ✗"


def test_normal_sized_skill_is_not_touched(reg):
    text = reg.load("写报告")
    assert "已截断" not in text, "正常大小的技能被误截了 ✗"


def test_oversized_resource_is_also_capped(tmp_path):
    """**资源文件同样要限量** ✓ —— 否则"分段读资源"这条路自己就能把上下文顶爆 ✗。"""
    d = tmp_path / "skills" / "带大资源"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: 带大资源\n---\n正文", "utf-8")
    (d / "大文件.md").write_text("啊" * (S.MAX_SKILL_CHARS + 3000), "utf-8")
    reg = S.SkillRegistry(tmp_path / "skills")
    text = reg.load("带大资源", "大文件.md")
    assert len(text) < S.MAX_SKILL_CHARS + 500, f"资源文件没限量 ✗：{len(text)} 字"
    assert "已截断" in text


# ═══ ⑤ 分页读全（2026-10-11「平台闸」修复 ①）═══
#
# 现场（实测）：一份 2 万字左右的技能文档，模型只吃到 4000 字，
# **而且连"被截断了"这句提示都没吃到**（提示挂在正文尾巴上 ⇒ 被第二道截断砍掉 ✗）
# ⇒ 它以为技能就这么点内容，照着残缺的技能做事，在 8.7 那一步反复返工 ✗✓。
#
# 所以这一组钉三件事（缺一条，模型就又会"不知道自己没看全"）：
#   ① 提示必须在**这一页的开头**（尾巴上的提示会被下游任何一次截断吃掉 ✗）
#   ② 提示必须写清**还有多少字**（真数字，不是拍脑袋的常量 ✗）
#   ③ 提示必须给出**可直接照抄的续读参数**（`offset=` —— 光说"请分段读"模型不会读 ✓）

def test_truncation_notice_reports_the_real_total(tmp_path):
    """★ 提示里的"共 N 字"必须是**真数字**（不是截断后的长度 ✗）。

    这一条是**先写断言、再回过来核实过**的 ✓：我原本以为 f-string 里的 `len(cleaned)`
    取的是截断后的值（bug ✗），实测**不是** —— Python 先算完右边再赋值 ✓
    （`c='x'*10; c = c[:4] + f'{len(c)}'` ⇒ 报的是 **10** ✓）。
    ⇒ 数字这一半**本来就是对的** ✓ 如实记在这里 ✓ 免得下一个人再"修"一遍 ✓。
    真正的病在下一条：提示挂在**尾巴**上，被下游的 4000 字截断吃掉 ✗。
    """
    d = tmp_path / "skills" / "巨无霸2"
    d.mkdir(parents=True)
    total = S.MAX_SKILL_CHARS + 5000
    (d / "SKILL.md").write_text("哗" * total, "utf-8")
    reg = S.SkillRegistry(tmp_path / "skills")
    text = reg.load("巨无霸2")
    assert f"共 {total} 字" in text, f"没报出真实总字数（应含「共 {total} 字」）✗：{text[:200]!r}"


def test_truncation_notice_is_at_the_front_so_it_survives_downstream_cuts(tmp_path):
    """★★ **提示必须在开头** ✓ —— 挂在尾巴上等于没写（下游任何一次截断都会吃掉它 ✓）。

    实测链条：skills.py 留在尾巴 → `loop.load_skill` 再砍一刀 ⇒ 提示消失 ✗
    ⇒ 模型**不知道自己看到的不是全文** ✗✓ 这是本次最要紧的一条。
    """
    d = tmp_path / "skills" / "巨无霸3"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("哗" * (S.MAX_SKILL_CHARS + 5000), "utf-8")
    reg = S.SkillRegistry(tmp_path / "skills")
    text = reg.load("巨无霸3")
    head = text[:300]
    assert "已截断" in head, f"提示不在开头（前 300 字里没有）✗：{head!r}"
    # 下游就算按 4000 字砍，也必须还能看到这句 ✓
    assert "已截断" in text[:4000], "按 loop 那道 4000 字截断之后提示又没了 ✗"


def test_truncation_notice_tells_the_model_the_exact_next_call(tmp_path):
    """★ 不能只说"请分段读"✗ —— 要给**能照抄的参数**（`load_skill(..., offset=N)` ✓）。"""
    d = tmp_path / "skills" / "巨无霸4"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("哗" * (S.MAX_SKILL_CHARS + 5000), "utf-8")
    reg = S.SkillRegistry(tmp_path / "skills")
    text = reg.load("巨无霸4")
    assert "offset=" in text, "没给出续读参数 ✗"
    assert "load_skill" in text, "没点明用哪个工具续读 ✗"
    assert "巨无霸4" in text, "没点明读哪个技能 ✗"


def test_offset_reads_the_rest_and_reaches_the_end(tmp_path):
    """★ 分页要真能读完（否则"能读全"只是句空话 ✗）：第一页 + 第二页 = 全文 ✓。"""
    d = tmp_path / "skills" / "分页"
    d.mkdir(parents=True)
    body = "".join(chr(0x4E00 + (i % 2000)) for i in range(S.DEFAULT_SKILL_PAGE + 300))
    (d / "SKILL.md").write_text(body, "utf-8")
    reg = S.SkillRegistry(tmp_path / "skills")
    p1 = reg.load("分页")
    p2 = reg.load("分页", offset=S.DEFAULT_SKILL_PAGE)
    assert "还有" in p1 and f"offset={S.DEFAULT_SKILL_PAGE}" in p1, p1[:200]
    # 第二页是最后一段 ⇒ 必须如实说"已到结尾"，不能再说"还有 N 字"✗
    assert "已到结尾" in p2 or "已是最后" in p2, f"最后一页没说明到底 ✗：{p2[:200]!r}"
    # 两页拼起来要覆盖全文（页面头部那句提示不算正文，只核对正文片段）
    assert body[:200] in p1 and body[-200:] in p2, "分页拼不回全文 ✗"


def test_offset_beyond_the_end_says_so_instead_of_returning_empty(tmp_path):
    """★ 越界要**如实说**（返回空串 = 模型以为技能是空的 ✗ 那是另一种骗人 ✓）。"""
    d = tmp_path / "skills" / "小的"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("很短的一份技能", "utf-8")
    reg = S.SkillRegistry(tmp_path / "skills")
    out = reg.load("小的", offset=999999)
    assert out.strip(), "越界返回空串 ⇒ 模型以为技能是空的 ✗"
    assert "超出" in out or "已经到" in out, f"越界没说明原因 ✗：{out!r}"


def test_negative_offset_is_clamped_not_crashed(tmp_path):
    """★ 模型传负数/怪值是常态 ✓ —— 必须**兜住**（截到开头），不许抛异常弄死任务 ✗。"""
    d = tmp_path / "skills" / "负号"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("正文在这里", "utf-8")
    reg = S.SkillRegistry(tmp_path / "skills")
    assert "正文在这里" in reg.load("负号", offset=-100)
    assert "正文在这里" in reg.load("负号", offset="看这里")   # 怪字符串也一样兜住 ✓

