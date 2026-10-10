# 桌面壳（Tauri）——打包说明

Tauri 2.x 是 MIT/Apache-2.0 双许可，**可免费商用**，打包出的 exe/msi 无授权问题。

## 为什么最后做壳
壳只是『浏览器窗口换皮』，产品价值在 Web 层（已完备）。打包需要 Rust 工具链（一次性安装），且打包资源内嵌后端意味着要处理 Python 运行时分发（嵌入式 Python 或 PyInstaller）——这两步在开箱体验稳定后执行。

## 打包步骤（需要一次性环境准备）
1. 安装 Rust：https://rustup.rs（或国内 https://rsproxy.cn）
2. 后端打包为单 exe：`pip install pyinstaller && pyinstaller -F backend/run.py`（需先写 run.py 入口）
3. `cd desktop && npm create tauri-app@latest .` 初始化（选 React 模板对接 ../frontend）
4. 替换 src-tauri/tauri.conf.json 为本目录的配置
5. `npm run tauri build` → 产出 NSIS 安装包（内嵌后端 exe + 前端 dist + skills/contracts/docs）

## 当前骨架
- **✅ 2026-10-02 更新：Tauri 工程已完整并已构建出 exe**（`target/release/agent-shell.exe`，
  5.7 MB；`Cargo.toml` / `src/` / `build.rs` / `icons/` 均在库）。`target/` 与 `gen/` 已入
  `.gitignore`，重新构建用 `npm run tauri build`。
- tauri.conf.json 已配好：窗口尺寸、NSIS 中文安装器、资源清单
- 用户装完即桌面应用：双击图标 → 自动起后端 → 窗口内就是 LanternLogic Agent 界面

### 资源清单已修正（2026-09-30）
原清单写的是 `../skills` 与 `../config.example.json`，**这两个路径不存在**：
- 技能在 `backend/skills/`（不是根目录的 `skills/`）
- 配置模板在 `contracts/config.example.json`

**更要紧的是**：原清单写 `../backend`（整目录）会把
**`backend/data/`（用户的任务历史与工作区）和 `backend/.venv/`（几百 MB）一起打进安装包** ——
既有体积问题，也有**把开发者自己的数据分发给用户**的隐私问题。
现已改为精确清单（`backend/app`、`backend/skills`、`backend/requirements.txt`，**不含 data/.venv**）。

## 替代方案（不打包壳）
install.bat + start.bat 已经能做到『双击安装 → 双击启动 → 自动开浏览器』——壳只是省掉浏览器标签页。可以先分发这个形态验证需求，再决定要不要壳。