# -*- coding: utf-8 -*-

"""★★ 全盘扫描必须在**执行层**硬拦（2026-10-14 真机事故复发）。



## 现场（用户机器上真跑出来的）



```

三个 find 进程从根目录扫，父进程早退出了，它们还在扫：

  find / -maxdepth 6 -name SKILL.md -path *卡点*        PID 648   烧 7,816 秒 CPU

  find / -maxdepth 6 -name qa_video.py                  PID 23448 烧 7,320 秒 CPU

  find / -maxdepth 6 -type d -iname *卡点*              PID 34732 烧 7,112 秒 CPU

⇒ 合计 **6.2 小时 CPU**、CPU 负载 18%、风扇一直转 ✗

```



## 为什么"写进技能文档"不够



技能里**早就写明**了：



> ⛔ 绝对不要"扫全盘"找东西

> `find / -maxdepth 6 -name tools -type d` # ✗✗ 禁止！从根目录扫 C 盘 + D 盘



**但它还是做了** ✗ —— 而且专挑 `-maxdepth 6` 这种"看起来有节制"的写法。

⇒ 文档约束办不到的事，**要在执行层拦** ✓



## 拦什么（判据要准，不能误伤正常命令）



拦：搜索根是**文件系统根**（`/` 或盘符根 `C:/` / `/c` / `/d` …）的 find/grep/ls -R

放行：搜索根是**当前目录或工作区子路径**的（`find . -maxdepth 3` / `find tools/ -name x`）✓

"""

from __future__ import annotations



import pathlib

import sys



BACKEND = pathlib.Path(__file__).resolve().parents[1]

sys.path.insert(0, str(BACKEND))





def _guard():

    from app.executors.local import whole_disk_scan_reason

    return whole_disk_scan_reason





def test_guard_exists():

    from app.executors import local

    assert hasattr(local, "whole_disk_scan_reason"), (

        "执行层没有「全盘扫描」拦截 ⇒ 光靠文档拦不住（真机已复发一次，烧 6.2 小时 CPU）✗"

    )





def test_blocks_scans_from_filesystem_root():

    """★ 真机那三条**必须全部拦下**（它们是本次事故的原样命令）。"""

    g = _guard()

    BAD = [

        "find / -maxdepth 6 -name SKILL.md -path *卡点*",

        "find / -maxdepth 6 -name qa_video.py",

        "find / -maxdepth 6 -type d -iname *卡点*",

        "find / -type f -name SKILL.md",

        "find / -name tools -type d",

        "ls -R /",

        "grep -r foo /",

        "grep -rn 'x' / --include=*.md",

        "find /c -name SKILL.md",

        "find /d/ -name qa_video.py",

        "cd / && find . -name SKILL.md",

    ]

    for cmd in BAD:

        r = g(cmd)

        assert r, f"这条没拦住 ⇒ 又会烧掉几小时 CPU ✗\n  {cmd}"





def test_allows_scoped_searches():

    """★ 别误伤：**限定在自身工作区**的搜索是正常操作，必须放行 ✓"""

    g = _guard()

    OK = [

        "find . -maxdepth 3 -name SKILL.md",

        "find . -maxdepth 3 -type d -name tools",

        "find tools/ -name '*.py'",

        "find ./data -name beats.json",

        "ls -la",

        "ls -R tools/",

        "grep -rn 'ffmpeg' tools/",

        "grep -n 'caption' tools/qa_video.py",

        "find /d/AI/agent-shell/backend -name SKILL.md",

        "find \"$PWD\" -maxdepth 2 -name '*.json'",

        "sed -n '130,200p' tools/render/render.mjs",

        "python tools/qa_video.py out/final.mp4 --workspace .",

    ]

    for cmd in OK:

        r = g(cmd)

        assert not r, f"正常命令被误拦 ✗（会挡住它干活）\n  {cmd}\n  理由：{r}"





def test_reason_is_actionable_and_chinese():

    """★ 拦下时要给**人话 + 替代做法**（不然模型只会换个写法再撞一次）。"""

    g = _guard()

    r = g("find / -maxdepth 6 -name SKILL.md")

    assert r and ("文件系统根" in r or "全盘" in r), f"理由没说清是什么问题：{r!r}"

    assert "find ." in r or "工作区" in r, (

        f"没给替代做法（模型会换个写法再撞）✗：{r!r}"

    )





def test_guard_is_wired_into_shell():

    """★ 接进 `_shell()` 才算数（写了不接 = 死代码）✗"""

    src = (BACKEND / "app" / "executors" / "local.py").read_text("utf-8", errors="replace")

    assert "whole_disk_scan_reason(" in src, "拦截函数没被调用 ⇒ 是死代码 ✗"

    i = src.find("async def _shell(")

    j = src.find("async def ", i + 20)

    seg = src[i:j]

    assert "whole_disk_scan_reason(" in seg, (

        "拦截没接在 `_shell()` 里 ⇒ 命令照样会跑出去 ✗"

    )

