# 贡献指南（CONTRIBUTING）

> 这份文件写给**想改这个项目的人**（也包括三个月后的我自己 ✓）。
> 它不是客套话，是这个仓库真实在用的规矩 —— 每一条都能在 `scripts/` 里找到对应脚本 ✓。

---

## 一、先把环境跑起来

```powershell
# 1. 一键安装（需要预装 Python 3.11+ 与 Node 18+）
双击 install.bat          # 装依赖 + 生成 config.json + 构建前端，实测约 43 秒

# 2. 启动
双击 start.bat            # 起后端并打开浏览器

# 3. 打开设置页选模型 → 填 API Key → 开聊
```

想确认"我这台机器装干净了吗"：

```powershell
# 用 git archive 造一份"全新克隆"沙箱，跑一遍安装并前后各做一次 preflight
powershell -File scripts\clean_machine_drill.ps1
# 末尾必须是：DRILL PASSED: install=0 preflight=0 artifacts=all-present
```

---

## 二、改代码前必读：**红绿回滚组**

这个仓库用「**红绿**」保证"每条修复真有判别力" ✓ —— 不是靠自觉：

```powershell
python scripts\redgreen_check.py        # 108 组：每组都能复现"改坏⇒红、改对⇒绿"
```

- 修 bug **必须**在 `scripts/redgreen_check.py` 里加一组（**锚点**：源码里那句话 + 期望行为）
- 锚点要**锚在关键那一行**，不要用 `if False` 之类"写了但不渲染"的写法糊过去
  （历史上真发生过：锚点被包进 `{false && …}` 照样通过 ✗ —— 红绿组自己把它抓出来了 ✓）

---

## 三、提交前跑完整门（**五项，缺一不可**）

```powershell
powershell -File scripts\check_all.ps1
```

| 项 | 是什么 | 绿的判据 |
|---|---|---|
| pytest | 全部后端测试 | 全过（跳过项会说明原因）|
| pyflakes | 静态检查 | exit 0，**不许有未使用变量/未定义名** |
| tsc | 前端类型检查 | exit 0 |
| dist 新鲜 | 前端产物和源码是否同步 | 改过前端就必须 `npm run build` |
| redgreen | 回滚组 | 108/108 |

> git hook 只跑**快检**；**完整门要自己跑**（`scripts/check_all.ps1`）。提交信息里写清"真问题 / 怎么修 / 怎么验证" ✓。

---

## 四、几条硬规矩（踩过坑才写下来的）

1. **不要用脚本改源码** ✗ —— PowerShell 改写中文/引号会把文件弄坏 ✓（用编辑器改 ✓）。
   非要脚本改，用 UTF-8 **无 BOM** 写回；`.ps1` 反过来**必须带 BOM** ✓（`tests/test_ps1_encoding_guard.py` 会拦 ✓）。
2. **改完后端接口要重启后端** ✓ —— 不然前端会拿到 405/404（"新接口没加载"就是这么来的 ✓）。
3. **改前端要重建产物** ✓ —— 后端直接托管 `frontend/dist` ✓（门里有"产物新鲜"这一项 ✓）。
4. **发往群里的命令要过审批** ✓；涉及**应用自身目录**（`app`/`.venv`/`scripts`/`config.json`…）的
   删除/移动**一律停下来问人** ✓（连"本任务全部允许"也不放行 ✓）。
5. **不要提交密钥**：`config.json` 与 `.env` 已在 `.gitignore` ✓；
   提交前可跑 `python scripts\scan_git_history_secrets.py`（连**历史**一起扫 ✓）。

---

## 五、代码结构速查

```
contracts/     契约层（改接口先改这里）
backend/app/   引擎：main.py（HTTP+派发）/ loop.py（任务循环）/ team.py（团队与模式）
               approval.py（审批）/ store.py（存储）/ pricing.py（单价）/ capabilities.py（能力）
backend/tests/ 全部测试；红绿锚点也在这里
frontend/src/  React + Vite；components/ 下按功能分文件
frontend/scripts/e2e_team_regression.mjs   真群端到端回归（要真模型、要花钱 ✗）
scripts/       守门与体检脚本
docs/          用户指南、供应商配置、截图说明
```

---

## 六、关于"模式"

群里有多套协作模式（`backend/app/team.py::MODES`）：
`manual`（点名派）/ `broadcast`（全员广播）/ `leader`（组长拆解）/ `relay`（接力）/ `meeting`（开会）。

⚠️ **现状（2026-10-06）**：项目终验门 / 两级验收 / 缺陷回环 / 波次这四套机制
**只接在 `leader` 上** ✓ —— 其他模式**还没有** ✓（对应测试见
`tests/test_broadcast_mode.py` 与 `tests/test_meeting_mode.py` 里的"现状记录"那条 ✓）。
要动这块，先看那两条测试：**它们红的时候，就是行为变了的时候** ✓。

---

## 七、报 bug / 提 PR

- 报 bug 请带上：**复现步骤 + 期望 + 实际 + 门禁输出**（`check_all.ps1` 的结果 ✓）
- PR 请一个 commit 一件事，并在描述里写清"**怎么验证的**" ✓（不是"应该没问题" ✗）
- 大改动先开 issue 说清方向 ✓（省得白写 ✓）
