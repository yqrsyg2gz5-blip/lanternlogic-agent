"""★ 结构化交付（2026-10-05 第 4 条，依据 MetaGPT 的 Structured Outputs）。

## 为什么

MetaGPT 的原话：**agents communicate through documents and diagrams (structured outputs),
rather than dialogue** —— 因为"闲聊式协作"会像**电话游戏**一样在传递中失真。
他们的做法给每个角色定了**产出模式**：产品经理出 PRD（用户故事 + 需求池），
架构师出**系统设计（File Lists / Data Structures / Interface Definitions）**，
工程师按设计实现**指定的类与函数**，QA 出**测试用例**。

**我们的现场正是它的反面**：成员把成果写成一大段话发群里，下一棒靠这段话理解 ✗。
（这是四条"跑不完"病因里的第 4 条。）

## 做法（固定小节 = 可检查、可交接）

| 这步是什么活 | 必须写的小节 |
|---|---|
| 设计 / 架构 / 契约 | **文件清单**、**接口定义**、**数据结构** |
| 实现 / 编码 / 脚本 | **改动文件**、**自测命令** |
| 测试 / 自检 / 验收 | **用例清单**、**运行结果** |

三处用它：
1. **派活时**：工作单里加一段【交付格式（必须按小节写，缺了会被验收打回）】
2. **交接时**：下游工作单里先给上游的**小节内容**（不是一大段白话），再给一句摘要
3. **验收的低层（机器查）**：缺小节 ⇒ **直接打回**（确定性判定，不花模型钱、不会放水）

近义写法也认（模型不一定逐字照抄标题），避免无谓打回。
"""
from __future__ import annotations

from app.team import TeamStore


def test_required_sections_by_kind_of_work():
    assert TeamStore.required_sections("定订单服务的 API 契约", "docs/api.md") == \
        ["文件清单", "接口定义", "数据结构"]
    assert TeamStore.required_sections("按契约实现订单接口", "代码") == ["改动文件", "自测命令"]
    assert TeamStore.required_sections("写自检脚本并真跑", "test.py") == ["用例清单", "运行结果"]
    assert TeamStore.required_sections("写一段宣传语", "文案") == []


def test_missing_sections_judges_substance_not_wording():
    """★★ 2026-10-06 **8 轮真跑逼出来的大改** ✗✗ —— 这条检查从"查词"改成"查实质" ✓。

    一天里它误判了 **4 次** ✓：实现类/测试类判错 ✓ 单干要不要并集 ✓ "不写测试"被当关键词 ✓
    最后一次最典型（第 8 轮）：工作区里 `wordcount.py`、`test_wordcount.py` **都在** ✓、
    交付里也写了改动和测试 ✓，可就因为它的小节标题**没逐字照抄我那四个词** ✗ ⇒
    判"没交" ⇒ 打回 3 次 ⇒ **整项失败** ✓✓。

    公开经验也是这么说的 ✓：用评分标准验证智能体时，
    **"点名具体的工具 / 文件格式 / 运行时"是被明确点名的错误做法** ✗（判实质，不判叫法 ✓）。

    ⇒ 现在：**名字对得上 ✓ 或者 有那个实质** ✓ 就算交 ✓；什么都没交仍然会缺 ✓。
    """
    from app.team import TeamStore as T

    # 第 8 轮那种真实交付：标题不逐字，但"东西都在" ✓
    real = ("本轮完成情况：\n"
            "- 新建 wordcount.py：读取文件输出行数/词数/字符数\n"
            "- 新建 test_wordcount.py：11 条断言\n\n"
            "测试跑了：\n$ python -m pytest test_wordcount.py -q\n11 passed in 0.31s\n")
    assert T.missing_sections(real, ["改动文件", "自测命令", "用例清单", "运行结果"]) == [], \
        "东西都在却被判'缺小节'——就是那个连打回 3 次的误判 ✗"

    # 真没交 ⇒ 照样缺 ✓（别把检查改废了 ✗）
    assert T.missing_sections("我做完了，挺好的。", ["改动文件", "自测命令"]) == \
        ["改动文件", "自测命令"]
    assert T.missing_sections("", ["改动文件"]) == ["改动文件"]
    # 确认性验收只有一句话 ⇒ 算交 ✓（它本来就没法写更多 ✓）
    assert T.missing_sections("❌ 项目验收不通过：主命令能跑，但缺测试文件。", ["验收结论"]) == []


