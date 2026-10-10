# -*- coding: utf-8 -*-
"""一键启动的**体检**（Phase 1 ②）：把"起不来"的每一种原因说清楚，并给出可复制的下一步。

为什么要它：
  现在的 `start.bat` 假定你已经跑过 `install.bat`、装了 Node、建了 venv、构建过前端；
  任何一步没做，用户看到的就是一句拼音提示然后窗口一闪。新用户根本不知道
  "缺什么、去哪儿补"。本脚本把"缺什么 + 怎么补"变成**机器可读 + 人话可读**的报告。

用法（人看）：
    backend\\.venv\\Scripts\\python.exe scripts\\preflight.py
    python scripts\\preflight.py --json        # 给脚本/测试用
    python scripts\\preflight.py --port 8642   # 覆盖端口检查
退出码：0 = 可以启动；1 = 有阻断项（报告里逐条给出补法）。
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 出厂自带的技能库（目录名就是技能名）。用于**装机包完整性点名核对**：
# 少一个 / 名字被解包工具改坏，都要在这里响。新人加技能时，
# `tests/test_preflight.py::test_expected_skill_list_matches_repo` 会红，逼着同步这份名单。
EXPECTED_SKILLS = ("代码工程", "写作助手", "幻灯片制作", "数据分析可视化",
                   "桌面整理", "网站建设", "联网研究", "视频生成")
_SKILL_FIX = ("重新获取完整包：git clone 官方仓库，或用 7-Zip 解压官方 zip；"
              "★ 不要用 `git archive | tar` 在 Windows 上解包 —— 实测它会丢掉中文名目录"
              "（本班演练：266 个文件只解出 201 个，技能目录要么没了、要么名字变成乱码）")

# 每项体检的结论：(级别, 代号, 人话, 可复制的下一步)
OK, WARN, FAIL = "ok", "warn", "fail"


def _venv_python(root: Path) -> Path:
    return root / "backend" / ".venv" / "Scripts" / "python.exe" if os.name == "nt" \
        else root / "backend" / ".venv" / "bin" / "python"


def _port_busy(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _config_port(root: Path) -> int:
    """从 config.json 读端口（读不到就用默认 8642；顺带判断"配置文件在不在"）。"""
    p = root / "config.json"
    if not p.exists():
        return 8642
    try:
        return int(json.loads(p.read_text("utf-8-sig")).get("server", {}).get("port") or 8642)
    except Exception:
        return 8642


def _probe_python(exe: str) -> tuple[bool, str]:
    """真跑一下解释器：能不能 import encodings（= 是不是**完整**的 Python）。

    ★ 为什么必须真跑（2026-10-04 装机演练实测）：
      本机 PATH 里的 `python` 指向一个**只剩 python.exe+DLLs、没有 Lib\\ 的残件**
      （大概是某次中断的安装留下的）。它 `python --version` 照样能打印，
      但 `python -m venv` 当场炸：
          Could not find platform independent libraries <prefix>
          Fatal Python error: Failed to import encodings module
      而 install.bat 用的就是裸 `python` ⇒ 用户在"别人的机器"上装不上，
      还只能看到这句看不懂的话。所以体检改为**真跑一次**，并去找能用的替代解释器。
    """
    import subprocess
    try:
        r = subprocess.run([exe, "-c",
                            "import encodings, sys; print('DSH_PY_OK', '.'.join(map(str, sys.version_info[:3])))"],
                           capture_output=True, text=True, timeout=20)
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:80]}"
    if r.returncode != 0:
        msg = (r.stderr or r.stdout or "").strip()
        return False, (msg.splitlines()[-1][:120] if msg else "非零退出")
    # ★ 光看返回码不够：任何"能跑且退出 0"的程序都会蒙混过关（拿 .bat 当解释器试过）。
    #   所以让探针打印一个**哨兵**，认哨兵，不认"看起来像版本号"。
    out = (r.stdout or "").strip()
    if not out.startswith("DSH_PY_OK "):
        return False, f"不是一个 Python 解释器（输出：{out[-60:]!r}）"
    return True, out.split(" ", 1)[1].strip()


def _alternative_pythons() -> list[str]:
    """能用的替代解释器（给"PATH 里的坏了"的用户一条出路；只报路径，不乱装）。"""
    found: list[str] = []
    # ① uv 托管的 python（本机 venv 就是这么来的）
    uv_root = Path(os.environ.get("APPDATA", "")) / "uv" / "python"
    if uv_root.is_dir():
        for d in sorted(uv_root.iterdir()):
            exe = d / "python.exe"
            if exe.exists():
                found.append(str(exe))
    # ② Windows 的 py 启动器
    for c in (Path(os.environ.get("WINDIR", r"C:\Windows")) / "py.exe",
              Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Python"):
        if c.is_file():
            found.append(str(c))
        elif c.is_dir():
            for d in sorted(c.iterdir()):
                exe = d / "python.exe"
                if exe.exists():
                    found.append(str(exe))
    return found


def check(root: Path = ROOT, port: int | None = None) -> list[dict[str, str]]:
    """体检并返回结论清单（**只读，不改任何东西**）。"""
    out: list[dict[str, str]] = []

    # ① Python（**真跑一次**：版本号能打印 ≠ 解释器是完整的）
    major, minor = sys.version_info[:2]
    if (major, minor) >= (3, 11):
        out.append({"level": OK, "code": "python", "msg": f"Python {major}.{minor}", "fix": ""})
    else:
        out.append({"level": FAIL, "code": "python",
                    "msg": f"Python 版本过低（{major}.{minor}）——本应用需要 3.11+",
                    "fix": "装一个 Python 3.12/3.13：https://www.python.org/downloads/ （安装时勾 Add to PATH）"})

    # ①' PATH 里的 `python` 是不是**完整**解释器（install.bat 用的就是它）
    ok_py, info_py = _probe_python("python")
    if ok_py:
        out.append({"level": OK, "code": "python_path", "msg": f"PATH 里的 python 可用（{info_py}）", "fix": ""})
    else:
        alts = _alternative_pythons()
        out.append({"level": FAIL, "code": "python_path",
                    "msg": f"PATH 里的 python 不是一个完整的 Python（{info_py}）——装环境会失败",
                    "fix": "换一个完整的 Python 3.11+（到 https://www.python.org/downloads/ 重装，勾 Add to PATH）；"
                           + (f"本机可用的其它解释器：{alts[:2]}。" if alts else "")
                           + "临时办法：双击 install.bat 前先 set AGENT_SHELL_PYTHON=<完整的python.exe 路径>"})

    # ② 依赖（venv + 关键包）
    vpy = _venv_python(root)
    if not vpy.exists():
        out.append({"level": FAIL, "code": "venv", "msg": "还没建 Python 虚拟环境（backend\\.venv）",
                    "fix": "双击 install.bat（或在仓库根跑：python -m venv backend\\.venv && "
                           "backend\\.venv\\Scripts\\python.exe -m pip install -r backend\\requirements.txt）"})
    else:
        probe = [str(vpy), "-c", "import fastapi, uvicorn, httpx"]
        import subprocess
        r = subprocess.run(probe, capture_output=True, text=True)
        if r.returncode == 0:
            out.append({"level": OK, "code": "deps", "msg": "依赖齐全（fastapi/uvicorn/httpx）", "fix": ""})
        else:
            out.append({"level": FAIL, "code": "deps", "msg": "虚拟环境里缺依赖（后端起不来）",
                        "fix": f"{vpy} -m pip install -r backend\\requirements.txt"})

    # ③ 配置文件
    if (root / "config.json").exists():
        out.append({"level": OK, "code": "config", "msg": "config.json 在位", "fix": ""})
    else:
        out.append({"level": FAIL, "code": "config", "msg": "缺 config.json（第一次用要复制一份模板）",
                    "fix": "copy contracts\\config.example.json config.json"})

    # ④ 界面
    if (root / "frontend" / "dist" / "index.html").exists():
        out.append({"level": OK, "code": "ui", "msg": "界面已构建（frontend\\dist）", "fix": ""})
    else:
        out.append({"level": FAIL, "code": "ui", "msg": "界面还没构建（frontend\\dist 不存在）——打开会是空白页",
                    "fix": "cd frontend && npm install && npm run build"
                           "（要装 Node.js：https://nodejs.org/ ；装了就会随安装一起构建）"})

    # ⑤ 技能库完整性（装机包体检 —— 本班"干净机器"演练实测的静默丢包）
    #   背景：本仓 266 个跟踪文件里有 **90 个名字含中文**（技能库的目录名全是中文：
    #   skills\代码工程\、写作助手\、幻灯片制作\…）。而 Windows 上用
    #   `git archive HEAD | tar -x` 解包时，bundled tar 对这些路径**逐条报错并跳过**
    #   （"Invalid empty pathname"）⇒ 解出来只有 201 个文件、技能库一个都不剩，
    #   而应用照常启动 —— Agent 只是**安静地**没了技能（"可用技能"那段提示词消失）。
    #   ⇒ 体检**点名核对**（不是数个数）：装出来的包里，技能目录名还必须是**原名**。
    #   本班实测更狠的一面：活下来的 6 个目录**名字被改坏**了
    #   （`桌面整理` → 变成 UTF-8 被按 GBK 读出来的乱码），数个数根本发现不了。
    skills_dir = root / "backend" / "skills"
    present = {d.name for d in skills_dir.iterdir() if d.is_dir()} if skills_dir.is_dir() else set()
    missing = [s for s in EXPECTED_SKILLS if s not in present]
    extra = sorted(present - set(EXPECTED_SKILLS))
    if not missing and not extra:
        out.append({"level": OK, "code": "skills",
                    "msg": f"技能库完整（{len(EXPECTED_SKILLS)} 个技能，名字正确）", "fix": ""})
    elif not present:
        out.append({"level": FAIL, "code": "skills",
                    "msg": "技能库不见了（backend\\skills 下 0 个技能）——安装包不完整",
                    "fix": _SKILL_FIX})
    else:
        out.append({"level": FAIL, "code": "skills",
                    "msg": f"技能库不完整：缺 {missing or '无'}"
                           + (f"；另有 {len(extra)} 个名字对不上的目录（解包时被改坏了）" if extra else ""),
                    "fix": _SKILL_FIX})

    # ⑥ 端口
    p = port or _config_port(root)
    if _port_busy(p):
        out.append({"level": WARN, "code": "port",
                    "msg": f"端口 {p} 已有服务在跑（可能就是本应用）——会直接用它，不会重复启动",
                    "fix": ""})
    else:
        out.append({"level": OK, "code": "port", "msg": f"端口 {p} 空闲", "fix": ""})
    return out


def blocking(items: list[dict[str, str]]) -> list[dict[str, str]]:
    return [i for i in items if i["level"] == FAIL]


def _out(text: str) -> None:
    """按控制台自己的编码打印（GBK 控制台也不会崩：编不出的字符降级成 ?）。

    为什么不用 emoji：zh-CN 的 cmd 默认是 GBK，emoji 直接 UnicodeEncodeError
    （本脚本第一版就是这么崩的）。标记一律用 ASCII，中文照常显示。
    """
    enc = (sys.stdout.encoding or "utf-8")
    sys.stdout.buffer.write(text.encode(enc, errors="replace"))
    sys.stdout.buffer.flush()


def render(items: list[dict[str, str]], port: int) -> str:
    icon = {OK: "[OK]", WARN: "[!] ", FAIL: "[X] "}
    lines = ["", "  ── 启动前体检 ───────────────────────────────"]
    for i in items:
        lines.append(f"   {icon[i['level']]} {i['msg']}")
        if i["fix"]:
            lines.append(f"        → {i['fix']}")
    bad = blocking(items)
    lines.append("")
    if bad:
        lines.append(f"   还有 {len(bad)} 项没准备好 —— 按上面的「→」逐条做，然后重新双击 start.bat。")
    else:
        lines.append(f"   一切就绪：界面会在 http://127.0.0.1:{port}/ 打开（浏览器稍后自动弹出）")
    lines.append("  ─────────────────────────────────────────────")
    lines.append("")
    return chr(10).join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    ap.add_argument("--print-url", action="store_true",
                    help="只打印界面地址（给 start.bat 取值用；体检不过也照样打印）")
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--port", type=int, default=None)
    a = ap.parse_args()
    root = Path(a.root)
    port = a.port or _config_port(root)
    items = check(root, port)
    if a.print_url:
        _out(f"http://127.0.0.1:{port}/" + chr(10))
        return 1 if blocking(items) else 0
    if a.json:
        _out(json.dumps({"port": port, "ready": not blocking(items), "items": items},
                        ensure_ascii=False, indent=2) + chr(10))
    else:
        _out(render(items, port))
    return 1 if blocking(items) else 0


if __name__ == "__main__":
    sys.exit(main())
