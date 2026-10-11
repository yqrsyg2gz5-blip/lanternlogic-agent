---
name: 数据分析可视化
description: 把 CSV/JSON 等数据做成图表报告时使用——分析流程与图表规范
---

# 数据分析可视化

接到"分析这份数据/做成图表"的任务时：

## 流程
1. 先 file_read 看清数据结构与规模（列含义、行数、脏数据）。
2. update_plan：清洗 → 统计 → 可视化 → 结论。
3. 产出 `report.html`：内嵌 ECharts（CDN：https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js）。

## 图表规范
- 一图一观点，每张图配一句"这张图说明什么"。
- 图表类型选对：趋势用折线、对比用柱状、占比用饼图（≤5 类）、分布用散点/直方。
- 报告结构：数据概览 → 关键发现（每条配图）→ 结论与建议。

## 交付
- 报告与数据结论写进 `report.html`（数据内嵌 JSON），交付时列文件清单。
