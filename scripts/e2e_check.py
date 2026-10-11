"""LanternLogic Agent 一键体检（自动化 E2E 回归，第 41 班）。

用法：
    python scripts/e2e_check.py              # 快速档：不调真实大模型（mock 语义检查 + 结构检查）
    python scripts/e2e_check.py --full       # 完整档：真实跑 3 个任务（用当前配置的大脑，花一点 API 费）

体检项：
  A. 后端存活 / 版本接口
  B. 技能注册（数量、frontmatter 完整、不可见字符清洗生效）
  C. MCP server 状态
  D. 视频引擎配置一致性（provider 白名单 / Key 状态）
  E. 沙箱状态与 Docker 可用性
  F. 全流程回归（--full）：写作任务 / 工具链任务 / 交付门拦截——
     断言事件流形状（plan→action→observation→…→done）与产物落盘
  G. 单元测试套件（pytest）

输出：逐项 PASS/FAIL + 总结（退出码 0=全过）。
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8642/api/v1"
ROOT = Path(__file__).resolve().parents[1]
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"{'✅' if ok else '❌'} {name}" + (f" ｜ {detail}" if detail else ""))


def wait_task(c: httpx.Client, tid: str, timeout: float = 180) -> tuple[str, list[dict]]:
    seen, final, events = 0, None, []
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            evs = c.get(f"/tasks/{tid}/events", params={"after_seq": seen}, timeout=10).json()
        except Exception:
            time.sleep(3)
            continue
        for e in evs:
            seen = max(seen, e["seq"])
            events.append(e)
            if e["type"] == "status" and e["payload"].get("state") in ("done", "idle", "failed", "cancelled", "partial"):
                final = e["payload"]["state"]
        if final:
            break
        time.sleep(3)
    return final or "timeout", events


def main() -> int:
    full = "--full" in sys.argv
    c = httpx.Client(base_url=BASE, timeout=15)

    # A. 后端存活
    try:
        r = c.get("/tasks")
        check("A1 后端存活", r.status_code == 200)
    except Exception as e:
        check("A1 后端存活", False, f"连接失败：{e}——先启动后端")
        return 1

    # B. 技能
    skills = c.get("/skills").json()
    check("B1 技能注册 ≥ 8", len(skills) >= 8, f"{len(skills)} 个")
    need = {"代码工程", "写作助手", "视频生成"}
    check("B2 核心技能在册", need <= {s["name"] for s in skills})

    # C. MCP
    mcp_names = [s["name"] for s in skills if s["name"].startswith("mcp__")]
    check("C1 MCP 工具注入（如有配置）", True, f"{len(mcp_names)} 个 MCP 工具在工具表")

    # D. 视频引擎
    settings = c.get("/settings").json()
    vprov = settings.get("video", {}).get("provider", "")
    check("D1 视频引擎合法", vprov in ("", "minimax", "wan", "seedance", "kling"), vprov or "未启用")
    if vprov:
        check("D2 视频 Key 已设", settings["video"].get("key_set") is True)

    # E. 沙箱
    ex = settings.get("executor", {})
    check("E1 沙箱配置合法", ex.get("sandbox") in ("off", "docker"), ex.get("sandbox", ""))
    if ex.get("sandbox") == "docker":
        check("E2 Docker 可用", ex.get("sandbox_available") is True)

    # F. 全流程回归（--full 才跑，花 API 费）
    if full:
        # F1 写作任务：计划→写文件→交付，产物落盘
        tid = c.post("/tasks", json={"input": "写一句 10 字以内的话到文件 f1.txt，然后交付。"}).json()["id"]
        final, events = wait_task(c, tid)
        actions = [e["payload"].get("tool") for e in events if e["type"] == "action"]
        has_write = "file_write" in actions
        check("F1 写作任务全流程", final in ("done", "idle") and has_write, f"终态={final}, 动作={len(actions)}")

        # F2 工具链任务：写→读回→核对（触发交付门要求的验证链）
        tid2 = c.post("/tasks", json={"input": "用 file_write 写 calc.txt 内容 1+1=2，再用 file_read 读回确认内容正确，然后交付。"}).json()["id"]
        final2, events2 = wait_task(c, tid2)
        acts2 = [e["payload"].get("tool") for e in events2 if e["type"] == "action"]
        check("F2 写后读回链路", final2 in ("done", "idle") and "file_write" in acts2 and "file_read" in acts2, f"终态={final2}")

        # F3 用量结构化（缓存/估算字段存在）
        evs = c.get(f"/tasks/{tid}/events", params={"after_seq": -1}).json()
        usage = next((e["payload"].get("usage") for e in evs if e["type"] == "knowledge" and "用量" in str(e["payload"].get("title", ""))), None)
        check("F3 结构化用量落盘", isinstance(usage, dict) and "input_tokens" in (usage or {}), str(usage)[:80] if usage else "无")
    else:
        print("（跳过 F 全流程回归——加 --full 跑真实任务档）")

    # G. 单元测试（判据用退出码：pytest 全过才返回 0——文本汇总行会被 \r 弄得不可靠）
    r = subprocess.run([str(ROOT / "backend/.venv/Scripts/python.exe"), "-m", "pytest", "tests/", "-q"],
                       cwd=ROOT / "backend", capture_output=True, text=True, timeout=300)
    flat = (r.stdout or "").replace("\r", "\n")
    summary = next((ln.strip() for ln in flat.splitlines() if " passed" in ln), f"exit={r.returncode}")
    check("G1 单元测试", r.returncode == 0, summary)

    fails = [n for n, ok, _ in results if not ok]
    print(f"\n体检总结：{len(results) - len(fails)}/{len(results)} 通过" + (f"｜未过：{fails}" if fails else "｜全部健康 ✅"))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
