# 第三方依赖与许可证声明（THIRD-PARTY NOTICES）

> 本文件列出 LanternLogic Agent 使用的第三方组件及其许可证，供分发时合规使用。
> 生成方式：`_临时工具/_dep_inventory.py`（从**本项目自己的源码**反推 import，再读包元数据）；
> 生成日期：**2026-10-06**。版本号是当时的实际安装版本，重新生成即可刷新。

---

## 一、后端（Python）

| 组件 | 版本 | 许可证 | 用途 |
|---|---|---|---|
| **edge-tts** | 7.2.8 | ⚠️ **LGPL-3.0** | 语音合成（可选：微软 Edge 在线 TTS）|
| nltk | 3.10.3 | Apache-2.0 | 文本切分（可选）|
| httpx | 0.28.1 | BSD-3-Clause | HTTP 客户端（模型/工具调用）|
| cryptography | 50.0.2 | Apache-2.0 / BSD-3-Clause | 本地加密（访问令牌等）|
| fastapi | 0.142.2 | MIT | HTTP 服务框架 |
| uvicorn | 0.54.0 | BSD-3-Clause | ASGI 服务器 |
| pydantic | 2.13.5 | MIT | 数据校验 |
| pytest | 9.1.1 | MIT | 测试（仅开发）|
| playwright | 1.63.0 | Apache-2.0 | 浏览器自动化（可选能力）|
| pyttsx3 | 2.99 | MIT | 本地离线语音合成（可选）|
| PyYAML | 6.0.3 | MIT | 配置解析 |

（`zope`/`yarl`/`zipp` 等为上述组件的传递依赖，均为 MIT/BSD/Apache 系。）

### ⚠️ 关于 edge-tts（LGPL-3.0）——分发前务必知悉

LGPL **不会传染**本项目源码 ✓，但**分发二进制时**（例如打成 exe 或随安装包发布）需满足：

1. **声明**使用了该库并**附上 LGPL-3.0 全文**（或可访问的链接）✓；
2. 允许接收者**用修改过的版本替换该库** ✓（动态链接即可；静态链接需提供目标文件）；
3. 若**修改**了该库本身，修改部分仍需以 LGPL 发布 ✓。

**仅提供源码 / SaaS 场景**：一般不产生额外义务 ✓。

**建议**（待定，见「开源准备-决策书」）：把 edge-tts 作为**可选依赖**（用户自行 `pip install`），
**不预置进安装包** ⇒ 义务最简单 ✓。

---

## 二、前端（Node）

| 组件 | 版本 | 许可证 |
|---|---|---|
| react / react-dom | ^18.3.1 | MIT |
| react-markdown | ^10.1.0 | MIT |
| remark-gfm | ^4.0.1 | MIT |
| lucide-react | ^1.49.0 | ISC |
| qrcode | ^1.5.4 | MIT |
| vite | ^5.4.11 | MIT |
| typescript | ^5.6.3 | Apache-2.0 |
| @vitejs/plugin-react | ^4.3.4 | MIT |
| playwright-core | ^1.63.0 | Apache-2.0 |
| jsqr | ^1.4.0 | Apache-2.0 |
| @types/react、@types/react-dom、@types/qrcode | — | MIT |

**前端依赖全部为 MIT / ISC / Apache-2.0** ✓ —— **没有 GPL/AGPL** ✓。

---

## 三、本仓库自有的小实现（无需第三方许可）

- `frontend/src/lib/tinyHighlight.js` —— **本项目自写的**极小语法高亮（约 60 行）。
  之所以自己写：用户对体积敏感，highlight.js 约 100KB+ ✗；行为测试见
  `backend/tests/test_syntax_highlight.py`（用 node 真跑，断言"拼回去必须和原文一致" ✓）。

---

## 四、复核方式

```powershell
# 重新生成清单（会读实际安装版本与包元数据）
#   ★ 2026-10-07：原命令里写的是**作者本机的绝对路径** ✗ 换台机器就跑不了 ✓
#     清单脚本在仓库外的临时工具目录 ✓ 用相对写法（或按需改成你自己的路径）✓
& .\backend\.venv\Scripts\python.exe ..\_临时工具\_dep_inventory.py

# 密钥体检（分发前必跑；本脚本已修好中文 Windows 控制台编码崩溃）
& .\backend\.venv\Scripts\python.exe .\scripts\scan_git_history_secrets.py
```

> 本文件是**事实清单**，不构成法律意见；正式发布前建议由专业人士复核一次 ✓。
