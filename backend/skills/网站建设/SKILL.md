---
name: 网站建设
description: 制作静态网页/单页站/Landing Page 时使用——结构、样式与交付规范
---

# 网站建设

接到"做个网页/网站/Landing Page"的任务时，按以下规范产出：

## 结构
- 单页：工作区 `index.html`（内联 `<style>` 即可）。
- 多页：`index.html` + 子页面 + `style.css`，相对路径互链；图片放 `assets/`。

## 质量规范
- 语义化标签（header/main/section/footer），移动端可用（viewport + 弹性布局）。
- 视觉有品位：统一的配色（2-3 色）、字体层级、留白；不用默认蓝紫渐变。
- 无构建步骤、纯静态；外链资源只用稳定 CDN（字体/图标库）。
- 所有文案真实切题，不用占位 Lorem ipsum。

## 交付
1. 文件写入工作区后，交付消息里列出文件清单。
2. 告知预览方式：`/api/v1/tasks/{task_id}/files/raw?path=index.html`（界面的"预览"按钮可直接打开）。