def test_solo_task_requires_both_section_families():
    """★★ 2026-10-06（**评测台第四轮抓到的** ✗）：

    小活只派了一项 ⇒ 同一个人**又实现又写测试** ✓，它交的是「用例清单（11 条，全通过）」✓ ——
    而系统只按"实现类"要「改动文件 / 自测命令」✗ ⇒ 判它"交付摘要为空" ⇒ 打回 3 次 ⇒ **失败** ✓✓
    （成本倒是砍半了 ✓ 38.8 万 → 17.7 万 tok ✓ 但活没做成 ✗ —— 这种"便宜"不算数 ✓）

    修法两条 ✓：
    · **单干（分工单只有一项）⇒ 两套小节都要**（并集 ✓，不是二选一 ✗）
    · 关键词判定**先去掉否定短语** —— "（只写主程序、**不写测试**）"里带"测试" ✗
      会把实现项误判成"两样都干"（本班验证时当场撞到 ✓）
    """
    from app.team import TeamStore as T

    solo = T.required_sections("写个 wordcount.py 并真跑", "wordcount.py", solo=True)
    assert solo == ["改动文件", "自测命令", "用例清单", "运行结果"], solo
    # 多项里的"实现项"：任务书带"不写测试"⇒ 仍然只要实现类 ✓
    impl = T.required_sections("按契约实现 todo.py（只写主程序、不写测试）", "todo.py")
    assert impl == ["改动文件", "自测命令"], impl
    assert T.required_sections("写用例并真跑", "test_a.py") == ["用例清单", "运行结果"]
    assert T.required_sections("定接口契约", "docs/api.md") == ["文件清单", "接口定义", "数据结构"]


def test_acceptance_items_are_classified_before_impl_and_test():
    """★★ 2026-10-06（**评测台第二轮真跑抓到的自相矛盾** ✗）：

    "确认性验收"的任务书里写着「别新写验收脚本 ✗、≤5 条断言都不必 ✓」，
    底下却按测试类要它交「用例清单」✗ ⇒ **必然**被"缺小节"打回 ✓（实测又白跑一轮 ✗）。

    ★ 而且验收任务书里本身带"别顺手重写**实现**"这种字眼 ✓ ⇒ 关键词排下去会被判成实现类 ✗
    （本班验证时当场撞到 ✓）⇒ **验收类必须整体提前判** ✓。
    """
    from app import main as m
    from app.team import TeamStore as T

    light = m._project_acceptance_task("做个小工具", "a.py", has_failures=True)
    full = m._project_acceptance_task("做个小工具", "a.py", has_failures=False)
    assert T.required_sections(light, "验收记录") == ["验收结论"], light[:80]
    full_need = T.required_sections(full, "验收记录")
    assert full_need == ["验收结论", "运行结果"], full_need      # 完整终验要看到真实输出 ✓
    # 三条老规矩不能被带坏 ✓（★ 注意"实现"那条已按新语义改成**并集** ✓ 见上一条测试 ✓）
    assert T.required_sections("按契约实现 todo.py 并真跑自测", "todo.py") == \
        ["改动文件", "自测命令", "用例清单", "运行结果"]
    assert T.required_sections("写用例并真跑", "test_a.py") == ["用例清单", "运行结果"]
    assert T.required_sections("定接口契约", "docs/api.md") == ["文件清单", "接口定义", "数据结构"]


