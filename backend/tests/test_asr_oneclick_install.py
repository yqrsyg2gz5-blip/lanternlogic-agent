# -*- coding: utf-8 -*-
"""★ 2026-10-07 「一键装 + 一键下模型」（用户原话：*"我想要的就是一键能安装，然后还能使用这种的"*）

## 为什么要有它

在此之前，界面上那个按钮叫「用这个」✓ **只切配置** ✗ ——
依赖没装、模型没下 ⇒ 点完**一说话就报错** ✓（报错虽然可照做 ✓ 但用户要的是"能用" ✗）。

## 三条红线（每条都有断言 ✓）

1. **绝不执行用户输入的任意命令** ✗ —— 跑的只有**两条写死的**：
   `sys.executable -m pip install -U qwen-asr modelscope` ✓ 与 `-m modelscope download --model …` ✓
   用 `sys.executable` ✓ 保证装进**本项目 venv** ✓（不然会装到别的 Python 里去 ✓ 白装 ✗）
2. **不装作成功** ✗ —— pip 返回 0 **不等于**能 import ✓ ⇒ 装完**再探一次** ✓ 才敢报成功 ✓
3. **不偷偷下大文件** ✗ —— 下模型要用户点 ✓ 而且显示"已下多少 MB" ✓（1–4GB 的量级 ✓ 有权先知道 ✓）
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app import local_install as A  # noqa: E402

_SRC = pathlib.Path(A.__file__).read_text("utf-8")


def test_only_two_hardcoded_commands_and_no_shell():
    """**命令是写死的 + 不走 shell** ✓ —— 这是"它不是一个任意命令执行口子"的依据 ✓。"""
    assert "shell=True" not in _SRC, "用了 shell=True ✗（那就是命令注入口子 ✓）"
    assert "sys.executable" in _SRC, "没用本项目的 python ✗ ⇒ 可能装到别的环境里去 ✓"
    assert '"pip", "install", "-U", "qwen-asr", "modelscope"' in _SRC, "装依赖的命令不是写死的那条 ✗"
    # ★ 2026-10-07 实测补的：**要顺带装国内下载器** ✓
    #   只装 qwen-asr 时环境里没下载器 ⇒ 下模型直连 HuggingFace ✓ 国内常慢甚至不通 ✗
    assert "modelscope" in _SRC, "没顺带装国内下载器 ⇒ 下模型会走直连 HF（国内常慢/不通）✗"
    assert "modelscope" in _SRC and "download" in _SRC, "下模型的命令没写 ✗"
    # 不接受任何用户输入拼进命令 ✓
    assert "os.system" not in _SRC and "eval(" not in _SRC, "别用 os.system/eval ✗"


def test_install_rechecks_import_before_claiming_success():
    """**pip 成功 ≠ 能用** ✗ ⇒ 装完必须**再探一次** ✓ 才报成功 ✓（本项目不装作成功 ✓）。"""
    assert "find_spec" in _SRC, "装完没有再探一次 ✗"
    assert "import 不到" in _SRC, "没处理'pip 说成功但其实 import 不到'那种情况 ✗"


def test_model_goes_into_our_own_data_dir():
    """模型下进 **`backend/data/asr_models/`** ✓ —— 跟其它数据一个地方 ✓ 好清理 ✓ 不会被误当缓存删掉 ✓。"""
    p = A.model_cache_dir("0.6b")
    assert "data" in p.parts and "asr_models" in p.parts, p
    assert p.parts[-1] == "0.6b", p


def test_unknown_tier_is_rejected_before_doing_anything():
    """档位只认两个 ✓（官方真实存在的 ✓）—— 别的当场拒 ✗ 不开工 ✓。"""
    r = A.pull_model("1.7b-quant")            # 那个是我们以前编的 ✗ 官方没有 ✓
    assert r["ok"] is False and "未知档位" in r["detail"], r
    assert A.status()["state"] == "idle", "被拒了却还是开工了 ✗"


def test_only_one_job_at_a_time():
    """**同一时刻只允许一个** ✓ —— 两条 pip / 两个下载撞在一起会互相毁掉 ✓。"""
    assert "已经有一个安装/下载在跑了" in _SRC, "没有并发保护 ✗"
    assert "_LOCK" in _SRC and "threading" in _SRC, "没有锁 ✗"


def test_status_reports_real_output_and_bytes():
    """状态里要能看到**真实输出最后几行**与**已下字节** ✓（用户要的"进度" ✓）。"""
    st = A.status()
    for k in ("kind", "state", "lines", "bytes", "elapsed", "target", "error"):
        assert k in st, f"状态缺字段 {k} ✗"
    assert isinstance(st["lines"], list) and isinstance(st["bytes"], int), st


def test_endpoints_are_registered():
    """三个接口都得在 ✓（装 / 下 / 看状态 ✓）。"""
    import pathlib as _p
    main = (_p.Path(__file__).resolve().parents[1] / "app" / "main.py").read_text("utf-8")
    for route in ('"/api/v1/settings/asr/install"', '"/api/v1/settings/asr/pull"',
                  '"/api/v1/settings/asr/install/status"'):
        assert route in main, f"接口没注册：{route} ✗"
