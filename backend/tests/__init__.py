# -*- coding: utf-8 -*-
"""★ 2026-10-08：这个文件是**必须有的** ✗ —— 不是清洁癖，是修一个真踩到的坑 ✓

## 踩到的事（完整门当场抓出来的）

有 4 个既有测试文件互相借工具函数 ✓ 写法是 `from tests.test_approval_capability_matrix import ...` ✓
（还有 `tests.test_round9_fixes` / `tests.test_delivery_outcome` ✓ + `conftest.py` 也用了 ✓）。

而本仓的 `backend/tests` 原来**没有 `__init__.py`** ⇒ 按 PEP 420 它只是个**命名空间包** ✗。
**命名空间包永远输给同名的"真包"** ✓ —— 不管 `sys.path` 顺序 ✗。

2026-10-08 02:55，本机 site-packages 里多了一个**顶层 `tests` 真包** ✗
（`gruut` 2.4.0 —— MeloTTS 那系的依赖 —— 把自己的测试目录**打包进去了** ✗
 里面有 `test_en.py` / `test_g2p.py` 之类 ✓ 那是它自己的测试 ✓）。
⇒ 仓库里的 `import tests.xxx` 全被导到那个空包上 ✗ ⇒ **4 个文件采集失败** ✓
  （`pytest`：`1 warning, 4 errors in 1.28s` ✓ 报 `No module named 'tests.test_...'` ✓）
⇒ 提交被 pre-commit 拦下 ✓ **门判得对** ✓

## 为什么这么修（而不是逐个改那 4 个文件）

给 `tests/` 一个 `__init__.py` ⇒ 它从"命名空间包"变成**真包** ✓
⇒ 与 site-packages 那个同名包**平级竞争** ⇒ 由 `sys.path` 顺序决定 ✓
⇒ 而 pytest 的 `pythonpath = .`（`backend/pytest.ini` ✓）把**本仓的 `backend` 放在最前** ✓
⇒ 仓库的 `tests` **永远赢** ✓✓ —— 以后谁再往 site-packages 塞同名包都不会再踩 ✗

★ 顺带把 pytest 的模块名钉住了：从此测试模块名恒为 `tests.test_xxx` ✓
  （原来按"第一个没有 `__init__.py` 的目录"算 ✓ 那种算法正是**脆**的来源 ✓）
★ 已核对：全仓**没有任何**测试用裸名字互相 import（`from test_xxx import` ✗ 零命中 ✓）
  ⇒ 这个改动不会把谁的名字改坏 ✓
"""
