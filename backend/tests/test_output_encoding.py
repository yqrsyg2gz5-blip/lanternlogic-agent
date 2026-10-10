"""子进程输出解码 —— P2-20 回归。

真实事故（2026-09-30，task_20260930_0449）：
  用户让 Agent 删除桌面一个中文名文件夹，`rm -rf` 执行成功后，
  观察结果里的「已删除」变成了「宸插垹闄」——因为 Git Bash 的输出里
  同时含合法 UTF-8 与非法字节，而旧实现"UTF-8 严格解码失败就**整体**退回 GBK"，
  把本来正确的中文一起毁掉了。模型因此读不到自己命令的输出，只能靠 `ls` 报错反推结论。
"""
from __future__ import annotations

from app.executors.local import decode_output


def test_pure_utf8_is_decoded():
    assert decode_output("已删除\n".encode("utf-8")) == "已删除\n"


def test_pure_gbk_is_decoded():
    """cmd.exe（代码页 936）的输出仍要能正确解出中文。"""
    raw = "已删除".encode("gbk")
    assert decode_output(raw) == "已删除"


def test_ascii_passthrough():
    assert decode_output(b"total 0\ndrwxr-xr-x\n") == "total 0\ndrwxr-xr-x\n"


def test_empty():
    assert decode_output(b"") == ""


def test_mixed_output_keeps_utf8_chinese_readable():
    """★ 核心回归：UTF-8 中文 + 非法字节 → 中文必须仍然可读。

    构造方式与真实事故一致：bash 用 $'\\212' 之类转义路径里的原始字节，
    于是输出里既有合法 UTF-8（echo 的中文）又有非法字节。
    """
    raw = "已删除\n".encode("utf-8") + b"ls: cannot access '/c/Users/y/Desktop/\xff\xfe': No such file\n"
    text = decode_output(raw)
    assert "已删除" in text, f"中文被毁了：{text!r}"
    assert "宸插垹闄" not in text, "落回了旧行为（整体按 GBK 解）"


def test_invalid_bytes_do_not_raise():
    out = decode_output(b"\xff\xfe\x00\x81")
    # 修复审计 \u00a77.5 "恒真"弱断言：非法字节必须被替换处理，不能原样透传
    assert out is not None and out != ""
    assert "\x81" not in out, "非法字节不得原样出现在输出里"