def test_implementation_wins_over_test_when_both_appear():
    """★ 2026-10-06 实测的分类坑：任务书写成"按契约实现 todo.py，并真跑一遍自测"（两类关键词都有 ✗）。

    ★★ 规则后来**改过一次**（2026-10-06 评测台第四轮 ✓）：原来判"实现优先、只要实现类" ✓，
      结果撞上"小活一个人又实现又写测试"⇒ 它交的「用例清单」被判没交 ⇒ 打回 3 次 ⇒ 失败 ✗✓。
      ⇒ 现在：**两样都干 ⇒ 两套小节都要（并集）** ✓；只有一类 ⇒ 只要求那一类 ✓。
      （"实现优先"这条**不再成立** ✓ —— 所以这条测试也跟着改了 ✓，不是绕过 ✓。）
    """
    # 两样都干 ⇒ 并集 ✓
    assert TeamStore.required_sections("按契约实现 todo.py，并真跑一遍自测", "todo.py") == \
        ["改动文件", "自测命令", "用例清单", "运行结果"]
    # 只有测试类 ⇒ 只要测试类 ✓
    assert TeamStore.required_sections("写自检脚本并真跑", "test.py") == ["用例清单", "运行结果"]
    assert TeamStore.required_sections("项目的最终验收", "验收记录") == ["用例清单", "运行结果"]


def test_missing_sections_only_flags_what_is_really_missing():
    need = ["文件清单", "接口定义", "数据结构"]
    assert TeamStore.missing_sections("文件清单：a.py\n接口定义：f()\n数据结构：Order", need) == []
    assert TeamStore.missing_sections("文件清单：a.py", need) == ["接口定义", "数据结构"]
    # 近义写法也认（避免为了一个词反复打回）
    assert TeamStore.missing_sections("文件列表：a.py\nAPI：GET /x\n字段：id,total", need) == []
    assert TeamStore.missing_sections("", need) == need          # 空交付 ⇒ 全缺
    assert TeamStore.missing_sections("啥也没写", []) == []      # 不强制的活不挑刺


def test_pick_sections_pulls_structured_blocks_out_of_a_delivery():
    reply = ("【架构师交付】契约已定稿。\n"
             "## 文件清单\n- docs/api.md\n- src/orders.py\n"
             "**接口定义**：GET /orders、POST /orders\n"
             "▸ 数据结构：Order{id, items, total}\n"
             "另外随便写点别的：今天天气不错")
    got = dict(TeamStore.pick_sections(reply))
    assert "文件清单" in got and "docs/api.md" in got["文件清单"]
    assert "接口定义" in got and "数据结构" in got
    assert TeamStore.pick_sections("") == []
    assert TeamStore.pick_sections("只有一段白话，没有任何小节标题") == []


def test_handoff_gives_structured_sections_not_a_wall_of_text():
    """★ 交接时把上游的**小节**给下游（这才叫"按文档协作"）。"""
    g = {"leader_plan": [
        {"name": "架构师", "task": "定契约", "output": "docs/api.md", "status": "done",
         "attachments": ["docs/api.md"],
         "reply": "## 文件清单\n- docs/api.md\n## 接口定义\nGET /orders\n## 数据结构\nOrder{id}"},
    ]}
    item = {"name": "程序员", "task": "按契约实现订单接口", "output": "src/orders.py",
            "depends_on": ["架构师"]}
    text = TeamStore._leader_handoff(g, item)
    assert "上一步的交付" in text
    assert "▸ 文件清单" in text and "docs/api.md" in text
    assert "▸ 接口定义" in text and "▸ 数据结构" in text
    # 实现类这一步必须被要求写"改动文件 / 自测命令"
    assert "【交付格式" in text and "改动文件" in text and "自测命令" in text


