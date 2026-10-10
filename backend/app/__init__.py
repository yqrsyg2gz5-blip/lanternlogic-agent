"""LanternLogic Agent 后端 —— Phase 2 最小闭环。

分层（七大预留接口）：
  main.py     HTTP/SSE 边界（契约二）
  loop.py     Agent Loop（一次迭代只调一个工具）
  providers/  模型提供者（可换）
  executors/  执行器/沙箱（可换）
  store.py    存储（fs JSONL，可换 sqlite）
  bus.py      事件总线（SSE 推送）
  approval.py 命令审批护栏
"""
