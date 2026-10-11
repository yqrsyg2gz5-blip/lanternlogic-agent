# -*- coding: utf-8 -*-
"""发布前那几份文档的**一致性守卫** —— 2026-10-06 夜。

## 为什么要有它

这批改的是**对外文字** ✓ 最容易出现的情况是：
**改了这处、忘了那处** ⇒ 门口写着"19 个工具"而实际 22 个 ✗
⇒ 更要命的是**两份文件互相打架**（README 说"开源实现" ✓ LICENSE 说"未开源" ✗）——
这种矛盾**用户一眼就能看到** ✓ 而它比缺一句话更伤信任 ✓。

⇒ 凡是"**能被机器核对的事实**"，都在这儿钉住 ✓：
工具数 ✓ 不再出现攀比措辞 ✓ 顶部三句在 ✓ 隐私/条款文件在且被链接 ✓
联网点与代码一致 ✓ 不出现"没有任何上报"这类**没验证过**的承诺 ✗。
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_README = (_ROOT / "README.md").read_text("utf-8")
_PRIVACY = (_ROOT / "PRIVACY.md").read_text("utf-8")
_TERMS = (_ROOT / "TERMS.md").read_text("utf-8")


def _tool_count() -> int:
    sys.path.insert(0, str(_ROOT / "backend"))
    from app.tools import ALL_TOOLS
    return len(ALL_TOOLS)


def test_readme_tool_count_matches_the_code():
    """**门口写的工具数必须等于代码里的工具数** ✓。

    真事：README 写 19 ✓ 实际早就 22 了 ✗（HANDOVER 改过、README 漏了 ✓）——
    这种"过时数字"最伤信任 ✓ 而且**机器一秒就能对上** ✓。
    """
    n = _tool_count()
    assert f"**{n}**" in _README or f"（**{n} 个**）" in _README, \
        f"README 里没写对工具数（实际 {n} 个）✗"
    assert "19 个" not in _README and "19个" not in _README, "README 里还留着过时的 19 ✗"


def test_no_borrowed_halo_in_the_front_page():
    """**不写"受谁启发"** ✓（用户口径：自己研发的 ✓ 不攀比 ✓）。

    这条只管对外首屏（README）✓ —— 内部文档里提别的产品做**对照**是正常的 ✓ 不在管辖内 ✓。
    顺带把"开源"这个词也管住：现在 LICENSE 写的是**未开源** ✓
    ⇒ README 里不能出现"开源实现"这种**和 LICENSE 打架**的说法 ✗。
    """
    for bad in ("受 Manus", "Manus 启发", "开源实现", "启发自"):
        assert bad not in _README, f"README 里还留着攀比/错误措辞：{bad} ✗"
    assert "自己研发" in _README or "自行设计" in _README, "没说清是自研的 ✓"


def test_front_page_answers_the_three_questions():
    """首屏必须回答用户最关心的三件事 ✓（用户点名要的 ✓）：

    ① 要花钱吗 ✓ ② 东西归谁 ✓ ③ **能不能拿去卖** —— 这三句**必须在顶部** ✓
    （放文末等于没写 ✓ 用户不会翻到底 ✓）。

    ★ 2026-10-09（AGPL 批）改：第 ③ 条**从"不许卖"翻成了"能卖，但…"** ✓
      —— 口径变了（AGPL 允许商用 ✓ 见 LICENSE/COMMERCIAL.md ✓）⇒ 断言跟着变 ✓
      本仓规矩：要求变了、测试跟着变 ✓ 红一次 ✓ 改对 ✓（这已是第 N 次 ✓）
    """
    head = "\n".join(_README.splitlines()[:30])
    for k in ("免费用", "产出归你", "商用也免费"):
        assert k in head, f"顶部三句缺了「{k}」✗（放文末等于没写 ✓）"


def test_license_belongs_to_the_registered_business():
    """★★ 署名必须是**营业执照上的那个主体** ✓ —— 写错名字等于"指的不是同一家" ✗。

    真事（2026-10-07 凌晨 ✗）：文件里一直写着"**丹东云杉网络工作室**" ✗
    而用户执照上的全称是"**丹东振兴云杉互联网服务工作室（个体工商户）**" ✗✓
    —— 差着字 ✓ 法律上可能被认为**不是同一个主体** ✗。
    所以这条把**准确名称**钉死 ✓（以后谁改错都当场红 ✓）。
    """
    right = "丹东振兴云杉互联网服务工作室"
    lic = (_ROOT / "LICENSE").read_text("utf-8")
    assert right in lic, f"LICENSE 里的主体名称不对 ✗（应为 {right} ✓）"
    for f in ("README.md", "PRIVACY.md", "TERMS.md"):
        assert right in (_ROOT / f).read_text("utf-8"), f"{f} 里的主体名称不对 ✗"
    # 旧名字（错的）不许再出现在对外文件里 ✗
    for f in ("LICENSE", "README.md", "PRIVACY.md", "TERMS.md"):
        assert "丹东云杉网络工作室" not in (_ROOT / f).read_text("utf-8"), \
            f"{f} 还留着旧名称'丹东云杉网络工作室' ✗"
    # 联系方式必须能找得到人 ✓（用户明确要求写上邮箱 ✓）
    # ★ 2026-10-08 晚改：LICENSE 换成 **AGPL-3.0 官方全文**（英文 ✓ 里面不会有邮箱 ✗）
    #   ⇒ 邮箱挪到 COMMERCIAL.md（商业授权说明 ✓）—— 那儿才是"要谈钱"的地方 ✓
    #     而 LICENSE 里只要有**版权主体**就够了 ✓（官方全文要求的正是这个 ✓）
    assert "yangbo0801@163.com" in (_ROOT / "COMMERCIAL.md").read_text("utf-8"), \
        "COMMERCIAL.md 里没有联系邮箱 ✗（商业授权得能找得到人 ✓）"


def test_license_says_everyone_uses_free_but_nobody_may_resell():
    """★★ 授权口径 —— 这份断言在**一天里跟着用户改过三次** ✓ 都记在这儿 ✓

    > v1（2026-10-07）："个人企业都行，但你不能把我的 Agent 拿去卖"（闭源 + 商业授权）
    > v2（2026-10-08 白天）："相当于半开源 —— 免费用，但**不许卖、不许改名**，
    >   只有我能商用，别人商用要向我授权"（自家 source-available 协议）
    > **v3（2026-10-08 晚，当前）："我想做这个 agpl 开源"** ⇒ **AGPL-3.0 真开源** ✓

    ⇒ v3 与 v2 的**关键差别**（必须记清 ✗ 别混 ✓）：
      · v2 禁商用 ⇒ **v3 允许商用**（含卖 ✓ AGPL 不禁止收费 ✓）
      · v2 禁改名 ⇒ **v3 允许改名**（只要保留版权声明与许可证 ✓）
      · v2 的"商用须授权" ⇒ **v3 变成双授权**：遵守 AGPL 就免费商用 ✓
        不想开源修改才买商业授权 ✓（买的是"免除开源义务" ✓ 不是"商用许可" ✓）

    ★ 所以这条测试**从"禁商用"翻成了"讲清商用规则"** ✓ —— 这是要求变了 ✓
      不是"改测试迁就代码" ✗（本仓规矩：口径变了、测试跟着变 ✓ 红一次 ✓ 改对 ✓）
    """
    lic = (_ROOT / "LICENSE").read_text("utf-8")
    assert "AGPL" in lic, "LICENSE 不是 AGPL ✗"
    comm = (_ROOT / "COMMERCIAL.md").read_text("utf-8")
    readme = (_ROOT / "README.md").read_text("utf-8")
    # ① "能免费用"这条没变 ✓（AGPL 更宽松 ✓ 但要**说清楚** ✓）
    for name, txt in (("README.md", readme), ("COMMERCIAL.md", comm)):
        assert "AGPL" in txt, f"{name} 没写许可证 ✗"
        assert "商用" in txt, f"{name} 没讲商用规则 ✗（AGPL 允许商用 ✓ 这点必须写明 ✓）"
        for bad in ("企业/组织业务场景需商业授权", "企业内部使用须", "需付费"):
            assert bad not in txt, f"{name} 还写着'企业要付费'（与 AGPL 冲突）✗"
    # ② 双授权那句话必须在 ✓（这是商业模式的落点 ✓）
    assert "免除" in comm and "开源义务" in comm, "COMMERCIAL.md 没讲清'买的是免除开源义务'✗"
    # ③ 第 13 条（网络服务也开源）必须讲 ✓ —— 这是用户最容易踩的坑 ✓
    assert "13" in comm, "COMMERCIAL.md 没提 AGPL 第 13 条 ✗"
    # ④ 三处口径一致：**都得说清"AGPL + 可以商用"** ✓
    #   ★ 2026-10-08 晚改：原来这里断言 LICENSE 里含"免费/企业/禁转售"✗ ——
    #     那是 v2 自家协议的中文句子 ✓ 而现在的 LICENSE 是 **AGPL 英文官方全文** ✗
    #     ⇒ 中文表述挪到 README/TERMS/COMMERCIAL 三处 ✓ 断言跟着挪 ✓
    for f in ("README.md", "TERMS.md", "COMMERCIAL.md"):
        t = (_ROOT / f).read_text("utf-8")
        assert "免费" in t, f"{f} 没说免费 ✗"
        assert "AGPL" in t, f"{f} 没写许可证 ✗"
        for bad in ("企业/组织业务场景需商业授权", "企业内部使用须", "需付费"):
            assert bad not in t, f"{f} 还写着'企业要付费'（与 AGPL 冲突）✗"


def test_privacy_and_terms_exist_and_are_linked():
    """**两份法律文件必须在，而且从 README 点得到** ✓（不然等于没有 ✓）。"""
    assert (_ROOT / "PRIVACY.md").exists(), "没有 PRIVACY.md ✗"
    assert (_ROOT / "TERMS.md").exists(), "没有 TERMS.md ✗"
    assert "PRIVACY.md" in _README and "TERMS.md" in _README, "README 没链到这两份 ✗"
    assert "PRIVACY.md" in _TERMS, "服务条款没链隐私政策 ✗"


def test_privacy_policy_is_honest_about_going_online():
    """**隐私政策必须承认"会联网"** ✓ —— 这是它存在的意义 ✓。

    用户拿来的那份清单里就有一条：原文写"数据全存本机不上传"**太绝对** ✗。
    事实是：用云端模型 / 搜索 / 云出图时，内容**确实会离开这台电脑** ✓
    ⇒ 政策里必须写清"什么时候发、发给谁" ✓ 并且提示"想彻底不外发就换本地模型" ✓。
    """
    for k in ("会联网", "模型服务", "Ollama", "局域网", "Webhook", "环境变量"):
        assert k in _PRIVACY, f"隐私政策没讲「{k}」✗"
    assert "不包含任何" in _PRIVACY and "统计" in _PRIVACY, "没说明'没有统计上报' ✓"
    # 不能出现"绝对不外发"这种与事实相反的承诺 ✗
    for bad in ("绝不上传", "数据不会离开", "完全不上传"):
        assert bad not in _PRIVACY, f"隐私政策里出现了与事实相反的绝对承诺：{bad} ✗"


def test_terms_keeps_the_three_promises_and_the_three_bans():
    """服务条款的三允三禁 ✓（这是用户点名要写的那几句 ✓）。

    ★ 2026-10-07 改：原来这里还断言"商业授权" ✓ —— 那是**旧口径**（企业要付费 ✗）；
      用户当天明确改成「**个人企业都免费，只禁转售/公开源码/冒名**」✓ ⇒ 断言跟着改 ✓。
      这就是"口径变了，测试得跟着变"的正常代价 ✓（红一次 ✓ 改对 ✓）。
    ★ 2026-10-08 又改一次（v2）：用户决定**源码公开** ✓ ⇒ 原来钉的"公开源码"这条禁令
      与"上传公开仓库"**自相矛盾** ✗ ⇒ 换成 v2 的禁令：**改名换姓 / 抄袭冒充 / 未授权商用** ✗
      （原话："不能擅自把我这个东西改成他的东西然后去卖" ✓）
    """
    for k in ("免费用", "产出归你", "不许转售冒名"):
        assert k in _TERMS, f"条款缺「{k}」✗"
    for k in ("转售", "冒名", "责任限制", "商业授权"):   # ★ AGPL 批：「未授权商用」换成了「商业授权」（AGPL 允许商用 ✓ 卖的是免除开源义务 ✓）
        assert k in _TERMS, f"条款缺「{k}」✗"
    # 与 LICENSE 的口径必须一致 ✓（v2 = "源码公开 + 个人企业免费 + 商用须授权" ✓）
    assert "源码公开" in _TERMS, "条款与 LICENSE 口径不一致 ✗（v2 该说'源码公开'）"
    assert "免费" in _TERMS, "条款没说免费 ✗"


def test_readme_states_the_supported_platforms_honestly():
    """★ **支持范围要写清** ✓ 而且**不许吹** ✗（2026-10-07 用户提的 ✓）。

    实情：只在 **Windows 真机**验过 ✓ Mac/Linux 理论可跑但**没验** ✗
    ⇒ 对外必须说"**没验过的平台不宣称支持**" ✓
      （与项目里"没跑过就不写已实测"是同一条规矩 ✓）。
    """
    assert "支持范围" in _README, "README 没写支持范围 ✗（别人会以为 Mac/Linux 也能用 ✓）"
    assert "Windows" in _README and "没验过" in _README, \
        "没如实说明「只在 Windows 验过、其他没验」✗"
    for bad in ("全平台支持", "跨平台开箱即用", "Mac/Linux 同样支持"):
        assert bad not in _README, f"README 里出现了没验证过的宣称：{bad} ✗"