def test_format_section_comes_first_and_rework_carries_the_previous_version():
    """★ 2026-10-06 降本（终验跑实测：小项目 79 万 tok，大头是"打回后重做"）：
    · 交付格式要求**前置到工作单开头**（原来在末尾，模型常漏读 ⇒ 实测"缺小节"打回了 2 次）
    · 打回重做时**把上一版交付带回来**（否则它只能凭记忆整份重写，既贵又丢上次做对的部分）
    """
    g = {"leader_plan": [], "leader_goal": "做待办 CLI"}
    item = {"name": "程序员", "task": "按契约实现 todo.py", "output": "todo.py",
            "depends_on": [], "reroll_advice": "缺「运行结果」小节",
            "reply": "【程序员交付】改动文件：todo.py（198 行）\n自测命令：python todo.py add x"}
    text = TeamStore._leader_handoff(g, item)
    head = text.splitlines()[:4]
    assert any("交付格式" in ln for ln in head), head          # 格式要求在**开头**
    assert "改动文件" in head[0] or any("改动文件" in ln for ln in head), head
    assert "上一次被打回的原因" in text and "缺「运行结果」小节" in text
    assert "你上一版交了什么" in text and "todo.py（198 行）" in text   # 带回了上一版
    # 长交付要截断（别把上下文撑爆）
    item2 = dict(item, reply="x" * 5000)
    t2 = TeamStore._leader_handoff(g, item2)
    assert "…（略）" in t2


def test_missing_attachments_fall_back_to_paths_in_the_reply(tmp_path, monkeypatch):
    """★★ 2026-10-06 真群实测：文件**明明在工作区**，可这一版没声明附件 ⇒
    低层直接判"没有交付任何文件"⇒ 整步失败 ✗（那一轮因此连挂 3 次、下游全挡住、多烧 24 万 tok）。

    修法：附件为空时，从交付正文里**兜路径** —— 但**只认工作区里真实存在的**：
    "我打算建 xxx.py" 这种话不能当成交付（宁可漏认，不可误认）。
    """
    from app import main as m

    ws = tmp_path / "ws"
    (ws / "sub").mkdir(parents=True)
    (ws / "todo.py").write_text("print(1)", encoding="utf-8")
    (ws / "sub" / "report.md").write_text("ok", encoding="utf-8")
    monkeypatch.setattr(m.store, "workspace_dir", lambda tid: ws)
    item = {"task": "按契约实现", "output": "todo.py"}

    ok, detail = m._low_level_check(
        "t1", [], reply="改动文件：todo.py（新增）\n自测命令：python todo.py list\n"
                        "另外我打算建 future.py", item=item)
    assert ok is True, detail
    assert "附件清单为空" in detail and "todo.py" in detail, detail
    assert "future.py" not in detail, "不存在的路径不能算交付"

    # 正文里也找不到真实文件 ⇒ 仍然如实判"没有交付任何文件"
    # （先补齐必需小节，才能走到"附件"那一关 —— 结构检查在它前面，这是设计如此）
    ok2, detail2 = m._low_level_check(
        "t1", [], reply="改动文件：无\n自测命令：没跑\n我做完了，挺好的", item=item)
    assert ok2 is False and "没有交付任何文件" in detail2, detail2
    # 正常声明附件时，行为不变（同样要先满足小节要求 —— 结构检查在附件检查之前）
    ok3, _ = m._low_level_check("t1", ["todo.py"], reply="改动文件：todo.py\n自测命令：python todo.py list",
                                item=item)
    assert ok3 is True


def test_low_level_check_rejects_a_delivery_missing_sections(tmp_path, monkeypatch):
    """★ 缺小节 ⇒ **低层直接打回**（确定性，不花模型钱、不会放水）。"""
    from app import main as m

    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "docs").mkdir()
    (ws / "docs" / "api.md").write_text("内容", encoding="utf-8")
    monkeypatch.setattr(m.store, "workspace_dir", lambda tid: ws)
    item = {"task": "定契约（设计类）", "output": "docs/api.md"}
    ok, detail = m._low_level_check("t1", ["docs/api.md"], reply="契约写好了，挺好的", item=item)
    assert ok is False and "缺必需小节" in detail, detail
    ok2, detail2 = m._low_level_check(
        "t1", ["docs/api.md"],
        reply="文件清单：docs/api.md\n接口定义：GET /orders\n数据结构：Order{id}", item=item)
    assert ok2 is True, detail2
