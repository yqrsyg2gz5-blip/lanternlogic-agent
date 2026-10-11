"""命令审批护栏 —— 契约二 waiting_approval 状态机。

审批动作：Allow Once / Always Allow / Deny。

2026-10-02 审计 §4 **fail-closed 重写**。旧版是"看懂才拦、看不懂放行"——实测 13 种
平凡构造可免审批越界（bash -c 不在整条命令开头 / r''m 引号拼接 / xargs bash / $()
命令替换 / 2> 越界写文件 / curl|bash / python -c …），且绕过后提示写着
「只读，免审批」——虚假安全感。新版原则：**看不懂就必审批**。

判定顺序与规则：
  ① 结构嫌疑一律 ask：
     · 命令替换（$() 、反引号、进程替换 <( ）与 heredoc —— 内容无法静态判定，
       原文级检测（引号内也算）；
     · 输出重定向到真实文件（> / >> / 2> / &> ；/dev/null、nul、&1、&2 等空目标除外）；
     · shell 包装器出现在**任何子命令**开头（bash/sh/zsh/dash/cmd/powershell/pwsh，
       含 /usr/bin/bash 前缀形态）：能透视内层（-c/-Command//c 后有内容）就递归分析
       内层，透视不了（裸 bash、bash 从 stdin 读脚本）→ ask；
     · 前缀包装分两类（验证报告 24 第8条：与实现对齐）——
       **不透明包装**（sudo/doas/env/exec/wsl/runas/start：提权/换环境）→ 一律 ask；
       **透明包装**（xargs/timeout/nohup/nice/stdbuf/command/watch/setsid/time…：
       只影响调度，语义不变）→ 剥掉本体与自身参数后**递归判定内层**
       （`timeout 60 python -m pytest` 不误伤，`xargs rm` 仍命中）；
     · 变量/家目录路径（$VAR/~）与相对上跳（../）——配合写动词或 cd 时判定；
     · 远程代码执行类工具（mshta/certutil/wmic/schtasks/rundll32…）→ 一律 ask。
  ② 危险动词（保留 P1-5）：按**每个子命令**的首词判定（引号感知切分 ; && || |），
     首词判定前做引号归一化——`r''m` → `rm`，引号拼接不再隐身；`find -exec rm` 仍命中。
  ③ 联网 / 内联代码一律 ask（fail-closed 新增）：curl/wget/ssh/scp/pip/npm…
     （全部 URL 都是回环地址除外——数据不出机器不叫外传）、解释器内联代码
     （python -c / node -e / `python -` 读 stdin…）、git push/pull/clone、
     docker run/exec/pull/build。
  ④ 越界路径必审批（保留 P1-5）：出现工作区 / allowed_dirs 之外的绝对路径时，
     只有**白名单纯只读**命令才免审批（allow_readonly，记审计）。白名单 = 明确的
     查询命令（ls/cat/find/grep/awk…），黑名单判定不了的一律 ask——这正是旧版
     「审计：只读访问工作区之外（免审批）」假提示的病根，现在提示重新变回真话。
  ⑤ 按任务隔离（保留 P1-6）：pending 以 (task_id, call_id) 为键；「总是允许」按任务
     记忆，结构键 / 动作键 / 联网键分开记——记住 rm 不代表记住 bash -c。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Callable, Iterable, NamedTuple

# ---------- 包装器识别（拆壳用） ----------
#
# ⚠️ 中间的 `(?:[^\s]*\s+)*?` 不能省：真实任务里用的是
# `powershell -NoProfile -Command "…"` —— 少了这一段，正则只认"开关紧跟解释器"的写法，
# 于是**静默不拆壳**，首词变成 powershell，只读白名单随之失效。
_WRAPPER_RE = re.compile(
    r"^\s*(?:[A-Za-z]:[\\/][^\s]*[\\/])?"
    r"(?:sh|bash|zsh|dash|cmd|powershell|pwsh)(?:\.exe)?\b"
    r"(?:[^\s]*\s+)*?"
    r"(?:-lc|-c|-Command|--command|//c|//C|/c|/C)\s+(.*)$",
    re.I | re.S,
)

# 命令里出现的绝对路径。只认这三种形态，**故意不匹配泛 Unix 路径**，
# 以免把 URL（http://host/api/v1）误判成越界路径而天天弹审批。
# 盘符形态必须加 `(?<![A-Za-z0-9])` 前置否定：否则 `http:/…` 里的 `p:/` 会被当成盘符。
_ABS_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9])[A-Za-z]:[\\/][^\s\"'`;|&)]+"
    r"|(?<![A-Za-z0-9])~[\\/][^\s\"'`;|&)]+"
    r"|(?<![A-Za-z0-9])/(?:c|d|e|f|g)/[^\s\"'`;|&)]+"
)

_GITBASH_DRIVE_RE = re.compile(r"^/([a-zA-Z])/(.*)$")
_QUOTES = "\"'`"
# 引号内的内容要**先剥掉再拆子命令**：否则 `awk '{n++; s+=$1}'` 里的 `;` 会被误当成
# 命令分隔符，拆出 `s+=$1}` 这种片段，导致正常只读管道被误判成"脚本"。
_QUOTED_RE = re.compile(r"'[^']*'|\"[^\"]*\"")

# ---------- ① 结构嫌疑 ----------
#
# shell 包装器：出现在**任何子命令开头**都算（旧版只认整条命令开头 → `echo hi && bash -c …` 直通）。
_WRAPPER_HEADS: frozenset[str] = frozenset({"sh", "bash", "zsh", "dash", "cmd", "powershell", "pwsh"})
# 前缀包装：喂入内容不可知，一律 ask。
# 二十四轮 🔴P0（四表合并同源 + fail-closed）：
# 此前有四张包装器表（_OPAQUE 8 / _TRANSPARENT 10 / _VERB_SKIP 15 / 容器面 _WRAPPER_PEER 8）
# 互不同源——su/chroot/nsenter/systemd-run/setpriv/script/flock/strace 十类提权/换环境
# 包装器【哪张表都不在】→ docker system prune -a 零审批整族放行。
# 单一事实来源 = _WRAPPERS_UNIFIED；每类带【参数消费规则】（跳过自身参数再取内层 head）。
# 分类：
#   opaque   —— 一律 ask（提权/换环境，喂入不可知）
#   argjump  —— 透明：跳掉自身参数后递归判定内层
#   plain    —— 透明：只剥本体
_WRAPPER_DEF: dict[str, tuple[str, int]] = {
    # ---- opaque ----
    "sudo": ("opaque", 0), "doas": ("opaque", 0), "env": ("opaque", 0),
    "exec": ("opaque", 0), "wsl": ("opaque", 0), "runas": ("opaque", 0),
    "start": ("opaque", 0),
    "su": ("opaque", 0), "chroot": ("opaque", 0), "unshare": ("opaque", 0),
    "nsenter": ("opaque", 0), "systemd-run": ("opaque", 0), "setpriv": ("opaque", 0),
    "script": ("opaque", 0), "strace": ("opaque", 0), "ltrace": ("opaque", 0),
    "setarch": ("opaque", 0), "capsh": ("opaque", 0), "firejail": ("opaque", 0),
    "bwrap": ("opaque", 0), "proot": ("opaque", 0),
    # ---- argjump（带独立参数：跳 N 个自身参数/选项）----
    "timeout": ("argjump", 1), "nice": ("argjump", 0), "ionice": ("argjump", 0),
    "stdbuf": ("argjump", 0), "setsid": ("argjump", 0), "taskset": ("argjump", 0),
    "flock": ("argjump", 1), "time": ("plain", 0), "watch": ("plain", 0),
    "command": ("argjump", 0), "xargs": ("plain", 0), "nohup": ("plain", 0),
}

def _merge_wrapper_tables() -> None:
    """四张旧表并入 _WRAPPER_DEF 后，为兼容旧引用重算导出集合。"""
    globals()["_OPAQUE_WRAPPERS"] = frozenset(
        k for k, (kind, _) in _WRAPPER_DEF.items() if kind == "opaque")
    globals()["_TRANSPARENT_WRAPPERS"] = frozenset(
        k for k, (kind, _) in _WRAPPER_DEF.items() if kind in ("argjump", "plain"))
    globals()["_PREFIX_WRAPPERS"] = _OPAQUE_WRAPPERS | _TRANSPARENT_WRAPPERS
    globals()["_VERB_SKIP_WRAPPERS"] = frozenset(_WRAPPER_DEF.keys())


# 静态声明（pyflakes 可见；运行时由 _merge_wrapper_tables() 立即覆盖为统一表导出值）
_OPAQUE_WRAPPERS: frozenset[str] = frozenset()
_TRANSPARENT_WRAPPERS: frozenset[str] = frozenset()
_PREFIX_WRAPPERS: frozenset[str] = frozenset()
_VERB_SKIP_WRAPPERS: frozenset[str] = frozenset()

_merge_wrapper_tables()  # 二十四轮：模块加载即从单一事实来源导出旧名

# （旧四张表已删除——二十三轮 🔴P0：四表互不同源导致
#   su/chroot/nsenter/systemd-run/setpriv/script/flock/strace 十类零审批；
#   现在只有 _WRAPPER_DEF 一张表说了算。兼容旧引用见上 _merge_wrapper_tables。）
# 重定向到这些目标 = 丢弃/复用 fd，不是写文件
_NULL_TARGETS: frozenset[str] = frozenset({"/dev/null", "nul", "/dev/stdout", "/dev/stderr"})
_FD_DUP_RE = re.compile(r"&?\d")
# 捕获重定向目标：`> x` `>> x` `2> x` `&> x` `2>&1`（引号先剥掉——引号里的 `>` 不是重定向）
_REDIRECT_TOKEN_RE = re.compile(r"(?:(?:\d+|&)\s*)?>{1,2}\|?\s*(&\d|[^\s;&|<>]+)")

# ★ 2026-10-11（平台闸修复 ③）：**「总是允许」的命令级记忆上限**（每个任务）
#   为什么要有：长任务会把每条命令的指纹一直堆下去 ✗（本项目一贯的规矩：
#   "内存里的东西都得有主" ✓）。2000 条 ≈ 一个超长任务的量级 ✓ 再多就该丢了 ✓。
_MAX_CMD_MEMORY = 2000
# ★ 同上：**允许被"按类记忆"的键前缀**（其余一律只记"那一条命令"✗）
#   依据：一次"总是允许"只该覆盖**用户当时看到的那个东西** ✓
#     · `verb:rm`    = "rm 这类" ✓（他看的就是那个动词 ✓）
#     · `outside:D:` = "这个目录" ✓（他看的就是那个路径 ✓）
#   而 `net:`（联网/内联代码）与 `struct:`（结构嫌疑）**不在表里** ✗ ——
#   放行它们的**一整类** = 从此任意联网/任意代码免审批（审计 P1 ✓）。
_REMEMBERABLE_KEY_PREFIXES: tuple[str, ...] = (
    "verb:", "outside:", "confirm:", "forever:", "protect:", "allowall:",
)

# ---------- ② 危险动作 ----------
#
# 要跳过的"前缀包装"——二十四轮：四表合一后由 _merge_wrapper_tables 从
# _WRAPPER_DEF 导出（单一事实来源，不再手写第二份）。

# shell 控制流 / 提权关键字：出现就意味着这是"脚本"而不是"查询"（白名单判定用）
_SHELL_KEYWORDS: frozenset[str] = frozenset({
    "while", "for", "do", "done", "if", "then", "else", "elif", "fi", "case", "esac",
    "until", "eval", "source", "function", "trap", "exec", "sudo", "runas", "call",
    "setlocal", "endlocal", "start", "command",
})

# ---------- ③ 联网 / 内联代码 ----------
#
# 数据外传与任意代码执行不靠事后审计——出现即问（全部 URL 都是回环地址除外）。
_NET_HEADS: frozenset[str] = frozenset({
    # 网络工具（复审 P0：socat/openssl 均可外连/反连）
    "curl", "wget", "scp", "sftp", "ssh", "nc", "netcat", "ncat", "telnet", "ftp",
    "lftp", "socat", "openssl", "tftp",
    "invoke-webrequest", "invoke-restmethod", "iwr", "irm",
    # 包管理器 = 下载并执行第三方代码（供应链）——复审 P0：仅"安装/执行"类子命令问，
    # 纯查询（pip list / npm --version）放行，见 _PKG_ASK_SUBS
    "pip", "pip3", "npm", "npx", "yarn", "pnpm", "conda", "uv", "poetry", "gem", "cargo",
    # git/容器 CLI：仅特定子命令算联网/任意代码（见 _GIT_NET_SUBS / _DOCKER_NET_SUBS）
    # 第九轮复验⑥：podman/nerdctl/ctr 与 docker 同能力（拉镜像=下载并执行第三方代码）
    "git", "docker", "podman", "nerdctl", "ctr",
})
# 包管理器里"会下载/会执行"的子命令（复审 P0 误伤修复：list/show/--version 不在列）
_PKG_ASK_SUBS: frozenset[str] = frozenset({
    "install", "add", "download", "run", "exec", "x", "publish", "upload",
    "build", "test", "start", "ci", "create", "init", "shell",
})
_GIT_NET_SUBS: frozenset[str] = frozenset({"push", "pull", "fetch", "clone"})  # remote -v 是只读，不拦
# 复审 P0 误伤修复：包管理器走子命令守卫的头部集合
_PKG_ASK_SUBS_GUARD_HEADS: frozenset[str] = frozenset({
    "pip", "pip3", "npm", "yarn", "pnpm", "conda", "uv", "poetry", "gem", "cargo",
})
# 复审 P0：远程代码执行类工具——正常工作流不该出现，出现即问
_REMOTE_EXEC_HEADS: frozenset[str] = frozenset({
    "mshta", "rundll32", "regsvr32", "certutil", "bitsadmin", "wscript", "cscript",
    "msiexec", "wmic", "schtasks",
})
# 二十六轮第3处：git/pip 的【间接执行】参数面（struct 键，随 ①c 面扫描）。
# git 的 -c 可设 core.pager / core.sshCommand / difftool.*.cmd /
# filter.<d>.clean / filter.<d>.smudge——全是"运行外部命令"的配置键；
# --exec-path 改执行路径；--upload-pack/--receive-pack/--send-pack 的值
# 直接是远端 helper 命令。此前 git 在无关命令白名单里整族放行，
# `git -c core.pager='docker system prune -a' log` 三模式 PASS（独立复现）。
# pip 的 -c/--constraint 按验证员拍板一并 ASK（宁严）。
# 残余风险（如实声明）：仓库级 .gitconfig 恶意配置不在命令行参数里，此面拦不到。
#
# 二十六轮第 7 批第 1 处：`--config-env` 补口 + 全部 29 个 git 全局选项逐个判定。
# 真机复现（git 2.53.0.windows.3，本班）：`--config-env=KEY=ENVVAR` 与
# `--config-env KEY=ENVVAR` 两种形态【都能注入配置】，且与 `-c` 是同一能力面：
#   EVIL='!echo PWNED' → git --config-env=alias.pwn=EVIL pwn  → 真的执行了 shell
#   而 git -c alias.pwn=… pwn → ASK。同一能力面一个拦一个不拦（二十六轮第 6 批）。
# 同族扫描结论（git-doc/git.html 的 OPTIONS 一节，29 条）：
#   · 注配置     ：-c、--config-env                       → 已拦 / 本处补拦
#   · 指定外部程序：--exec-path、--upload-pack、--receive-pack、--send-pack（后三者
#                 是子命令级同名参数，一并保留）          → 已拦
#   · 改属性来源 ：--attr-source=<tree-ish>——本班实测（临时仓库 + GIT_TRACE）：
#                 `--attr-source=HEAD` 让【树里的 .gitattributes 生效】而工作区没有
#                 （git check-attr filter → evilfilter；对照组 = unspecified）。
#                 属性规则正是 filter.<n>.clean/smudge/process 与 diff.<n>.command
#                 （外部程序）的开关 ⇒ 归入"能指定外部程序"族，一并拦。
#   · 只改落点 ：-C/--git-dir/--work-tree/--namespace —— 不注入配置，只是让 git
#                 去别处找【已存在】的 .git/config（要成立得先有写盘原语）→ 不拦，
#                 残余风险如实声明（工作流成本>收益：CI/submodule 脚本大量使用）。
#   · 无能力   ：-v/--version/-h/--help/--html-path/--man-path/--info-path/
#                 -p/--paginate/-P/--no-pager/--bare/--no-replace-objects/
#                 --no-lazy-fetch/--no-optional-locks/--no-advice/
#                 --literal-pathspecs/--glob-pathspecs/--noglob-pathspecs/
#                 --icase-pathspecs/--list-cmds → 不拦（真机逐条实测无外部程序）。
#   · 同族旁路（非命令行参数面）：GIT_CONFIG_COUNT/GIT_CONFIG_KEY_0/
#                 GIT_CONFIG_VALUE_0 环境变量注入（本班实测真的执行 shell：
#                 alias.pwnenv='!echo PWNED' → 执行）。命令串里不出现，此面拦不到
#                 ——如实声明，属"随行环境"面而非参数面。
_GIT_EXEC_FLAGS: frozenset[str] = frozenset({
    "-c", "--config-env", "--exec-path", "--upload-pack", "--receive-pack", "--send-pack",
    "--attr-source",
})
_PIP_EXEC_FLAGS: frozenset[str] = frozenset({"-c", "--constraint"})
# 二十六轮第2批（验证员补充）：git config 写恶意配置是【两步攻击的第一步】——
# 先写 core.pager，之后任何 git log 都执行它。key 判定（小写比较）：
# 精确集 + alias.*（!命令）+ filter.*.clean/.smudge + pager.* + diff 外部工具。
_GIT_CONFIG_EXEC_KEYS: frozenset[str] = frozenset({
    "core.pager", "core.sshcommand", "core.fsmonitor", "core.editor", "gpg.program",
    "diff.external", "interactive.difffilter",
    # 二十六轮第 4 批第 5 处（验证员清单 10+2 键补口）：
    "credential.helper",           # git 最经典任意命令执行向量
    "core.askpass", "core.gitproxy", "core.alternaterefscommand",
    "core.hookspath",              # hooksPath 指向攻击者目录 = 钩子全换
    "sequence.editor", "merge.tool", "include.path",  # include.* = 配置注入
})
# tar/rsync 的执行参数（二十六轮第2批补充，独立实测三模式 PASS 的旁路）：
#   tar --to-command='…' / --use-compress-program / -I <cmd>；rsync --rsh / -e <cmd>
_TAR_EXEC_FLAGS: tuple[str, ...] = ("--to-command", "--use-compress-program", "-I")
_RSYNC_EXEC_FLAGS: tuple[str, ...] = ("--rsh", "-e")
# 复审 P0：破坏性系统操作——无路径上下文也必问（shutdown 类无路径可分析）
_ALWAYS_ASK_HEADS: frozenset[str] = frozenset({
    "shutdown", "reboot", "poweroff", "halt", "mkfs", "shred", "unlink",
    "taskkill", "icacls", "takeown", "net", "xz", "bzip2",
    # 第四轮复验：PowerShell 任意代码执行通道（iex 可执行下载/内联脚本）
    "iex", "invoke-expression", "invoke-command", "add-type",
    "set-executionpolicy", "start-process", "register-scheduledtask", "new-service", "sc",
})
# 第四轮复验：编辑器可执行 shell（vim -c ':!rm' / -S 脚本 / +命令）——正文参数即代码
_EDITOR_EXEC_HEADS: frozenset[str] = frozenset({"vim", "nvim", "vi", "ex", "emacs", "view"})
# 调试器：-ex/-x 参数可在内部执行 shell（gdb -batch -ex 'shell rm …'）
_DEBUGGER_HEADS: frozenset[str] = frozenset({"gdb", "lldb"})

# ── 第五轮复验（能力面重构，替代"穷举动词"）──
# 「破坏性写」能力面：会覆写/清空已有文件的操作。只要目标**已存在**就必问——
# 这是"清空用户交付物"这一能力面的最小完备覆盖，不依赖动词表穷举。
_DESTRUCTIVE_OVERWRITE_HEADS: frozenset[str] = frozenset({
    "truncate", "dd", "tee", "install",
    "cp", "copy", "xcopy", "robocopy", "mv", "move", "rename",
    # 第六轮复验②：ln/mklink 建链接会占用/覆写目标路径（ln -sf 强制覆盖）
    "ln", "mklink",
})
# 就地改写类：`perl -i`/`sed -i`/`ruby -i` —— 直接改目标文件内容
_INPLACE_FLAG_HEADS: frozenset[str] = frozenset({"perl", "sed", "ruby", "python", "awk"})
# 其中"稀有但对已有文件致命"的子集：目标状态无法核实时 fail-closed ask
# （cp/mv 是文件整理的日常操作，目标不存在=创建/改名，免问；truncate/dd/tee/install
#   在正常 Agent 工作流里几乎只用于覆写，问错代价远低于漏放代价）
_STRICT_OVERWRITE_HEADS: frozenset[str] = frozenset({"truncate", "dd", "tee", "install"})
# 第四轮复验：解释器/构建工具跑**脚本文件**（内容不在命令行里，须读文件后静态扫描）
_SCRIPT_RUNNER_HEADS: frozenset[str] = frozenset({
    "python", "python3", "py", "node", "deno", "bun", "perl", "ruby", "php", "lua",
    "make", "gmake", "npm", "pnpm", "yarn",
})
_SCRIPT_EXT_RE = re.compile(r"\.(?:bat|cmd|ps1|vbs|js|mjs|cjs|sh|py|pl|rb|php|mk)$", re.I)
# 脚本内容里的危险调用模式（**调用形态**，不是裸词——第五轮复验：注释/字符串里的
# "rm -rf dist" 是文档不是行为）。Python 另有 AST 级检测（_scan_python_ast）。
_SCRIPT_DANGER_RE = re.compile(
    r"(?:^|[\s;&|'\"()])(?:rm|shred|mkfs|dd|format|del|erase)\s+-?\w"
    r"|rmtree|os\.remove|os\.unlink|os\.system|subprocess|popen|system\("
    r"|invoke-expression|downloadstring|downloadfile|webclient"
    r"|register-scheduledtask|new-service|schtasks|startup[\\/]|start\s+menu"
    r"|curl\s+https?://|wget\s+https?://|invoke-webrequest"
    r"|:\s*\(\s*\)\s*\{",
    re.I,
)
# JS/TS 删除/写 API 的**调用形态**（.rmSync( / .unlink( / rimraf( …）
_JS_DELETE_CALL_RE = re.compile(
    r"\.\s*(?:rmSync|rm|unlinkSync|unlink|rmdirSync|rmdir|writeFileSync|writeFile|truncateSync|truncate|copyFileSync|renameSync)\s*\("
    r"|\b(?:rimraf|del|unlinkSync|rmSync)\s*\(",
    re.I,
)
# Python 危险调用（AST 用；模块名.函数名的短集合）
_PY_DANGER_CALLS: frozenset[str] = frozenset({
    "system", "popen", "run", "call", "check_output", "check_call",  # os/subprocess
    "rmtree", "remove", "unlink", "rmdir", "rename", "replace", "move",  # shutil/os
    "remove_file", "copyfile", "write_text", "write_bytes", "open",  # os/pathlib 覆写
})
_PY_DANGER_MODULES: frozenset[str] = frozenset({"os", "subprocess", "shutil", "pathlib", "sys"})
# 复审 P0：写动词兜底集——这些动词配合**不可静态判定的路径**（$VAR / ..）时必问。
# 纯工作区内的普通 mv/cp 仍按 auto_edit 语义放行（只对路径不透明的情况收紧）。
_WRITE_FALLBACK_HEADS: frozenset[str] = frozenset({
    "mv", "move", "cp", "copy", "xcopy", "robocopy", "dd", "truncate", "tar", "tee",
    "ln", "mklink", "install", "rsync", "unzip", "zip", "7z", "gzip", "gunzip",
    "split", "csplit", "patch", "attrib",
    # 第四轮复验：PowerShell 写 cmdlet（$env: 路径此前完全不在判定面）
    "set-content", "add-content", "clear-content", "out-file", "new-item",
    "set-item", "set-itemproperty", "new-itemproperty", "remove-itemproperty",
    "move-item", "copy-item", "rename-item", "set-acl", "new-psdrive",
    "export-clixml", "export-csv", "tee-object",
})
# 变量路径：$HOME / $USERPROFILE / ${VAR} / %VAR%（cmd 形态）——解析不了目标，写操作无法静态判定
_VAR_TOKEN_RE = re.compile(r"\$[A-Za-z_{]|%[A-Za-z_]\w*%")
# 相对路径上跳：../../ 或 ..\  （配合写动词/ cd 判定；引号内字面量不算，见误伤修复）
_DOTDOT_RE = re.compile(r"\.\.[/\\]")
_DOCKER_NET_SUBS: frozenset[str] = frozenset({"run", "exec", "pull", "build", "create"})
# 第五轮复验：`docker compose run/exec` 与 `docker run/exec` 同判（换入口同能力）
_DOCKER_COMPOSE_SUBS: frozenset[str] = frozenset({"run", "exec"})
# 二十轮 🔴0：Docker 对象名（`docker <object> <verb>` 形态的第一段）——
# 出现在 toks[1] 时真动词在 toks[2]（此前写死 toks[1] → 整族绕过）
_DOCKER_OBJECTS: frozenset[str] = frozenset({
    "image", "images", "container", "containers", "system", "volume",
    "volumes", "network", "networks", "context", "builder", "buildx",
    "manifest", "trust", "plugin", "stack", "swarm", "node", "service",
    "config", "secret", "checkpoint", "compose",
})
# 破坏集：删资源类子命令（与生命周期集并列入判定；观察类不在其中）
_DOCKER_DESTRUCTIVE_SUBS: frozenset[str] = frozenset({
    "prune", "rm", "rmi", "kill", "stop", "restart", "pause", "unpause",
    "update", "rename", "tag", "push", "import", "export", "commit", "save", "load",
    "deploy", "scale", "leave", "init", "disconnect", "revoke", "cp",
})
# 二十轮 🔴0（结构性方案 b·动词白名单反转）：不枚举危险——只放行【已知安全动词】，
# 其余一律 ASK（新子命令/新对象名/未知形态默认被拦，不再靠枚举追）
_DOCKER_SAFE_VERBS: frozenset[str] = frozenset({
    "ps", "images", "ls", "inspect", "version", "logs", "info", "top",
    "port", "diff", "stats", "history", "events", "wait", "df", "search",
    "config",  # compose config / docker config ls 前者是只读渲染
    "du", "services", "ca", "show",  # 二十四轮：buildx du / stack services / swarm ca / context show
})
# 全局选项表（docker [OPTIONS] COMMAND —— 剥掉它们及其值，再取动词）
_DOCKER_GLOBAL_OPTS: frozenset[str] = frozenset({
    "--context", "-c", "--host", "-H", "--config", "--log-level", "-l",
    "--tls", "--tlscacert", "--tlscert", "--tlskey", "--registry-mirror",
})
# 无值全局选项（单跳，不消费下一个 token）。真机实测：`docker --debug ps`
# 正常执行——--debug/-D/--tlsverify 无值；此前混进带值表双跳会吞掉子命令
# （`docker --debug -h` 误判"只有选项无动词"）。
_DOCKER_GLOBAL_FLAGS: frozenset[str] = frozenset({
    "--debug", "-D", "--tlsverify",
})
# 连字符旧入口（独立 CLI，与 v2 插件同能力）
_COMPOSE_LEGACY_CLIS: frozenset[str] = frozenset({
    "docker-compose", "podman-compose", "nerdctl-compose",
})
# 第九轮复验⑥：compose 的联网/执行面（run/exec 换入口；up 隐含 build+create+start）
_DOCKER_COMPOSE_NET_SUBS: frozenset[str] = frozenset({"run", "exec", "up", "create", "build", "pull"})
# 第九轮复验⑥：容器 CLI 按**形态**识别（同名形态的 podman/nerdctl/ctr 同能力）；
# compose 全局选项（--profile/-f/--env-file）此前使 run/exec 定位失明。
_CONTAINER_CLIS: frozenset[str] = frozenset({"docker", "podman", "nerdctl", "ctr"}) | _COMPOSE_LEGACY_CLIS
# 二十五轮 🔴2："完全无关普通命令"白名单——这些 head 既不会把后续 token 当
# 命令执行、也与容器状态无关；其后即使出现容器 CLI 字样（如 `grep docker
# build.log`）也只是文本操作，交给其它判定面。
# 刻意【不收】执行器家族（xargs / find -exec / watch / nohup / su / sudo /
# env / sh / bash…）——已知执行器在 _WRAPPER_DEF，未知执行器落
# "未知包装器 ASK"（_container_lifecycle 入口 fail-closed）。
_BENIGN_NONCONTAINER_HEADS: frozenset[str] = frozenset({
    "ls", "dir", "cat", "echo", "printf", "grep", "egrep", "fgrep", "rg",
    "head", "tail", "wc", "sort", "uniq", "diff", "comm", "cut", "tr",
    "date", "whoami", "who", "id", "pwd", "uname", "hostname",
    "file", "stat", "du", "df", "which", "whereis", "type", "man",
    "git", "python", "python3", "py", "pip", "pip3", "node", "npm",
    "curl", "wget",  # 联网面自行判定（回环豁免/外传拦截都在 net 面）
})
# 容器 CLI 的布尔标志（单跳）；其余默认"标志 + 值"双跳（白名单外也安全：只会多跳）
_CONTAINER_BOOL_FLAGS: frozenset[str] = frozenset({
    "--rm", "--privileged", "--init", "--detach", "-d", "--interactive",
    "-i", "--tty", "-t", "-T", "--read-only", "--no-deps", "--service-ports",
    "-q", "--quiet", "--help", "-h", "--version", "-V",
})
_INTERPRETERS: frozenset[str] = frozenset({
    "python", "python3", "py", "node", "deno", "bun", "perl", "ruby", "php", "lua",
})
_INLINE_FLAGS: frozenset[str] = frozenset({"-c", "-e", "-r", "--eval", "--command", "-"})
_LOCALHOST_RE = re.compile(r"https?://([^/\s:]+)", re.I)

# ---------- ④ 只读白名单（A 方案） ----------
#
# 起因：真实任务（扫描 Downloads，414 个文件）触发的 6 次审批**全是纯查询命令**。
# ⚠️ 2026-10-02 由黑名单改回**白名单**：黑名单版实测可被 `bash -lc`/`2>`/引号拼接
# 绕过（审计 §4 根因 2/3/4）。白名单的代价是"没收录的只读命令会被问一次"——
# 在 fail-closed 原则下这是正确方向：宁可多问，不可漏放。
_READONLY_HEADS: frozenset[str] = frozenset({
    # POSIX 查询
    "ls", "pwd", "cat", "head", "tail", "less", "more", "find", "grep", "egrep", "fgrep",
    "rg", "awk", "sort", "uniq", "wc", "file", "stat", "du", "df", "free", "ps",
    "whoami", "id", "hostname", "uname", "date", "echo", "printf", "which", "where",
    "printenv", "env", "basename", "dirname", "readlink", "realpath", "tree", "sleep",
    "true", "false", "cd", "pushd", "popd", "exit", "set", "md5sum", "sha1sum",
    "sha256sum", "fc", "diff", "cmp", "join", "cut", "paste", "tr", "column", "jq",
    # Windows 查询
    "dir", "type", "ver", "systeminfo", "tasklist", "ipconfig", "where",
    # PowerShell 查询 cmdlet
    "get-childitem", "gci", "get-content", "gc", "get-item", "gi", "get-process", "gps",
    "get-service", "get-location", "get-command", "get-member", "get-help",
    "measure-object", "select-string", "sls", "test-path", "out-string",
    "select-object", "format-table", "format-list", "sort-object",
    # ★★ 2026-10-11（审批降噪）：真机命令形态补录 —— 这几条在 84 条真实命令里
    #   反复出现，且**默认用法就是"看一眼"**（写法级复核见 `_readonly_safe_head`）：
    #     · `sed -n '120,330p' render.mjs`   ← 看文件（`-i` 由 guards 挡 ✗）
    #     · `test -f x && echo OK`           ← 存在性探测（`[` 见 _READONLY_ALIASES ✓）
    #     · `ffmpeg -version` / `ffprobe -v error -show_entries …`  ← 问版本/问格式 ✓
    #     · `node -v` / `python -V`          ← 问版本 ✓（内联代码另有内联面拦 ✗）
    #     · `command -v ffmpeg`              ← 找程序在哪（透明包装，内层是 -v ✓）
    #     注意：`bash`/`sh`/`command` **故意不列** ✗ —— 它们由包装器面处理
    #     （`_WRAPPER_HEADS` / `_TRANSPARENT_WRAPPERS` ✓），列进白名单反而会
    #     抢在"透视不了 ⇒ ask"之前把它放行 ✗（那是 fail-closed 的核心 ✗）。
    "sed", "test", "ffmpeg", "ffprobe", "ffplay", "mediainfo", "identify", "magick",
    "node", "nodejs", "python", "python3", "py", "tar", "unzip", "gzip", "gunzip",
    "bzip2", "xz", "diff", "sleep",
})

# 出现这些片段一律**不**算只读（就地删除/执行、awk 的 system()、PowerShell 的 iex…）
_WRITE_MARKERS: tuple[str, ...] = (
    " -delete", " -exec", " -execdir", " -ok", " --delete", " -fprint", " -fls",
    "system(", "iex ", "invoke-expression", "add-type", "set-executionpolicy",
    "@'", '@"',
    # ★ 2026-10-11（平台闸修复 ③）：**awk 的"管道到命令"**也是执行 ✗
    #   `awk '/x/{print | "sh -c \"rm x\""}' file` —— 这是**审计过的一种绕过构造** ✓
    #   `is_pure_readonly` 只看首词（`awk` 在白名单里 ✓）⇒ 它曾判成"纯只读" ✗
    #   （旧代码只在"越界"那条路上用它、外面还套着结构嫌疑的面 ✓ 所以没漏 ✓；
    #    现在它成了"工作区内也放行"的主判据之一 ⇒ 这一条必须自己挡住 ✓）
    '| "', "| '",
)

# ★★★ 2026-10-11（审批降噪 · 真机 176 次弹门的诊断结论）★★★
#
# 现场：一条 18 秒卡点视频（DeepSeek flash）弹了上百次审批。拿那次任务的
# events.jsonl 里 **84 条真 shell 命令**过真判定重放 ⇒ **78 条判 ask** ✓
# 归因（`_diag_classify.py`）里两类白挨的：
#   · `outside:` 33 条 —— 全是**只读**（探测 ffmpeg / sed -n 看文件 / 调本仓 venv
#     跑工作区脚本）却被越界面拦下 ✗（只读快路排在越界面**之后** ⇒ 一次没跑到 ✗）
#   · 命令里的 `NAME=…` / `export NAME=…` 让"每段首词都要在只读白名单"**必然失败** ✗
#     哪怕值就是个静态路径（`FF="/d/MiniMax…/ffmpeg.exe"`）✓
# 下面三张表 + 两个小函数就是为这两条准备的 —— 判据只放宽到"**确定什么也没改**" ✓。

# 只读白名单的**同义词表**：同一个程序的"看一眼"写法有多个别名
_READONLY_ALIASES: dict[str, str] = {
    "[": "test", "[[": "test",
}

# 这些程序在**默认用法**下只读；但带上下面的开关就变成"写" ✗（放行前必须查一遍）
_READONLY_ARG_GUARDS: dict[str, tuple[str, ...]] = {
    # `sed -n '1,80p' file` 只读 ✓；`sed -i 's/a/b/' file` 就地改文件 ✗
    "sed": ("-i", "--in-place"),
    # `sort -o out.txt` 会写文件 ✗
    "sort": ("-o", "--output"),
    # `tar -x/-c` 是解包/打包（写）✗；`tar -t` 只是列内容 ✓
    #   ★ 但 `--to-command='sh -c id'` / `--use-compress-program=…` / `-I …` 是
    #     **执行外部程序** ✗（审计过的绕过构造，`test_assign_prefix` / `test_round9_fixes`
    #     两组红绿用例钉着 ✓）⇒ 这几个开关必须留在"写/执行"这一边 ✓。
    "tar": ("-x", "--extract", "-c", "--create", "-I", "--to-command",
            "--use-compress-program", "--checkpoint-action"),
    # `unzip -d dir` 解包 ✗
    "unzip": ("-d",),
}

# 媒体工具：**只有"问它自己的版本/能力"**算只读 ✓
#   `ffmpeg -version` / `ffmpeg -filters` ✓（不产出任何文件 ✓）
#   `ffmpeg -i in.mp4 out.mp4` ✗（出片就是写 ✓ 由下面的"没有输入输出文件"判据挡住 ✓）
_MEDIA_QUERY_HEADS: frozenset[str] = frozenset({
    "ffmpeg", "ffprobe", "ffplay", "mediainfo", "magick", "convert", "identify",
})
_MEDIA_QUERY_FLAGS: tuple[str, ...] = (
    "-version", "-buildconf", "-filters", "-formats", "-codecs", "-decoders",
    "-encoders", "-protocols", "-pix_fmts", "-sample_fmts", "-layouts", "-colors",
    "-h", "-help", "-hide_banner", "-loglevel", "-v", "-nostdin", "-show_entries",
    "-show_format", "-show_streams", "-select_streams", "-print_format",
    "-of", "-sexagesimal", "-pretty", "-bitexact", "-report",
    # ★ "只问不写"的开关集合（真机 84 条里 ffprobe 就是这么用的 ✓）
    #   —— `ffprobe -v error -show_entries format=duration -of default=nw=1 x.wav`
    #     是**读**那个 wav 的元数据 ✓ 不产出任何文件 ✓
    "-show_chapters", "-show_programs", "-show_data", "-count_frames",
    "-count_packets", "-show_private_data", "-read_intervals",
)
#: 出现这些就**不是**"问它自己"了 ✗（有输入/输出/滤镜 ⇒ 可能要产出文件 ✓）
#:   ★ `-of`（输出格式）**不在**这里 ✓ —— 它是 ffprobe 的查询开关（`-of default=nw=1` ✓），
#:     我第一版把它当成写开关 ✗ ⇒ 真机那条 `ffprobe … -of default=nw=1 x.wav` 被判死 ✓
_MEDIA_WRITE_FLAGS: tuple[str, ...] = (
    "-i", "-o", "-y", "-filter_complex", "-filter_complex_script", "-vf", "-af",
    "-map", "-c", "-c:a", "-c:v", "-f", "-ss", "-t", "-to", "-r", "-b:a", "-b:v",
)

# 解释器：**只有"问我自己的版本"**算只读 ✓（`python -c …` / `node -e …` 是内联代码 ✗
#   它们**照旧必问** ✓ —— 见 `_readonly_ok` 里那条"不放内联代码"的注释 ✓）
_INTERPRETER_VERSION_FLAGS: frozenset[str] = frozenset({
    "-v", "-vv", "--version", "-version",
})

_VAR_EXPANSION_RE = re.compile(r"\$[A-Za-z_{(]")
_ASSIGN_NAME_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=")


def _has_var_expansion(text: str) -> bool:
    """`$VAR` / `${VAR}` / `$(` 有没有出现（决定"这段内容能不能静态判定"）。"""
    return bool(_VAR_EXPANSION_RE.search(text)) or "`" in text


def _is_static_assign_value(value: str) -> bool:
    """赋的**值**能不能静态判定：不能有 `$`/反引号（命令替换、变量展开都不行 ✗）。

    ★ 这是"静态赋值不算脚本片段"那条放宽的**唯一凭据** ✓ ——
      值静态 ⇒ 这行除了设一个字符串什么也没干 ✓；值里带 `$` ⇒ 照旧当"判定不了" ✗。
    """
    v = value.strip().rstrip("\\").strip()
    return not _has_var_expansion(v)


def _strip_static_assigns(part: str) -> str:
    """剥掉**静态**赋值前缀：`NAME=value` / `export NAME=value` / `"NAME=value"`。

    ★ 为什么必须做（真机实测）：84 条命令里 **32 处** `NAME=…`、**53 处** `export …` ✓
      而 `_part_readonly` 原来是"一见 `=` 就判『脚本片段，判定不了』" ✗
      ⇒ 哪怕值只是个静态路径（`FF="/d/MiniMax…/ffmpeg.exe"`），整条命令也失去
      "只读"资格 ⇒ 后面明明全是 `ls`/`-version` 这类只看一眼的命令，照样弹审批 ✗。

    ★ 边界（一条都不松 ✓）：
      · 值里带 `$`/反引号 ⇒ **不剥** ✗（`PATH="/x:$PATH"` 仍按"判定不了"处理 ✓）
      · 只剥**开头**连续的赋值 ✗（`echo a=1` 里的 `=` 不在开头 ⇒ 不动它 ✓）
    """
    cur = _strip_wrapping_quotes(part).strip()
    for _ in range(20):
        if cur.lower().startswith("export ") and _is_static_assign_name(cur[7:].lstrip()):
            cur = cur[7:].lstrip()
            continue
        if not _is_static_assign_name(cur):
            break
        m = _ASSIGN_NAME_RE.match(cur)
        assert m is not None
        end = m.end() + len(_assign_value(m.end(), cur))   # ★ 引号感知取整词 ✗ 不是 split ✓
        cur = cur[end:].lstrip()
    return cur


def _is_static_assign_name(text: str) -> bool:
    """`text` 是不是以**静态** `NAME=value` 开头（供 `_strip_static_assigns` 用）。

    ★ 取"值"必须**按引号取整词** ✗ —— 我第一版用 `split()[0]`：
      `FF="/d/MiniMax Design/…/ffmpeg.exe"` 的值里有**空格** ⇒ 只看得到 `"/d/MiniMax`
      ⇒ 引号不配对 ⇒ 判成"非静态" ⇒ 整条命令失去只读资格 ✗
      （真机那批命令的路径**恰恰都带空格**：`MiniMax Design`、`Program Files (x86)` ✓）
    """
    s = _strip_wrapping_quotes(text).lstrip()
    m = _ASSIGN_NAME_RE.match(s)
    if not m:
        return False
    value = _assign_value(m.end(), s)
    if not value:
        return False
    if value[-1] in _QUOTES:
        # 整段被引号包住（`FF="/a b/ffmpeg.exe"`）⇒ `_assign_value` 会把收尾引号一起带上 ✗
        # —— 那是**赋值的语法**，不是值的一部分 ✓ 先摘掉它再判静态 ✓
        value = value[:-1]
    return _is_static_assign_value(value)


def _assign_value(start: int, text: str) -> str:
    """从 `start` 起取一个 shell 词的**原始文本**（引号感知；到空白/分隔符为止）。"""
    i, n = start, len(text)
    quote = ""
    while i < n:
        c = text[i]
        if quote:
            if c == quote:
                quote = ""
            i += 1
            continue
        if c in _QUOTES:
            quote = c
            i += 1
            continue
        if c == "\\" and i + 1 < n:
            i += 2
            continue
        if c.isspace() or c in ";&|<>":
            break
        i += 1
    return text[start:i]


def _static_assign_name(text: str) -> str:
    """`text` 开头那个**静态**赋值叫什么名字（不是静态赋值 ⇒ 空串）。"""
    s = _strip_wrapping_quotes(text).lstrip()
    if s.lower().startswith("export "):
        s = s[7:].lstrip()
    if not _is_static_assign_name(s):
        return ""
    m = _ASSIGN_NAME_RE.match(_strip_wrapping_quotes(s).lstrip())
    return m.group(1) if m else ""


def _clean_assign_value(value: str) -> str:
    """把一个赋值的字面量整理成"能当程序名/路径用"的样子 ✓。

    ★ 只摘**包围整段的成对引号** ✗ —— 值里带 `$`/反引号（动态）一律**原样返回** ✓
      （那种值本来就会被展开判据挡掉 ✓ 这里不该替它"洗干净" ✗）。
    """
    if _has_var_expansion(value):
        return ""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _note_static_assigns(parts: list[str]) -> set[str]:
    """把这一批子命令里的**静态赋值**记进黑板，返回见过的变量名集合 ✓。

    ★ 黑板是**每次判定都清空**的临时状态 ✗（见 `_ASSIGNED_VALUES` 的说明 ✓）。
    """
    names: set[str] = set()
    for part in parts:
        name = _static_assign_name(part)
        if not name:
            continue
        names.add(name)
        s = _strip_wrapping_quotes(part).lstrip()
        if s.lower().startswith("export "):
            s = s[7:].lstrip()
        m = _ASSIGN_NAME_RE.match(s)
        if not m:
            continue
        raw_value = _assign_value(m.end(), s)
        value = _clean_assign_value(raw_value.strip())
        if value:
            _ASSIGNED_VALUES[name] = value
    return names


_VAR_ONLY_RE = re.compile(r"""^\$[{]?([A-Za-z_][A-Za-z0-9_]*)[}]?$""")

#: `export NAME=value` 的**首 token 是 `export`**（不带 `=` ✗）—— 只看
#: `_raw_first_token` 会整段跳过"静态赋值剥离"那一步 ⇒ 53 处 `export …` 白挨 ✗。
_ASSIGN_SHAPED_RE = re.compile(r"^(?:export\s+|set\s+)?[\"']?[A-Za-z_][A-Za-z0-9_]*=")

#: 静态赋值占位符用的变量名（`${STATICVAR}` ✓ 见 `_substitute_static_vars` ✓）
_STATIC_VAR_NAME = "STATICVAR"

#: ★ 这一段里**已见过的静态赋值**：`名字 → 字面量值`（每次判定的**局部**状态 ✓）。
#:   为什么要它 ✗：`FF="/…/ffmpeg.exe"` 之后 `"$FF" -version` 里，
#:   `"$FF"` 这个"整段是变量"的形态要能回答"它其实是哪个程序" ✓
#:   —— 喊不出身份就只能判"看不懂" ⇒ 真机 26 处 `"$FF"`/`"$V"` 全被问 ✗。
#:   ★ 它是**一次判定内的临时黑板** ✗（不是缓存 ✗）：`check()` / `is_pure_readonly()`
#:     进门先清空 ✓（`_readonly_ok` 与 `_readonly_safe_head` 都是同步纯函数 ⇒ 无线程问题 ✓）。
#:     ⚠️ 我第一版把它做成"跨调用累加"✗ ⇒ 变量值会从上一个命令漏到下一个 ✗
#:     （`X=$(rm -rf /)` 那类动态赋值不该被下一个命令当成"已知"✓ 清空这条钉着 ✓）。
_ASSIGNED_VALUES: dict[str, str] = {}


def _is_assign_shaped(text: str) -> bool:
    """这一段**看起来就是赋值**吗（`NAME=` / `export NAME=` / `set NAME=`）。"""
    return bool(_ASSIGN_SHAPED_RE.match(_strip_wrapping_quotes(text).lstrip()))


#: 重定向 token（`2>&1` / `&>/dev/null` / `>/dev/null`）—— 它们决定"输出丢哪儿"，
#: 由 `_REDIRECT_TOKEN_RE` 那段负责 ✓；在"开关列表"里只算噪声 ⇒ 先剔掉 ✓。
_REDIRECT_TOKEN_ONLY_RE = re.compile(r"^(?:\d*|&)?>{1,2}\|?\s*(?:&\d|/dev/null|nul)$", re.I)


def _is_redirect_token(tok: str) -> bool:
    return bool(_REDIRECT_TOKEN_ONLY_RE.match(tok.strip().strip(_QUOTES)))


def _split_keeping_quoted_paths(text: str) -> list[str]:
    """按空白切词，但**引号内的空白不切** —— 只为让 `"…ffmpeg.exe"` 这种带引号的
    路径能被 `_head_of` 认出来 ✓。

    ★ 为什么必须补（真机实测）：`"/d/MiniMax Design/current/resources/ffmpeg/ffmpeg.exe"
      -version` 用 `split()` 会切成 `"/d/MiniMax`、`Design/current/…`、`ffmpeg.exe"`
      ✗ —— 首 token 是个**带引号的残缺路径** ⇒ `head` 变成 `minimax` ⇒ 明明在只读
      白名单里的 `ffmpeg` 认不出来 ✗（这次那批命令**恰恰全带空格路径** ✓）。

    ★ 稳妥起见只处理"**引号两两配平**"的情形 ✗：配不平（奇偶不对）就退回原来的
      `split()` —— 那时候任何"更聪明的切法"都可能引入我没想过的形态 ✗。
    """
    if sum(text.count(q) for q in _QUOTES) % 2:
        return text.split()
    out: list[str] = []
    buf: list[str] = []
    quote = ""
    for ch in text:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = ""
            continue
        if ch in _QUOTES:
            quote = ch
            buf.append(ch)
            continue
        if ch.isspace():
            if buf:
                out.append("".join(buf))
                buf = []
            continue
        buf.append(ch)
    if buf:
        out.append("".join(buf))
    return out

#: 这些短选项是**布尔**的（后面跟的那个词不是它的值）——`command -v ffmpeg` 的
#: `-v` 就是"查一下 ffmpeg 在哪" ✓，它**不吃** `ffmpeg` 这个参数 ✓。
_BOOL_ONLY_FLAGS: frozenset[str] = frozenset({"-v", "-V"})


def _var_only_reference(text: str, assigned: frozenset[str] | set[str] = frozenset()) -> str:
    """`"$FF"` / `${FF}` / `$FF` 这种"整段就是这个变量"的形态 ⇒ 返回**它被赋的那个值**。

    ★ 为什么要认它（真机实测）：`FF="/d/MiniMax…/ffmpeg.exe"` 之后紧跟
      `"$FF" -version` ✓ —— 值**上一行刚赋过**（静态字面量 ✓），这里只是把它当
      程序名用 ✓。整段就是变量 ⇒ 展开结果**确定**（就是那个静态值 ✓）⇒
      可以按"一个字面量"处理 ✓（调用方拿返回的路径去 `_head_of` 认程序 ✓）。
    ★ 只认**整段就是变量**这一种 ✗ —— `$VAR/suffix`、`${A}${B}` 这类一律不认 ✗
      ⇒ 判"判定不了" ⇒ 照旧问 ✓。
    ★ 返回的是**赋的那个值**（不是变量名）✗✓：调用方要能据此认出"这就是 ffmpeg" ✓ ——
      认不出（没赋过 / 动态赋的）就返回空串 ⇒ 照旧问 ✓。
      ⚠️ 我第一版返回变量名 ✗ ⇒ `"$FF"` 的首词成了 `$ff`（不在白名单）⇒ 判死 ✓
      （这条留在注释里，免得下一个人再踩 ✓）
    """
    m = _VAR_ONLY_RE.match(_strip_wrapping_quotes(str(text)).strip())
    if not m:
        return ""
    name = m.group(1)
    if name == _STATIC_VAR_NAME:                              # 占位符 ⇒ 上一段静态赋过 ✓
        return "STATICVALUE"
    if name not in assigned:
        return ""
    return _ASSIGNED_VALUES.get(name, "")


def _all_vars_known(text: str, assigned: frozenset[str] | set[str]) -> bool:
    """这段里出现的**每一个** `$VAR`/`${VAR}` 都在上文里静态赋过值吗 ✓。

    ★ 有它就够了吗 ✗：光"认得名字"不代表"内容安全" ✗ —— 调用方拿到 True 之后
      仍要把这些变量**换成中性字面量**再逐段判（`sed -n "$P"` ✓ / `sed -i "$P"` ✗）✓。
    """
    refs = set(re.findall(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?", text))
    if not refs:
        return False
    return all((r in assigned) or (r == _STATIC_VAR_NAME) for r in refs)


def _mask_known_vars(text: str, assigned: frozenset[str] | set[str]) -> str:
    """把**已知静态变量**换成中性字面量 —— 判据照旧跑 ✓。

    ★ 为什么是"中性字面量"而不是"把值填进去" ✗：
      值是路径（`/d/MiniMax…/ffmpeg.exe` ✓）—— 填进去反而把**程序名**塞到参数位上，
      语义就乱了 ✗。这一步只回答"这里有个**内容确定**的东西"✓；
      "它到底是什么程序"由 `_var_only_reference` 专门回答 ✓（两条路分工 ✓）。
    ★ 认不出来的变量**原样留着** ✗ ⇒ 后面的展开判据照旧拦 ✓（照旧问 ✓）。
    """
    names = {n for n in (assigned or ())} | ({_STATIC_VAR_NAME} if _STATIC_VAR_NAME else set())
    if not names:
        return text
    pat = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
    return re.sub(rf"\$\{{?(?:{pat})\}}?", "STATICVALUE", text)


def _has_real_redirect(command: str) -> bool:
    """整条命令里有没有**重定向到真实文件**（`> out.txt` / `>> log` ✗）吗。

    ★ 为什么要它（红绿门当场抓出来的 ✗）：`awk '{print > "/x/out.txt"}' in.txt`
      的重定向藏在**引号里** ✗ —— `is_pure_readonly` 只看"每段首词在不在白名单"
      （awk 在 ✓）⇒ 判成"纯只读" ⇒ 让写文件的命令走了只读快路 ✗
      （既有用例 `test_bypass_constructions_ask` 当场变红 ✓）。
      这里按**原文**扫一遍重定向（引号里的也算 ✓ —— 那正是 awk 的写法 ✓），
      只放过 `/dev/null` 与 `&1`/`&2` 这类 fd 复用 ✓。
    """
    for m in _REDIRECT_TOKEN_RE.finditer(command):
        tgt = m.group(1)
        if tgt.lower() in _NULL_TARGETS or _FD_DUP_RE.fullmatch(tgt):
            continue
        return True
    return False


def _readonly_safe_head(head: str, toks: list[str], part: str) -> bool:
    """白名单 head 的**用法级**复核：这几种程序换个开关就从"看"变"写" ✓。

    只读白名单本身判的是"**是哪个程序**" ✓；这里补一层"**这次怎么用它**" ✓：
      · `sed -n …` 只看 ✓ / `sed -i …` 就地改 ✗
      · `ffmpeg -version` 问版本 ✓ / `ffmpeg -i in out` 出片 ✗
      · `python -V` 问版本 ✓ / `python -c "…"` 内联代码 ✗（后者另有内联面拦 ✓）

    ★ 开关列表里**先剔掉重定向 token** ✗（`2>&1` / `2>/dev/null` / `>/dev/null`）——
      它们是"把输出丢到哪儿" ✓ 由上面那段重定向检查负责 ✓；留在这里只会被当成
      一个"不认识的开关" ⇒ 把 `node -v 2>&1` 这种纯查询判死 ✗（真机就有 ✓）。
    """
    low_toks = [t.strip(_QUOTES).lower() for t in toks[1:]]
    low_toks = [t for t in low_toks if not _is_redirect_token(t)]
    guards = _READONLY_ARG_GUARDS.get(head)
    if guards and any(t == g or t.startswith(g + "=") for t in low_toks for g in guards):
        return False
    if head in _MEDIA_QUERY_HEADS:
        # 只认"问它自己"：带 -version/-filters/… 之类查询开关，且**不出现输入输出文件**
        if not any(any(t.startswith(f) for f in _MEDIA_QUERY_FLAGS) for t in low_toks):
            return False
        if any(t in _MEDIA_WRITE_FLAGS for t in low_toks):
            return False
        # `-f lavfi` 这类"凭空造流"也算产出 ⇒ 只看上面那批"只问不写"的开关 ✓
        if not any(t in _MEDIA_QUERY_FLAGS for t in low_toks):
            return False
    if head in _INTERPRETERS:
        # 解释器只有在"只带版本开关、不带任何脚本/内联开关"时才算只读
        if not low_toks:
            return False
        #   ★ `-v`/`-V` 也是"问版本" ✓（node -v / perl -v ✓）—— 别把它们当成
        #     内联开关挡掉 ✗（真机 `node -v 2>&1` 就是这么写的 ✓）。
        if any(t.startswith(("-c", "-e", "-r", "--eval", "--command", "-"))
               and t not in _INTERPRETER_VERSION_FLAGS and not t.startswith("--v")
               for t in low_toks):
            return False
        if not all(t in _INTERPRETER_VERSION_FLAGS or t.startswith("--v") for t in low_toks):
            return False
    return True



def _iter_redir_targets(text: str) -> list[str]:
    """引号感知的重定向目标扫描器（替代正则：正则看不见 `> "quoted file"` 的目标）。

    支持 `>` `>>` `>|` `>>|` 与 `2>` `&>` 等 fd 前缀；`&1`/`&2`（fd 复用）与
    /dev/null 家族由调用方过滤。返回**原始写法**（含引号），调用方自行 strip。
    """
    targets: list[str] = []
    i, n = 0, len(text)
    quote = ""
    while i < n:
        c = text[i]
        if quote:
            if c == quote:
                quote = ""
            i += 1
            continue
        if c in _QUOTES:
            quote = c
            i += 1
            continue
        if c != ">":
            i += 1
            continue
        j = i + 1
        if j < n and text[j] == ">":
            j += 1
        if j < n and text[j] == "|":
            j += 1
        while j < n and text[j] in " \t":
            j += 1
        if j >= n:
            break
        if text[j] == "&":  # &1 / &2 fd 复用
            k = j + 1
            while k < n and text[k].isdigit():
                k += 1
            targets.append(text[j:k])
            i = k
            continue
        if text[j] in _QUOTES:  # > "my file.txt"
            q = text[j]
            k = text.find(q, j + 1)
            if k == -1:
                break
            targets.append(text[j : k + 1])
            i = k + 1
            continue
        k = j
        while k < n and text[k] not in " \t;&|<>":
            k += 1
        targets.append(text[j:k])
        i = k
    return [t for t in targets if t]


def _strip_wrapping_quotes(s: str) -> str:
    s = s.strip()
    while len(s) >= 2 and s[0] in _QUOTES and s[-1] == s[0]:
        s = s[1:-1].strip()
    return s


def _strip_var_prefix(ts: list[str]) -> list[str]:
    """剥 shell 赋值前缀（NAME=…，含引号/转义空格变体）与引号残片。

    二十三轮 🔴1：FOO=a<转义空格>b 与 "FOO=x"（带引号名）都会真执行
    docker——统一处理：NAME= 开头即剥；反斜杠结尾的值续体（以反斜杠结尾的
    值段）一并剥；引号残片跳过。

    ★ 二十六轮第 7 批：本函数原先只是【某一个判定面】里的局部闭包，导致
    git/pip/net/写入等其它按 head 判定的面看不到真 head（见 _strip_assign_prefix_str）。
    现提升为模块级单一实现，全仓共用。
    """
    while ts and (
        re.match(r"[A-Za-z_][A-Za-z0-9_]*=", ts[0])
        or re.match(r'"[A-Za-z_][A-Za-z0-9_]*=', ts[0])
    ):
        escaped = ts[0].endswith("\\")
        ts = ts[1:]
        if escaped and ts:
            ts = ts[1:]  # 转义空格的续体（FOO=a\ 后的 b）
    # 引号残片：含引号、不含 =、短的碎片（b' / 'a / x"）
    while ts and len(ts[0]) <= 4 and ("'" in ts[0] or '"' in ts[0]) and "=" not in ts[0]:
        ts = ts[1:]
    return ts


# 赋值前缀的"头"：允许前导空白，NAME 必须是 shell 合法标识符后紧跟 =
_ASSIGN_HEAD_RE = re.compile(r"\s*([A-Za-z_][A-Za-z0-9_]*)=(?!=)")


def _skip_shell_word(s: str, i: int) -> int:
    """从下标 i 起吃掉一个 shell 词，返回词尾下标（处理 '…' / "…" / 反斜杠转义）。

    只做"找词尾"这一件事，但这一件事正是 token 级启发式做不到的：
    `FOO='!echo PWNED' cmd` 用 `.split()` 会被切成 `FOO='!echo` 与 `PWNED'`
    两段（引号内的空白【不是】分隔符），于是截断点落在引号中间、
    残余部分变成 `PWNED' cmd`，真命令永远露不出来。
    """
    n = len(s)
    while i < n and not s[i].isspace():
        c = s[i]
        if c == "'":                      # 单引号：内部无转义，找下一个 '
            j = s.find("'", i + 1)
            i = n if j < 0 else j + 1
        elif c == '"':                    # 双引号：支持 \" 转义
            j = i + 1
            while j < n and s[j] != '"':
                j += 2 if s[j] == "\\" else 1
            i = n if j >= n else j + 1
        elif c == "\\":                   # 反斜杠转义下一个字符（FOO=a\ b）
            i += 2
        else:
            i += 1
    return min(i, n)


def _strip_assign_prefix_str(part: str) -> str:
    """剥掉命令开头的 shell 赋值前缀（`NAME=value …`），**其余部分逐字节不动**。

    ★ 用【引号感知的扫描器】而不是 `.split()` 启发式（二十六轮第 7 批实测教训：
      第一版照搬 token 级 `_strip_var_prefix`，对**含空格的长引号值**会截断在
      引号中间——`FOO='!echo PWNED' git push origin main` 残余成 `PWNED' git push…`，
      真 head 露不出来，该形态仍然绕过）。本版按 shell 词法吃词：
      单引号到下一个 `'`、双引号支持 `\\"` 转义、裸词支持 `\\ ` 转义。

    另外【不 split/join】：下游有大量按引号/正则分析的判定面（重定向、
    find -exec、脚本扫描），重建字符串会破坏结构。没剥掉任何东西时原样返回。

    为什么需要它：`FOO=bar cmd` 是 POSIX shell 的合法写法（容器里就是 sh），
    而多条判定面各自 `part.split()[0]` 取 head ⇒ 看到 "FOO=bar" 就不认识本体，
    整族绕过（实测 `FOO=bar git push origin main` / `FOO=bar dd of=$HOME/x`
    三模式全 PASS，而本体都是 ASK）。在 `_deep_parts` 出口统一剥，一处修好所有面。
    """
    i, n = 0, len(part)
    while i < n:
        m = _ASSIGN_HEAD_RE.match(part, i)
        if not m:
            break
        j = _skip_shell_word(part, m.end())
        if j >= n:
            return ""                     # 整条就是一个赋值（`FOO=bar`）
        if not part[j].isspace():
            break                         # 值后面不是空白 ⇒ 这不是前缀，别动它
        i = j
    return part if i == 0 else part[i:].lstrip()


# git 的"从环境变量注入配置"通道（git 2.31+）——键写 core.pager / alias.* 等
# 即执行外部命令，与 `-c` / `--config-env` 同一能力面。
_GIT_CONFIG_ENV_NAME_RE = re.compile(r"GIT_CONFIG_(?:COUNT|KEY_\d+|VALUE_\d+)$", re.I)


def _leading_assign_names(part: str) -> list[str]:
    """命令开头那串 shell 赋值前缀的【变量名】（按出现顺序）。

    复用与 `_strip_assign_prefix_str` 完全相同的词法（引号感知、支持 `\\ ` 转义），
    而且**只取前缀、不看中段**——所以 `echo GIT_CONFIG_COUNT=1` 这种"只是把字符串
    打印出来"的写法不会被误判成注入。
    """
    names: list[str] = []
    i, n = 0, len(part)
    while i < n:
        m = _ASSIGN_HEAD_RE.match(part, i)
        if not m:
            break
        names.append(m.group(1))
        j = _skip_shell_word(part, m.end())
        if j >= n or not part[j].isspace():
            break
        i = j
    return names


def _is_wrapper_arg(token: str) -> bool:
    """前缀包装自己的参数（不是真正的动作词）：`timeout 5 rm` 的 `5`、`nice -n 10 rm` 的
    `-n`/`10`、`env FOO=1 rm` 的 `FOO=1`。

    二十五轮 🔴2 配套：路径形态 token 也是包装器的文件参数（`flock <锁文件>`
    的锁文件）——此前留在变体首位，容器面入口会把它当"未知包装器"误拦
    （head 变成 l.lock），动词面也会停在路径上漏看真正的动词
    （`flock /tmp/l rm -rf x` 的 rm）。"""
    t = token.strip(_QUOTES)
    if re.fullmatch(r"[\d.]+[smhd]?", t) or t.startswith("-") or "=" in t:
        return True
    if t.startswith(("/", "./", "../")) or re.match(r"[A-Za-z]:[\\/]", t):
        return True
    return False


def _strip_wrapper_prefix(toks: list[str]) -> str:
    """剥掉透明包装器的本体与自身参数，返回内层命令文本。

    第四轮复验：`xargs -a list.txt shred` 之前解析成 `list.txt shred`（-a 的值被当
    命令，shred 漏判）——短选项（≤2 字符）后跟一个 token 时视为"选项带值"，两者
    一起跳过。`find … | xargs shred` 里 shred 才成为内层首词。

    ★ 2026-10-11（审批降噪）：`command -v x` 是**内建查询**（"x 在哪"）✓ ——
      `-v`/`-V` 在 `command` 这个内建下**不带值** ✗，可上面那条"短选项带值"把
      `ffmpeg` 当成了 `-v` 的值吞掉 ⇒ 内层变成 `which ffprobe`，而 `command` 的
      内层是 `ffmpeg`（不在只读白名单）⇒ 一条纯查询命令被判"包装器透视不了" ✗。
      真机 84 条里 `which ffmpeg ffprobe` / `command -v ffmpeg` 就是这形态 ✓。
    """
    rest = toks[1:]
    i = 0
    while i < len(rest):
        t = rest[i].strip(_QUOTES)
        if t.startswith("-"):
            if len(t) <= 2 and i + 1 < len(rest) and t not in _BOOL_ONLY_FLAGS:
                i += 2  # 短选项带独立值（-a list.txt / -n 10 / -I {}）
            else:
                i += 1
            continue
        if _is_wrapper_arg(t):
            i += 1
            continue
        break
    return " ".join(rest[i:]).strip()


def _to_windows_path(token: str) -> str:
    """`/c/Users/y/Desktop` → `C:\\Users\\y\\Desktop`（Git Bash 形态）。"""
    m = _GITBASH_DRIVE_RE.match(token)
    if m:
        return f"{m.group(1).upper()}:\\" + m.group(2).replace("/", "\\")
    return token


def _normalize_path(token: str) -> str:
    """把各种写法的同一路径归一到同一种形态（记忆键统一靠它）。"""
    cand = _to_windows_path(token)
    try:
        cand = os.path.expanduser(cand)
    except Exception:
        pass
    return cand.replace("\\", "/").lower()


class Verdict(NamedTuple):
    """审批判定结果。前两个字段与旧版 `(原因, 记忆键)` 位置兼容；`action` 区分
    「要人确认」与「只读越界自动放行」。"""

    reason: str
    key: str
    action: str = "ask"  # "ask"（弹审批） | "allow_readonly"（放行但记审计）


class ApprovalManager:
    def __init__(self) -> None:
        # (task_id, call_id) -> Future —— 键里带任务，见文件头规则 ⑤
        self._pending: dict[tuple[str, str], asyncio.Future[str]] = {}
        # ★ 2026-10-07（第 9 项补）：(task, call) → 那条命令 ✓
        #   给审计账用 ✓（"我批的是哪条命令" ✓）由 `note_command` 写、`command_of` 读 ✓
        self._cmds: dict[tuple[str, str], str] = {}
        self._always: dict[str, set[str]] = {}  # task_id -> 已放行的记忆键
        # ★「本任务全部允许」：task_id 集合。**只在内存**（重启即失效），
        #   且是用户对某个具体任务的明确授权（含 $()/heredoc 这类平时故意不记忆的结构性命令）。
        self._allow_all: set[str] = set()
        # ★★ 2026-10-06「这类以后都别问」：**跨任务、能持久化** ✓
        #   程序名（命令首词）-> 备注/时间 ✓。落盘在 `data/approvals/forever.json` ✓
        #   （用户按过才写 ✓，随时能在设置里收回 ✓）。
        self._forever: dict[str, str] = self._load_forever()

    # ---------- 「这类以后都别问」的持久化 ----------

    @staticmethod
    def _forever_path() -> Path:
        # backend/app/approval.py -> backend/data/approvals/forever.json
        return Path(__file__).resolve().parent.parent / "data" / "approvals" / "forever.json"

    def _load_forever(self) -> dict[str, str]:
        try:
            p = self._forever_path()
            if p.exists():
                data = json.loads(p.read_text("utf-8"))
                if isinstance(data, dict):
                    return {str(k): str(v) for k, v in data.items()}
        except Exception:                                   # noqa: BLE001
            pass                                            # 读不出来就当空 ✓（别让启动挂掉 ✗）
        return {}

    def _save_forever(self) -> None:
        try:
            p = self._forever_path()
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(self._forever, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:                                   # noqa: BLE001
            pass

    @staticmethod
    def _forever_key(command: str) -> str:
        """从命令里取"这类"的代表：**命令的第一个词**（程序名）✓。

        · `python xxx.py` ✓ `pytest -q` ✓ `git status` ✓ ⇒ 分别记 `python` / `pytest` / `git` ✓
        · 带路径的解释器（`...\\.venv\\Scripts\\python.exe x.py`）取**文件名** ✓ ⇒ `python.exe` ✓
        · 结构类（`$()`/heredoc…）返回空串 ⇒ 永不记忆 ✓（审计 P1：一次豁免 = 整类任意执行 ✗）
        """
        raw = str(command or "").strip()
        if not raw:
            return ""
        head = raw.split()[0].strip("\"'")
        if not head or any(ch in raw[:80] for ch in ("$(", "`", "<<", "&&", "||", "|")):
            # 带管道/串联/命令替换的，**不认"这类"** ✗ —— 它已经不是"一个程序"了 ✓
            return ""
        base = re.split(r"[\\/]", head)[-1].lower().strip()
        base = re.sub(r"\.(exe|cmd|bat|ps1|sh)$", "", base)
        return base if base.isidentifier() or base in ("pip", "uv", "npx", "npm", "node", "git",
                                                       "pytest", "python", "python3") else ""

    @classmethod
    def _forever_unsafe(cls, command: str) -> bool:
        """**破坏性动词一律不享受"以后别问"** ✗ —— 删/移/改名/覆盖写 照问 ✓。"""
        flat = cls._flatten(str(command or ""))
        if cls._DESTRUCTIVE.search(flat):
            return True
        head = flat.strip().split()[0].lower() if flat.strip() else ""
        if head in {h.lower() for h in _DESTRUCTIVE_OVERWRITE_HEADS}:
            return True
        return bool(re.search(r"(?i)(^|[\s;&|])(rm|del|erase|rmdir|rd|mv|ren|remove-item)\b", flat))

    def allow_forever(self, command: str) -> str:
        """记下"这类以后都别问" ✓ 返回记住的键（空串 = 这条不适合记 ✓）。"""
        key = self._forever_key(command)
        if not key or self._forever_unsafe(command):
            return ""
        self._forever[key] = time.strftime("%Y-%m-%d %H:%M")
        self._save_forever()
        return key

    def forever_rules(self) -> dict[str, str]:
        """当前"以后都别问"的清单（给设置页看与收回 ✓）。"""
        return dict(self._forever)

    def revoke_forever(self, key: str = "") -> list[str]:
        """收回一条（key 为空 = 全收回 ✓）。返回剩下的。"""
        if key:
            self._forever.pop(key, None)
        else:
            self._forever.clear()
        self._save_forever()
        return sorted(self._forever)

    # ---------- 命令分析 ----------

    @classmethod
    def unwrap(cls, command: str, depth: int = 4) -> str:
        """逐层拆掉 shell 包装：`bash -c "rm -rf x"` → `rm -rf x`。"""
        cur = _strip_wrapping_quotes(command)
        for _ in range(depth):
            m = _WRAPPER_RE.match(cur)
            if not m:
                break
            cur = _strip_wrapping_quotes(m.group(1))
        return cur

    @staticmethod
    def _head_of(token: str) -> str:
        """单个 token → 可执行名：**引号归一化**（`r''m`→`rm`，审计 §4 根因）、
        去路径前缀、去 .exe、小写。

        ★ 2026-10-11（审批降噪）：**整词被引号包住的路径先拆壳** ✗ ——
          `"/d/MiniMax Design/current/resources/ffmpeg/ffmpeg.exe" -version` 里
          首词是**带引号的一整条路径**（路径本身含空格 ✓）⇒ 只 `rsplit("/")` 拿到的是
          `minimax design/current/resources/ffmpeg/ffmpeg.exe"` ✗ 根本认不出是 ffmpeg ✗
          （真机 84 条里 ffmpeg/ffprobe 的调用**全是这种带引号的写法** ✓）。
        """
        t = _strip_wrapping_quotes(str(token).strip())
        t = re.sub(r"[\"'`]", "", t)  # 引号拼接（r''m / 'r'm）先拆掉
        t = t.lstrip("(")  # 第四轮复验：`(\rm -rf x)` 前导括号此前让首词判定失效
        t = t.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        low = t.lower()
        low = low[:-4] if low.endswith(".exe") else low
        # 第五轮复验：PowerShell 调用形态 `iex('…')` / `Invoke-Expression('…')` /
        # 尾随括号 —— 统一取第一个 `(` 之前的函数名（大小写归一后再判，顺序关键）
        if "(" in low:
            low = low.split("(", 1)[0].strip()
        return low

    @staticmethod
    def _raw_first_token(part: str) -> str:
        """子命令的原始首 token（**不做**路径/引号处理 —— 变量赋值要靠它识别）。"""
        part = part.strip().lstrip("(").strip()
        return part.split()[0] if part else ""

    @classmethod
    def _split_top(cls, command: str) -> list[str]:
        """引号感知的顶层切分：按 `;` `&&` `||` `|` 换行切成子命令。

        引号内的分隔符不切（`awk '{n++; s+=$1}'` 是一条命令）；`\\;` 转义不切
        （`find -exec rm {} \\;`）；`2>&1`、`&>file` 里的 & 不切。
        """
        parts: list[str] = []
        buf: list[str] = []
        quote = ""
        i, n = 0, len(command)
        while i < n:
            c = command[i]
            if quote:
                buf.append(c)
                if c == quote:
                    quote = ""
                i += 1
                continue
            if c in _QUOTES:
                quote = c
                buf.append(c)
                i += 1
                continue
            if c == "\\" and i + 1 < n and command[i + 1] in ";&|":
                buf.append(command[i + 1])  # 转义的分隔符按字面量保留
                i += 2
                continue
            if c in ";&|\n":
                nxt = command[i + 1] if i + 1 < n else ""
                prev = command[i - 1] if i > 0 else ""
                if command[i : i + 2] in ("&&", "||"):
                    parts.append("".join(buf))
                    buf = []
                    i += 2
                    continue
                # fd 复用 / &> 重定向里的 & 不切：2>&1（& 前是 >）、&>file（& 后是 >）。
                # 注意不能按"前是数字"保留：`sleep 5& rm x` 的 5& 是后台化分隔（审计 P1）。
                if c == "&" and (prev in (">", "&") or nxt in (">", "&")):
                    buf.append(c)
                    i += 1
                    continue
                # 第六轮复验 P0-2：`>| file`（覆写重定向）——`|` 紧跟 `>` 不是管道，
                # 保留在子命令里让重定向扫描器看见目标
                if c == "|" and prev == ">":
                    buf.append(c)
                    i += 1
                    continue
                parts.append("".join(buf))
                buf = []
                i += 1
                continue
            buf.append(c)
            i += 1
        parts.append("".join(buf))
        return [p.strip() for p in parts if p.strip()]

    @classmethod
    def _deep_parts(cls, command: str, depth: int = 0) -> list[str]:
        """**所有层级**的子命令：包装器（bash -c "…"）的内层也展开——
        供动词判定用。旧版只拆"整条命令开头"的壳，`echo hi && bash -c "rm …"`
        里的 rm 永远看不见（审计 §4 根因 1）。"""
        if depth > 6:
            return []
        out: list[str] = []
        for part in cls._split_top(command):
            # ★ 二十六轮第 7 批（审计方修的真洞）：命令开头的 shell 赋值前缀
            #   （`FOO=bar cmd`）必须在这里统一剥掉。此前只有容器包装器那一条面
            #   自己剥（旧局部闭包），而 git/pip/net/写入/动词等【所有按 head 判定】
            #   的面都是 part.split()[0] 直接取 head ⇒ 看到 "FOO=bar" 就不认识本体，
            #   整族静默放行。实测（三模式一致）：`FOO=bar git push origin main`
            #   （net 面）、`FOO=bar dd if=/dev/zero of=$HOME/x`（写入面）、
            #   `FOO=bar git -c core.pager='…' log`（间接执行面）全部 PASS，
            #   而它们去掉前缀后本体都是 ASK。改在咽喉点一处，所有面同时修好；
            #   其余部分【逐字节不动】（不 split/join，保引号结构）。
            part = _strip_assign_prefix_str(part)
            if not part.strip():
                continue  # 纯赋值片段（`FOO=bar` 单独成段）不是命令，跳过
            out.append(part)
            cur = _strip_wrapping_quotes(part)
            toks = cur.split()
            if toks and cls._head_of(toks[0]) in _WRAPPER_HEADS:
                m = _WRAPPER_RE.match(cur)
                if m and _strip_wrapping_quotes(m.group(1)).strip():
                    # 剥掉外层引号再递归：否则内层的 ; 被引号保护，切不开
                    out.extend(cls._deep_parts(_strip_wrapping_quotes(m.group(1)), depth + 1))
            elif toks and cls._head_of(toks[0]) in _TRANSPARENT_WRAPPERS:
                # 第四轮复验：`find … | xargs shred` 的 shred 藏在透明包装里——
                # 展开内层，让 always-ask/动词/脚本扫描都看得见（沙箱内外都展开）
                inner = _strip_wrapper_prefix(toks)
                if inner and inner != cur:
                    out.extend(cls._deep_parts(inner, depth + 1))
            elif toks and cls._head_of(toks[0]) in _CONTAINER_CLIS:
                # 第九轮复验⑥：**按形态而非名称**——podman/nerdctl/ctr 与 docker
                # 同能力；compose 的 run/exec 之前允许任意带值全局选项
                # （`docker compose --profile prod run …` 此前因 toks[2]='--profile'
                #  不是 run/exec 而整体失明）。
                body_tokens = cls._container_exec_body(toks)
                if body_tokens:
                    out.extend(cls._deep_parts(" ".join(body_tokens), depth + 1))
        return out

    @classmethod
    def _compose_exec_sub(cls, toks: list[str], i: int) -> str:
        """compose 之后跳掉任意带值选项，返回真实子命令（找不到返回 ""）。"""
        n = len(toks)
        j = i + 1
        while j < n:
            tk = toks[j].strip(_QUOTES)
            if not tk.startswith("-"):
                break
            j += 1 if ("=" in tk or tk in _CONTAINER_BOOL_FLAGS) else 2
        return cls._head_of(toks[j]) if j < n else ""

    @classmethod
    def _container_exec_body(cls, toks: list[str]) -> list[str]:
        """容器 CLI 的**真实执行体**提取（docker/podman/nerdctl/ctr，含 compose）。

        统一形态：跳过任意全局/子命令选项（默认带值，布尔标志单跳）找 run/exec；
        执行体 = 其后跳掉标志与 1 个名字（ctr run 是 image+container 两个名字）。
        """
        n = len(toks)
        i = 1
        # 阶段 0：全局选项（--context xxx / -H xxx…）
        while i < n:
            tk = toks[i].strip(_QUOTES)
            if not tk.startswith("-"):
                break
            i += 1 if ("=" in tk or tk in _CONTAINER_BOOL_FLAGS) else 2
        if i >= n:
            return []
        cli = cls._head_of(toks[0])
        sub = cls._head_of(toks[i])
        if sub == "compose":
            # 第九轮⑥：compose 之后、run/exec 之前可再有任意带值选项
            # （--profile prod / -f file / --env-file f）
            j = i + 1
            while j < n:
                tk = toks[j].strip(_QUOTES)
                if not tk.startswith("-"):
                    break
                j += 1 if ("=" in tk or tk in _CONTAINER_BOOL_FLAGS) else 2
            if j >= n or cls._head_of(toks[j]) not in _DOCKER_COMPOSE_SUBS:
                return []
            return cls._extract_container_body(toks, start=j + 1, skip_names=1)
        if sub in ("run", "exec"):
            if cli == "ctr" and sub == "run":
                # ctr run [flags] IMAGE CONTAINER [cmd…]：两个位置名
                return cls._extract_container_body(toks, start=i + 1, skip_names=2)
            return cls._extract_container_body(toks, start=i + 1, skip_names=1)
        if cli == "ctr" and sub in ("task", "tasks") and i + 1 < n and cls._head_of(toks[i + 1]) == "exec":
            # ctr task(s) exec [flags] CONTAINER cmd…：一个位置名
            return cls._extract_container_body(toks, start=i + 2, skip_names=1)
        return []

    @staticmethod
    def _extract_container_body(toks: list[str], start: int, skip_names: int = 1) -> list[str]:
        """从容器命令里剥出**真实执行体**。

        第六轮复验⑤：原实现用"已知带值选项白名单"——白名单外的 `--foo val` 会被
        拆成两个 token，`val` 被当镜像/服务名，执行体整体错位（各面失明）。
        改法：**默认假设带值**，只豁免已知的**布尔标志**（--rm/-d/-i…）。
        过度跳过（把镜像名当值）无害——阶段 2 反正要跳名字。
        第九轮⑥：skip_names=2 用于 ctr run（image + container 两个位置名）。
        """
        i = start
        n = len(toks)
        # 阶段 1：跳过选项（默认连值一起跳；= 形式与布尔标志单跳）
        while i < n:
            tk = toks[i].strip(_QUOTES)
            if not tk.startswith("-"):
                break
            if "=" in tk or tk in _CONTAINER_BOOL_FLAGS:
                i += 1
            else:
                i += 2  # 假定的"标志 + 值"（白名单外也安全：只会多跳）
        # 阶段 2：跳过镜像名/容器名/服务名（默认一个 token）
        i += skip_names
        if i > n:
            i = n
        return [tk.strip(_QUOTES) for tk in toks[i:] if tk.strip(_QUOTES)]

    @classmethod
    def _verb_hits(cls, command: str, approval_required: Iterable[str]) -> list[str]:
        """危险动作判定：**所有层级**子命令的首词（跳过前缀包装；引号归一化后判定），
        外加 `-exec`/`-execdir`/`-ok` 后面跟的那个词。

        2026-09-30 修正：不扫全文找危险词——`grep -nE '\\brm\\b' …` 的正则字面量
        不再误报（-exec 匹配前剥引号）。2026-10-02：首词判定加引号归一化
        （`r''m -rf …` 不再隐身），并透视包装器内层。
        """
        verbs = {str(v).lower() for v in approval_required}
        hits: list[str] = []
        for part in cls._deep_parts(command):
            toks = part.split()
            i = 0
            while i < len(toks) and (
                cls._head_of(toks[i]) in _VERB_SKIP_WRAPPERS
                or _is_wrapper_arg(toks[i])  # 不限 i>0：`FOO=1 rm x` 的赋值前缀也要穿透（审计 P0）
            ):
                i += 1
            if i < len(toks):
                h = cls._head_of(toks[i])
                if h in verbs and h not in hits:
                    hits.append(h)
            bare = _QUOTED_RE.sub("''", part).lower()
            for m in re.finditer(r"(?:-exec|-execdir|-ok)\s+([^\s;|&]+)", bare):
                h = cls._head_of(m.group(1))
                if h in verbs and h not in hits:
                    hits.append(h)
        return hits

    @staticmethod
    def _outside_key(norm: str) -> str:
        """越界路径的**记忆键**：按归一化后的**目录前几段**（同一目录的多种写法算一个键）。

        ★ 单一事实来源 ✗：原来这段只写在 ④ 越界面里 ✓，这次"只读快路"也要用同一个键 ✓
          ⇒ 提出来共用 ✓（两处各写一份迟早跑偏 —— 本仓栽过五次的"多处口径打架"✓）。
        """
        return "outside:" + "/".join(str(norm).split("/")[:4])

    @staticmethod
    def _outside_paths(command: str, is_inside: Callable[[str], bool]) -> list[tuple[str, str]]:
        """命令里指向工作区 / allowed_dirs 之外的绝对路径。"""
        out: list[tuple[str, str]] = []
        seen: set[str] = set()
        for token in _ABS_PATH_RE.findall(command):
            norm = _normalize_path(token)
            if norm in seen:
                continue
            seen.add(norm)
            try:
                inside = bool(is_inside(norm))
            except Exception:
                inside = False
            if not inside:
                out.append((token, norm))
        return out

    # ---------- ① 结构嫌疑 ----------

    @classmethod
    def _structural_suspicion(cls, command: str) -> tuple[str, str] | None:
        """看不懂的形态 → (记忆键 slug, 给人看的理由)；全看懂 → None。"""
        # 命令替换 / 进程替换 / heredoc：内容无法静态判定。原文级检测——
        # 引号里的 $() 一样会执行（审计 P0：`cat <(恶意脚本)` 曾被判"只读"放行）。
        if "$(" in command or "`" in command or "<(" in command or ">(" in command:
            return ("subst", "命令包含命令替换（$()/反引号/进程替换），无法静态判定，需人工确认")
        if "<<" in command:
            return ("heredoc", "命令包含 heredoc（内联脚本体无法静态判定），需人工确认")
        for part in cls._split_top(command):
            hit = cls._part_suspicion(part, depth=0)
            if hit:
                return hit
        return None

    @classmethod
    def _part_suspicion(cls, part: str, depth: int) -> tuple[str, str] | None:
        if depth > 6:
            return ("deep", "命令嵌套过深，无法静态判定，需人工确认")
        cur = _strip_wrapping_quotes(part)
        toks = cur.split()
        if not toks:
            return None
        # 先查本层重定向（剥引号：引号里的 > 不是重定向；空目标除外）——
        # 不能放在包装器递归之后：`bash -c "ls" > file` 的重定向在本层。
        # ⚠️ 六轮改动：重定向的**判定权已移交 _destructive_write_scan**（按目标
        # 存在性：覆写已有→ask / 写新文件→放行 / 不可核实→fail-closed）。
        # 此处只在**沙箱外**做一次"不可核实即拦"的兜底——但为避免双重拦截，
        # 统一交给 _destructive_write_scan 处理，这里不再返回。
        head = cls._head_of(toks[0])
        raw0 = cls._raw_first_token(cur)
        if raw0.startswith("$"):
            return ("var", "命令以变量展开（$VAR / ${VAR}）为首词，实际执行内容不可静态判定，需人工确认")
        if head in ("eval", "source", "."):
            return (f"eval:{head}", f"{head} = 执行任意脚本内容，需人工确认")
        if head in _WRAPPER_HEADS:
            m = _WRAPPER_RE.match(cur)
            if m:
                inner = _strip_wrapping_quotes(m.group(1))
                if not inner.strip():
                    return (f"wrap:{head}", f"通过 {head} 执行空/不可见命令，需人工确认")
                if inner.startswith("-"):
                    # 第四轮复验：`bash -lc -o '…'` —— -c 的值又是一个开关，真实执行
                    # 内容与解析结果不一致，不可静态判定 → ask
                    return (f"wrap:{head}", f"{head} 内层又以开关开头（{inner.split()[0]}），执行内容无法静态判定，需人工确认")
                for sub in cls._split_top(inner):  # 透视内层：逐个子命令递归
                    hit = cls._part_suspicion(sub, depth + 1)
                    if hit:
                        return hit
                return None
            # 裸 bash / bash 从 stdin 读脚本 / 识别不了的开关组合 → 透视不了 → ask
            return (f"wrap:{head}", f"通过 {head} 间接执行（内层不可见），需人工确认")
        if head in _OPAQUE_WRAPPERS:
            # 第九轮复验⑦：裸 env（无内层命令）= 打印环境变量，只读放行
            if head == "env" and len(toks) == 1:
                return None
            return (f"wrap:{head}", f"通过 {head} 包装执行（提权/换环境，喂入内容不可知），需人工确认")
        if head in _TRANSPARENT_WRAPPERS:
            # 复审 P0 误伤修复 + 第四轮复验：透明包装剥本体与自身参数（含 -a/-n 的
            # 独立值）后递归判定内层——`timeout 60 python -m pytest` 放行、
            # `xargs -a list.txt shred` 里的 shred 命中
            inner = _strip_wrapper_prefix(toks)
            if not inner:
                return (f"wrap:{head}", f"通过 {head} 包装执行（内层不可见），需人工确认")
            return cls._part_suspicion(inner, depth + 1)
        return None

    @staticmethod
    def _inquote_exec(command: str) -> tuple[str, str] | None:
        """白名单/普通命令的**脚本串**里藏的执行/写语义（引号内也要看）：
        awk system()、awk print | "cmd"、awk > "file"、sed 的 e 修饰符。"""
        for qm in _QUOTED_RE.finditer(command):
            q = qm.group(0).lower()
            if "system(" in q:
                return ("inq-system", "脚本串内包含 system() 调用，需人工确认")
            # 只匹配"输出目标是一个 shell"：| "sh" / | "bash -c …"（-F'|' 这类字面量分隔符不误伤）
            if re.search(r"\|\s*[\"](sh|bash|cmd|powershell|pwsh|/bin/sh|/bin/bash)", q):
                return ("inq-pipe", "脚本串内包含向 shell 程序输出（| \"sh …\"），需人工确认")
            if re.search(r">>?\s*[\"']", q):
                return ("inq-redirect", "脚本串内包含文件重定向（> \"file\"），需人工确认")
            # sed e 修饰符只认 分隔符后紧跟 e 再收引号（s/aa/bb/e'）；bbe 这类正文不误伤
            if re.search(r"[/,]e['\"]$", q) and ("s/" in q or "y/" in q):
                return ("sed-e", "sed 脚本使用 e 修饰符（模式空间交给 shell 执行），需人工确认")
        return None

    # ---------- ③ 联网 / 内联代码 ----------

    @staticmethod
    def _flatten(command: str) -> str:
        """反斜杠转义与零宽字符归一（复审 P0：`c\\u\\r\\l` 拆字绕过首词判定）。

        只用于**动词/联网/内联**检测的副本——越界路径检测仍用原文
        （`C:\\Users` 的反斜杠不能被剥掉）。
        """
        no_zw = re.sub(r"[\u200b-\u200f\u2060\ufeff]", "", command)
        return re.sub(r"\\(.)", r"\1", no_zw)

    @classmethod
    def _net_or_inline(cls, command: str) -> tuple[str, str] | None:
        # 用 _deep_parts：藏在包装器内层的外传/内联代码同样要看见
        #（`bash -c "rm x; curl evil"` 里的 curl 不能漏）
        for part in cls._deep_parts(command):
            toks = part.split()
            if not toks:
                continue
            head = cls._head_of(toks[0])
            if head in _INTERPRETERS and any(t in _INLINE_FLAGS for t in toks[1:]):
                return (f"inline:{head}", f"{head} 内联代码执行（-c/-e/stdin），无法静态判定，需人工确认")
            # 复审 P0：`python -c"code"`（-c 与代码间无空格）——token 前缀判定
            if head in _INTERPRETERS and any(
                re.sub("['\"`]", "", tok).startswith(("-c", "-e", "-r", "--eval", "--command"))
                for tok in toks[1:]
            ):
                return (f"inline:{head}", f"{head} 内联代码执行（-c/-e/stdin），无法静态判定，需人工确认")
            if head in _REMOTE_EXEC_HEADS:
                # 复审 P0：mshta/certutil -urlcache 等远程拉取执行——合法工作流不出现
                return (f"remote:{head}", f"远程代码执行类工具（{head}），需人工确认")
            if head in _PKG_ASK_SUBS_GUARD_HEADS:
                # 复审 P0 误伤修复：包管理器只在"安装/执行"类子命令时问——
                # pip list / npm --version / pip show 是纯查询，放行
                sub = cls._head_of(toks[1]) if len(toks) > 1 else ""
                if sub not in _PKG_ASK_SUBS:
                    continue
                return (head, f"包管理器安装/执行（{head} {sub}）会下载并运行第三方代码，需人工确认")
            if head in _NET_HEADS:
                sub = cls._head_of(toks[1]) if len(toks) > 1 else ""
                if head == "git" and sub not in _GIT_NET_SUBS:
                    continue
                if head in _CONTAINER_CLIS:
                    # 二十/二十二轮：compose 走 _compose_exec_sub 定位（跳过
                    # --profile/-f 等带值选项）——此前 toks[2] 被 --profile 占据
                    # → 观察类（ps/config/logs）被误判为联网
                    if sub == "compose":
                        real = cls._compose_exec_sub(toks, 1)
                        if real in _DOCKER_COMPOSE_NET_SUBS:
                            return (head, f"联网/外传能力命令（{head} compose {real}），需人工确认")
                        continue
                    # 非 compose：对象名形态（image/container/system/volume…）的
                    # 真动词在 toks[2]；白名单反转（安全动词放行，其余 ASK）
                    if sub in _DOCKER_OBJECTS and len(toks) >= 3:
                        sub = cls._head_of(toks[2])
                    if sub in _DOCKER_NET_SUBS:
                        pass  # 落到下方 return（联网 ASK）
                    elif sub in _DOCKER_SAFE_VERBS or sub in _DOCKER_DESTRUCTIVE_SUBS:
                        # 安全观察类放行；破坏集由 _container_lifecycle 处理（struct 键）
                        if sub in _DOCKER_SAFE_VERBS:
                            continue
                        return (head, f"容器 {head} {sub} 为破坏/改动类操作，需人工确认")
                    else:
                        # 未知动词 → 白名单反转默认 ASK（struct 级由 lifecycle 面处理）
                        continue
                    # 落到这里 = 联网子命令（pull/build/create）→ ASK
                if head in ("curl", "wget") and cls._all_localhost(toks):
                    continue  # 回环地址：数据不出机器，不算外传
                return (head, f"联网/外传能力命令（{head}），需人工确认")
        return None

    @classmethod
    def _container_lifecycle(cls, command: str) -> tuple[str, str] | None:
        """容器 CLI 判定——二十轮 🔴0 改【动词白名单反转（fail-closed）】。

        结构性修法（审查方案 b）：
        ① 先剥全局选项（--context/-H/--debug…及其值）——此前 `docker --context
           prod system prune -a` 因对象名落到 toks[2] 整族放行；
        ② 动词判定改为【白名单】——只有已知观察类动词（ps/images/ls/inspect/
           version/logs/info/…）放行，其余（run/exec/pull/build/prune/rm/
           stack deploy/swarm leave/container cp/未知新子命令）一律 ASK；
        ③ 连字符旧入口 docker-compose 等纳入 _CONTAINER_CLIS；
        ④ compose 子命令仍走专门定位（含全局选项）。
        为什么从根上解决：不再枚举危险子命令追新——未知动词默认被拦。
        """
        for part in cls._deep_parts(command):
            toks = part.split()
            if not toks:
                continue
            # 二十一轮 🔴3：剥环境变量前缀（DOCKER_HOST=… docker …）——
            # 此前首词被 VAR=值 占据 → 整族绕过（与全局选项同族）
            # 二十三轮 🔴1：带引号的变量名整体（"FOO=x"）也先剥
            while toks and re.fullmatch(r'"[A-Za-z_][A-Za-z0-9_]*=[^"]*"', toks[0]):
                toks = toks[1:]
            # 二十一轮 P0③：边界补全（VAR= 空值 / VAR='a b' 引号含空格）
            # 二十六轮第 7 批：原先这里的局部闭包已提升为模块级 _strip_var_prefix
            # （单一实现）——其它按 head 判定的面此前拿不到这套判据，见
            # _strip_assign_prefix_str 的注释。
            toks = _strip_var_prefix(toks)
            if not toks:
                continue
            head = cls._head_of(toks[0])
            # 二十一轮 P0② + 二十四轮 🔴P0（结构性重构）：
            # VAR 剥完后可能露出【包装器链】（VAR=x su -c "docker …" /
            # DOCKER_HOST=x timeout 5 docker …）。改用统一表 _WRAPPER_DEF：
            #   · opaque 类（su/chroot/nsenter/systemd-run/setpriv/script/
            #     strace/sudo/env…）→ **fail-closed 直接 ASK**（喂入不可知）；
            #   · 透明类（timeout/nice/taskset/setsid/…）→ 跳本体与自身参数
            #     （timeout 5 / nice -n 10 / taskset 0x1 / setsid -f / flock 文件），
            #     循环剥到露出容器 CLI 或非包装词。
            # 为什么从根上解决：不再枚举"危险包装器"——凡不在统一表内的
            # 非 CLI 词天然不会走到容器判定（表外词在 verb 阶段被 ASK）。
            while toks and (wkind := _WRAPPER_DEF.get(cls._head_of(toks[0]))):
                kind, _params = wkind
                # 二十二轮：opaque 命中时【先看这条链剥完是否真是容器 CLI】——
                # 裸 `env` / `env | grep` 的 head 是 env 但 env ∉ 容器 CLI，
                # 应交给其它判定面（裸 env 只读放行；容器链才 ASK）
                probe = toks[1:]
                while probe and (
                    re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=[^\s]*", probe[0])
                    or (probe[0].startswith("-") and "=" not in probe[0])
                ):
                    probe = probe[1:]
                reaches_container = bool(probe) and cls._head_of(probe[0]) in _CONTAINER_CLIS
                if kind == "opaque":
                    if not reaches_container:
                        break  # 不是容器链 → 退出包装器循环，交给其它判定面
                    return (f"wrap:{cls._head_of(toks[0])}",
                            f"通过 {cls._head_of(toks[0])} 包装容器命令（提权/换环境，喂入内容不可知），需人工确认")
                # 透明类：跳本体；argjump 跳自身参数（选项/数值/短引号残片），
                # 且允许跳【1 个任意位置 token】（flock <文件> / taskset <掩码>）——
                # 之后必须能看到容器 CLI，否则视为非容器命令退出（fail-closed 交给其它面）
                toks = toks[1:]
                if kind == "argjump":
                    while toks and (toks[0].startswith("-") or re.fullmatch(r"[\d.]+[a-zA-Z]?", toks[0])
                                    or (len(toks[0]) <= 4 and ("'" in toks[0] or '"' in toks[0]))):
                        toks = toks[1:]
                    if toks and not (cls._head_of(toks[0]) in _CONTAINER_CLIS
                                     or cls._head_of(toks[0]) in _WRAPPER_DEF):
                        toks = toks[1:]  # 跳 1 个位置参数（flock 文件/taskset 掩码）
                toks = _strip_var_prefix(toks)
            if not toks:
                continue
            head = cls._head_of(toks[0])
            if head not in _CONTAINER_CLIS:
                # 二十五轮 🔴2：fail-closed 落到【格式识别入口】——剥完包装器
                # 取到 head 的这一刻就判。表外 head（不在 _WRAPPER_DEF、不在
                # _CONTAINER_CLIS）分两路：
                #   · 属于"完全无关普通命令"白名单 → 交给其它判定面（grep
                #     docker build.log 这类文本操作不该被容器面拦）；
                #   · 否则只要后面跟着容器 CLI，它就是一个【未知包装器/执行器】
                #     （喂入与执行方式不可知）→ 一律 ASK。
                # 此前无条件 continue → zzznotreal docker system prune -a 等
                # 表外包装器 16/16 零审批放行（fail-open，二十五轮实测）。
                if head in _BENIGN_NONCONTAINER_HEADS:
                    continue
                if any(cls._head_of(t) in _CONTAINER_CLIS for t in toks[1:]):
                    return (f"wrap-unknown:{head}",
                            f"未知命令「{head}」包装容器命令（执行/喂入方式不可知），需人工确认")
                continue
            # 二十二轮：compose 专门定位须在白名单反转【之前】且泛化到任意位置
            # （`docker --context x compose …`；二十轮删旧分支时 compose 路径丢失，
            #   `docker compose --profile dev ps` 会把 "--profile" 当动词误 ASK）
            _ci = next((k for k in range(1, len(toks)) if cls._head_of(toks[k]) == "compose"), -1)
            if _ci != -1 and head != "docker-compose":
                # 二十六轮第2批第3处：compose 的 --help/-h 豁免与主路径同构——
                # 【--help 之后无任何位置 token】才豁免。此前 `docker compose
                # --help up` 被容器面豁免后掉进 net 面：host/sandbox ASK
                # net:docker、sandbox+nonet PASS——三模式不一致（独立实测）。
                # 真机实测 compose --help <子命令> 打印主题帮助不执行，但按
                # "flag 后带子命令不豁免"的统一口径三模式恒 ASK（宁可多问）。
                _hi = next((k for k, t in enumerate(toks)
                            if t.strip(_QUOTES) in ("--help", "-h")), -1)
                if _hi != -1 and not any(
                        not t.strip(_QUOTES).startswith("-") for t in toks[_hi + 1:]):
                    continue
                if "--dry-run" in toks:
                    continue  # 观察类 dry-run 修饰（二十四轮起，不真执行）
                # compose 的 -v/--version 与 docker 全局 -v 同族——真机实测：
                # `docker compose -v ps` → "no configuration file provided"
                # = ps【真执行】（version flag 不阻断）；裸 `docker compose -v`
                # 只打印帮助。拍板（二十五轮 c）：flag 后带子命令一律 ASK。
                # 判定：version flag 之后若还有任何【位置 token】→ ASK；
                # 不能用 _compose_exec_sub 定位（它把 -v 当带值选项双跳，
                # 会吞掉后面的子命令导致漏判）。
                _vi = next((k for k, t in enumerate(toks)
                            if t.strip(_QUOTES) in ("--version", "-v")), -1)
                if _vi != -1:
                    _rest = toks[_vi + 1:]
                    if any(not t.strip(_QUOTES).startswith("-") for t in _rest):
                        return ("compose-vflag-sub",
                                "容器 compose 版本 flag 之后仍带子命令（真机实测 -v/--version 不阻断执行），需人工确认")
                    continue  # version flag 后全是选项/无 token：裸自述（实测仅打印帮助）
                # 二十四轮：`compose alpha dry-run`（子命令里的 dry-run 修饰）
                if any(tk == "dry-run" for tk in toks[_ci:]):
                    continue
                real = cls._compose_exec_sub(toks, _ci)
                if real in _DOCKER_COMPOSE_NET_SUBS:
                    return (f"compose-{real}", f"docker compose {real} 会拉镜像/执行服务代码或删除资源（与断网无关），需人工确认")
                if real in _DOCKER_SAFE_VERBS:
                    continue
                return (f"compose-{real or '未知'}", f"docker compose {real or '(未知子命令)'} 默认需人工确认（白名单外，与断网无关）")
            # 二十五轮 🔴1：帮助/版本豁免收窄为【纯自述】——选项区出现
            # help/version flag 且其后【再无任何位置 token（子命令）】。
            # 真机实测（docker 29.8.0，2026-10-03，仅用安全观察子命令）：
            #   docker -v ps / docker --version ps / docker -v system df
            #     → daemon 连接错误 = 子命令【真执行】（-v/--version 不阻断）；
            #   docker --help ps / docker -h ps → 打印该主题帮助、不执行；
            #   docker -v / docker --debug -h / docker --context default -v
            #     / docker -v --help → 纯自述。
            # 拍板（二十五轮 c）：flag 之后只要还有子命令一律 ASK——不再假设
            # "帮助不执行"。i 同时是动词定位（停在第一个位置 token 上）。
            i = 1
            help_seen = False
            sub_after_flag = False
            while i < len(toks):
                tk = toks[i]
                if not tk.startswith("-"):
                    sub_after_flag = True
                    break
                if tk in ("--help", "-h", "--version", "-v"):
                    help_seen = True
                    i += 1
                    continue
                if "=" in tk:
                    i += 1  # --opt=value 自带值
                elif tk in _DOCKER_GLOBAL_FLAGS:
                    i += 1  # 无值全局选项（--debug/-D/--tlsverify，真机实测单跳）
                elif tk in _DOCKER_GLOBAL_OPTS or (tk.startswith("-") and not tk.startswith("--") and len(tk) == 2):
                    i += 2  # 带值选项
                else:
                    i += 1  # 布尔选项（--debug 等）
                continue
            if help_seen:
                if sub_after_flag:
                    return (f"{head}-flag-prefix",
                            f"容器 {head} 帮助/版本 flag 之后仍带子命令（真机实测 -v/--version 不阻断执行），需人工确认")
                continue  # 纯版本/帮助自述（flag 后无子命令，实测不执行资源操作）
            # 二十六轮第2批第3处：【选项-only = 纯自述】统一口径——
            # head 之后只有选项、无任何位置 token（子命令）时，真机实测
            # （docker 29.8.0）：`docker -v` / `-D` / `--tlsverify` /
            # `--context x` / `--log-level ps`（值非法）全部打印 Usage 或报错
            # 退出，无资源操作。此前裸 -D/--tlsverify ASK 而裸 -v PASS
            # （同族不同判）；资源操作必须经子命令，选项-only 结构性不执行。
            if i >= len(toks):
                continue
            verb = cls._head_of(toks[i])
            # 白名单反转：已知观察动词 → 放行；其余（含未知新词）→ ASK
            # 对象形态：verb ∈ _DOCKER_OBJECTS 时必须看子动词（pull/ls/…）——
            # "ctr images pull" 的 images 是对象名不是动词，不能因 images 在
            # 安全表就放行（十九轮 🔴1 的教训：对象名≠动词）
            if verb in _DOCKER_OBJECTS and i + 1 < len(toks):
                sub2 = cls._head_of(toks[i + 1])
                if sub2 in _DOCKER_SAFE_VERBS:
                    continue
                # 三级形态（trust signer remove / compose alpha dry-run）：
                # 子对象 ∈ _DOCKER_OBJECTS 时再看第三段动词
                if sub2 in _DOCKER_OBJECTS and i + 2 < len(toks):
                    sub3 = cls._head_of(toks[i + 2])
                    if sub3 in _DOCKER_SAFE_VERBS:
                        continue
                # --dry-run 修饰：不真执行 → 放行
                if "--dry-run" in toks:
                    continue
                # 子动词不在安全表（如 pull）→ ASK
                disp = " ".join(toks[1:i + 2])[:40]
                return (f"{head}-{verb}-{sub2}", f"容器 {head} {verb} {sub2} 默认需人工确认（子动词非观察类，与断网无关）")
            if verb in _DOCKER_SAFE_VERBS:
                continue
            disp = " ".join(toks[1:i + 1])[:40]
            if verb in _DOCKER_DESTRUCTIVE_SUBS:
                return (f"{head}-{verb}", f"容器 {head} {verb} 会删除/改动镜像容器卷等资源（{disp}，与断网无关），需人工确认")
            if verb in _DOCKER_NET_SUBS:
                return (f"{head}-{verb}", f"容器 {head} {verb} 会拉镜像或执行任意代码（{disp}，与断网无关），需人工确认")
            return (f"{head}-{verb}-unknown", f"容器 {head} 的子命令「{verb}」不在观察白名单内（{disp}），默认需人工确认")
        return None

    @classmethod
    def _git_config_env_injection(cls, command: str) -> tuple[str, str] | None:
        """git 的【配置注入环境变量】面（施工单风险点②，二十六轮第 7 批补）。

        现状证据（独立验证员在 0d4bee4 上实测 + 本班复现）：
            git -c core.pager='…' log                                   → ASK
            GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.pager \\
              GIT_CONFIG_VALUE_0='!echo PWNED' git log                  → PASS ★绕过
        这三条环境变量是 git 官方的"从环境变量写配置"通道，与 `-c` / `--config-env`
        同一能力面：键写 core.pager / alias.* / credential.helper 就执行外部命令。
        真机实测（验证员）：`GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=alias.pwn
        GIT_CONFIG_VALUE_0='!echo PWNED' git pwn` **真的执行了 shell**。

        为什么以前拦不到、也只能这样拦：payload（键与值）**根本不出现在命令行参数里**，
        所以参数面（_GIT_EXEC_FLAGS）永远看不见它；唯一的线索是命令开头那串赋值前缀。
        而前缀又被 `_deep_parts` 统一剥掉了（那是本次修的另一处），
        所以本面必须直接看【剥之前的原串】。

        只对 head 是 git 的情况拦：`GIT_CONFIG_COUNT=1 ./myscript.sh` 是把这个变量
        喂给别的程序，不是 git 注入，拦它属误伤；`env GIT_CONFIG_… git x` 由
        包装器面负责（已 ASK）。宁严与误伤的边界写在这里，供后续复核。
        """
        for part in cls._split_top(command):
            names = _leading_assign_names(part)
            if not any(_GIT_CONFIG_ENV_NAME_RE.match(nm) for nm in names):
                continue
            body = _strip_assign_prefix_str(part).split()
            if body and cls._head_of(body[0]) == "git":
                return ("env:git-config-inject",
                        "git 的配置注入环境变量（GIT_CONFIG_COUNT / GIT_CONFIG_KEY_<n> / "
                        "GIT_CONFIG_VALUE_<n>）能改写 git 配置（core.pager、alias.* 等"
                        "会让后续 git 操作执行外部命令），需人工确认")
        return None

    @classmethod
    def _net_or_inline_remote_only(cls, command: str) -> tuple[str, str] | None:
        """只查远程代码执行类工具（struct: 键——不允许"总是允许"记忆）。"""
        for part in cls._deep_parts(command):
            toks = part.split()
            if not toks:
                continue
            head = cls._head_of(toks[0])
            if head in _REMOTE_EXEC_HEADS:
                return (f"remote:{head}", f"远程代码执行类工具（{head}），需人工确认")
            # 二十六轮第3处：git/pip 间接执行参数；二十六轮第2批：git -c 只认
            # 【git 级】（head 与子命令之间）——子命令级 -c（git commit -c HEAD
            # = reuse message）不是 config 注入，误拦已实测并修正。
            if head == "git":
                _sub_i = next((k for k, t in enumerate(toks[1:], 1)
                               if not t.strip(_QUOTES).startswith("-")), None)
                _git_scope = toks[1:_sub_i] if _sub_i is not None else toks[1:]
                for t in _git_scope:
                    tt = t.strip(_QUOTES)
                    if tt in _GIT_EXEC_FLAGS or any(
                            tt.startswith(f + "=") for f in _GIT_EXEC_FLAGS if f.startswith("--")):
                        return ("indirect:git",
                                f"git 的间接执行参数（{tt.split('=')[0]}，可注入 pager/ssh helper/filter 配置"
                                "或改用外部 helper / 属性来源），需人工确认")
                # git config <key> <value>：把恶意 pager/ssh helper/filter 写进
                # .gitconfig（两步攻击第一步——之后任何 git log 都执行它）
                if _sub_i is not None and cls._head_of(toks[_sub_i]) == "config" and _sub_i + 1 < len(toks):
                    # 二十六轮第 5 批第 2 处：先跳过 config 自身的选项再取 key——
                    # 此前 `git config --global core.pager '…'` 把 --global 当 key
                    # 比对不命中 → 整族放行（最常见的真实写法被一行参数抹掉）。
                    # 二十六轮第 6 批第 2 处：选项表重写——此前只双跳
                    # --file/-f/--blob，--type path / --comment m 的【值】被当
                    # key 比对不命中 → 整族放行（真机 git 2.53 实测写盘成功）。
                    # 改为：带值选项表完整双跳 + 收集非选项 token + 读模式识别。
                    # 二十六轮第 7 批第 8-b 处：与真机 git 2.53.0.windows.3 逐条实测对照。
                    # ★ 原施工单把 --value/--url/--default 当"幻影项"要删——**半错，已纠正**：
                    #   · 三者都是【子命令作用域】的真选项（2.45+ 重写后只在子命令之后合法）：
                    #       git config get [--value=<pat>] [--default=<val>] [--url=<url>] <name>
                    #       git config set [--value=<pat>] <name> <value>
                    #     裸 `git config --value` 报 unknown option 是【位置】不对，不是选项不存在。
                    #   · --value ★【安全承重】：真机实测（临时仓库 + 沙箱 GIT_CONFIG_GLOBAL）：
                    #       git config set --value x core.pager '!cmd'  → rc=0，core.pager 真被写成 !cmd
                    #     本表若删掉它 → 值 x 被收进 non-opts 当了 key ⇒ 整条放行（真绕过）。
                    #     故留在表内，并由 _GITCFG_6B_ASKS 的 `set --value x core.pager` 钉住。
                    #   · --default / --url：只对 get 合法，删掉【不会】产生写绕过——真机实测：
                    #       git config set --default x core.pager '!cmd' → error: unknown option `default'
                    #       git config --default x core.pager '!cmd'     → error: --default is only applicable to --get
                    #     留着只为"带值选项一律双跳"这条口径自洽（宁严），非安全承重项；
                    #     行为由 _GITCFG_6B_PASSES 的 `get --default core.pager` 钉住。
                    # 裸 list/get：本表只在 token 以 "-" 开头时才查（见下面 _ctk.startswith("-")），
                    #   而 `git config list`（真机 rc=0）/ `git config get <name>` 是【非选项 token】，
                    #   走 fall-through 放行 ⇒ 原表里裸 "list"/"get" 两项【在此分支永不可达】，已删；
                    #   仍放行由 _GITCFG_6B_PASSES 的 `git config list` 钉住。
                    _CFG_VALUE_OPTS = {"--file", "-f", "--blob", "--type", "-t",
                                       "--comment", "--default", "--value", "--url"}
                    _CFG_READ_HEADS = {"--get", "--get-all",
                                       "--get-regexp", "--get-urlmatch",
                                       "--get-color", "--get-colorbool", "--list", "-l"}
                    _cfg_rest = toks[_sub_i + 1:]
                    _cfg_nonopts: list[str] = []
                    _cfg_saw_read = False
                    _cfg_saw_edit = False
                    _ci2 = 0
                    while _ci2 < len(_cfg_rest):
                        _ctk = _cfg_rest[_ci2].strip(_QUOTES)
                        if _ctk.startswith("-"):
                            if _ctk in _CFG_READ_HEADS:
                                _cfg_saw_read = True
                            if _ctk in ("--edit", "-e"):
                                _cfg_saw_edit = True
                            _ci2 += 2 if _ctk in _CFG_VALUE_OPTS else 1
                        else:
                            _cfg_nonopts.append(_ctk)
                            _ci2 += 1
                    if _cfg_saw_read:
                        continue  # 只读（--get/--list/get/list…）→ 不写配置
                    _cfg_key = ""
                    if _cfg_nonopts:
                        _first = _cfg_nonopts[0]
                        if _first == "edit":
                            return ("indirect:git-config",
                                    "git config edit 会拉起 core.editor（可执行外部命令），需人工确认")
                        if _first in ("unset", "remove-section", "rename-section"):
                            continue  # 删配置/改节名：注入不了可执行键
                        if _first == "set" and len(_cfg_nonopts) >= 3:
                            _cfg_key = _cfg_nonopts[1]  # 现代 set <name> <value>
                        elif _first != "set" and len(_cfg_nonopts) >= 2:
                            _cfg_key = _first  # 传统 <name> <value> 写法
                        # 其余（set 缺值 / 单 token = 传统读法）→ 只读/无效，放行
                    elif _cfg_saw_edit:
                        return ("indirect:git-config",
                                "git config --edit 会拉起 core.editor（可执行外部命令），需人工确认")
                    key = _cfg_key.lower()  # 二十六轮第6批：重写时曾丢 .lower()——大小写混排键（core.sshCommand）整族漏拦
                    if (key in _GIT_CONFIG_EXEC_KEYS
                            or key.startswith(("alias.", "pager."))
                            or key.endswith((".clean", ".smudge", ".process", ".textconv",
                                             ".helper", ".uploadpack", ".receivepack", ".command"))
                            or key.startswith(("credential.", "includeif.", "protocol."))
                            or key.endswith((".program", ".allow"))
                            or key.endswith(".cmd")):  # 二十六轮第3批：difftool.<x>.cmd
                        return ("indirect:git-config",
                                f"git config 写入可执行配置键（{key}），会让后续 git 操作执行外部命令，需人工确认")
            elif head == "tar":
                for t in toks[1:]:
                    tt = t.strip(_QUOTES)
                    if any(tt == f or tt.startswith(f + "=") for f in _TAR_EXEC_FLAGS):
                        return ("indirect:tar",
                                f"tar 的执行参数（{tt.split('=')[0]}，其值作为外部命令运行归档内容），需人工确认")
            elif head == "rsync":
                for t in toks[1:]:
                    tt = t.strip(_QUOTES)
                    if any(tt == f or tt.startswith(f + "=") for f in _RSYNC_EXEC_FLAGS):
                        return ("indirect:rsync",
                                f"rsync 的远程 shell 参数（{tt.split('=')[0]}，其值作为外部命令运行），需人工确认")
            else:
                exec_flags = _PIP_EXEC_FLAGS if head in ("pip", "pip3") else None
                if exec_flags:
                    for t in toks[1:]:
                        tt = t.strip(_QUOTES)
                        if tt in exec_flags or any(
                                tt.startswith(f + "=") for f in exec_flags if f.startswith("--")):
                            return (f"indirect:{head}",
                                    f"{head} 的间接执行参数（{tt}，可注入 pager/ssh helper/filter 等外部命令），需人工确认")
        return None

    @classmethod
    def _opaque_path_write(cls, command: str) -> tuple[str, str] | None:
        """复审 P0（24 号报告扩充）：写/破坏操作的"路径或目标不可判定"兜底。

        · 写动词 + $VAR / 相对上跳路径 → 必问（`dd of=$HOME/x`、`cd .. && dd …`）
        · find -exec/-execdir/-ok/-okdir：其后**每个**子命令的可执行名必须落在
          只读白名单，否则必问（`-exec shred`/-exec busybox rm/-exec cp 全在此拦——
          实测沙箱 rw 挂载下同样删得掉 /w 交付物）；`-exec ls`/`-exec grep` 放行
        · find -delete / sed -i：等效删除/就地改写，必问
        · git clean / git reset --hard：破坏性子命令，必问
        · cd 到 $VAR/~：后续命令工作目录不可判定，必问
        纯工作区内的普通 mv/cp 仍按 auto_edit 语义放行。
        """
        for part in cls._deep_parts(command):
            toks = part.split()
            if not toks:
                continue
            head = cls._head_of(toks[0])
            is_write = head in _WRITE_FALLBACK_HEADS
            if head == "find" and re.search(r"\s--?delete\b", part):
                return ("find-delete", "find -delete 等效删除操作，需人工确认")
            if head == "sed" and re.search(r"\s-i\b|--in-place", part):
                return ("sed-i", "sed -i 就地改写文件，需人工确认")
            if head == "find" and re.search(r"\s-(?:exec|execdir|ok|okdir)\b", part):
                # 复审 P0：-exec 后整段重新判定——只认白名单动词
                for seg in re.split(r"\s-(?:exec|execdir|ok|okdir)\b", part)[1:]:
                    seg_toks = seg.replace("{}", " ").replace("+", " ").replace(";", " ").split()
                    if not seg_toks:
                        continue
                    exec_head = cls._head_of(seg_toks[0])
                    if exec_head not in _READONLY_HEADS:
                        return (f"find-exec:{exec_head}",
                                f"find -exec 调用非只读程序（{exec_head}），需人工确认")
            if head == "git" and len(toks) > 1:
                sub = cls._head_of(toks[1])
                if sub == "clean":
                    return ("git-clean", "git clean 会删除未跟踪文件，需人工确认")
                if sub == "reset" and "--hard" in part:
                    return ("git-reset-hard", "git reset --hard 会丢弃全部未提交改动，需人工确认")
                # 第五轮复验：`git -c alias.wipe=!rm … wipe` —— 别名值以 ! 开头
                # 即执行 shell，整条命令是"换入口"
                if re.search(r"\balias\.[\w.-]+=!", part):
                    return ("git-alias-exec", "git 别名注入（-c alias.x=!cmd）会在 git 内执行 shell 命令，需人工确认")
            if head in _DEBUGGER_HEADS and re.search(r"\s-(?:ex|ie|x)\s", part):
                return ("debugger-exec", f"调试器（{head}）的 -ex/-x 参数可执行 shell 命令，需人工确认")
            if re.search(r"\$\{ifs\}", part, re.I):
                return ("ifs-split", "使用 ${IFS} 拆分命令词（规避静态判定），需人工确认")
            if is_write and (_VAR_TOKEN_RE.search(part) or _DOTDOT_RE.search(part)):
                return (f"opaque:{head}", f"写操作（{head}）目标含变量/上跳路径，无法静态判定，需人工确认")
            if head in _EDITOR_EXEC_HEADS and any(
                t2.strip(_QUOTES).startswith(("-c", "-S", "-s", "--cmd", "+")) or ":!" in t2
                for t2 in toks[1:]
            ):
                # 第四轮复验：vim -c ':!rm x' / vim -S evil.vim —— 参数即 shell 代码
                return ("editor-exec", f"编辑器（{head}）的 -c/-S/+ 参数可在其内执行 shell 命令，需人工确认")
            if head == "cd" and len(toks) > 1:
                arg = toks[1].lstrip("(").strip("\"'")
                if arg.startswith("$") or arg.startswith("~"):
                    return ("cd-var", "cd 目标为变量/家目录展开，后续命令的实际工作目录不可判定，需人工确认")
                if arg.startswith(".."):
                    return ("cd-up", "cd 上跳目录（cd ..），可能越出工作区，需人工确认")
        return None

    @staticmethod
    def _split_target_dir(head: str, toks: list[str]) -> tuple[str, list[str]] | None:
        """`cp/install/mv -t DIR` / `--target-directory[=]DIR` 形态解析。

        第九轮复验②b：此前 cp/install 取 cands[-1] 当目标位——`cp -t dir a.txt`
        取到的是**源文件** a.txt（若存在就误拦，真正落点 dir/a.txt 反而没判）。
        返回 (目录, 可见源列表)；无 -t 返回 None（走常规目标位）。
        """
        d: str | None = None
        srcs: list[str] = []
        i = 1
        while i < len(toks):
            tk = toks[i].strip(_QUOTES)
            if tk == "--":
                srcs.extend(t.strip(_QUOTES) for t in toks[i + 1:])
                break
            if tk in ("-t", "--target-directory"):
                if i + 1 < len(toks):
                    d = toks[i + 1].strip(_QUOTES)
                    i += 2
                    continue
            elif tk.startswith("--target-directory="):
                d = tk.split("=", 1)[1]
            elif tk.startswith("-t") and len(tk) > 2:
                d = tk[2:]  # 连写形态 -tdir
            elif not tk.startswith("-"):
                srcs.append(tk)
            i += 1
        if d is None:
            return None
        return d, srcs

    @staticmethod
    def _join_basename(d: str, src: str) -> str:
        base = re.split(r"[/\\]", src.rstrip("/\\"))[-1]
        return d.rstrip("/\\") + "/" + base

    # 目标目录形态适用的头（-t/--target-directory 与 `SRC… DIR/` 尾斜杠两种写法）
    _DIR_TARGET_HEADS: frozenset[str] = frozenset({
        "cp", "copy", "xcopy", "robocopy", "mv", "move", "install", "ln", "rename",
    })

    @classmethod
    def _write_landing_targets(cls, head: str, toks: list[str]) -> tuple[list[str], bool]:
        """破坏性写命令的**落点**统一解析——唯一实现。

        第十二轮 🔴5③：_extract_write_target 与 _destructive_write_scan 此前各写一份
        目标位逻辑（cp -t 只在一处生效、ln 不认 -t、`cp a.txt dir/` 把目录当覆写
        目标），现在两处都调这里。返回 (landings, undeterminable)：
          landings        显式落点（DIR/basename(源) 或目标位 token）
          undeterminable  True = 落点由管道/上游喂入（xargs 家族）→ 调用方 fail-closed
        """
        if head == "dd":
            m = re.search(r"\bof=(\S+)", " ".join(toks))
            return ([m.group(1).strip(_QUOTES)] if m else []), not m
        cands = [t2.strip(_QUOTES) for t2 in toks[1:] if not t2.startswith("-")]
        cands = [c for c in cands if c and not re.fullmatch(r"[\d.]+[kmgt]?b?", c, re.I)]
        if head in ("tee", "truncate"):
            # `< src` 是输入源不是写目标（切掉首个 `<` 及其后全部）
            cut = next((i for i, t2 in enumerate(cands) if t2 == "<"), None)
            if cut is not None:
                cands = cands[:cut]
            cands = [c for c in cands if not c.startswith("<")]
            return cands, not cands
        if head == "mklink":
            lc = [c for c in cands if c.lower() not in ("/d", "/h", "/j", "/?")]
            return ([lc[0]] if lc else []), not lc
        if head == "install" and any(t2.strip(_QUOTES) in ("-d", "--directory") for t2 in toks[1:]):
            return [], False  # install -d 只建目录，不覆写任何文件 → 放行
        if head in cls._DIR_TARGET_HEADS:
            tdir = cls._split_target_dir(head, toks)
            if tdir is not None:
                d, srcs = tdir
                if not srcs:
                    return [], True  # 源由管道喂入（xargs cp -t dir）→ fail-closed
                return [cls._join_basename(d, s) for s in srcs], False
            if cands and re.search(r"[/\\]$", cands[-1]):
                # `cp/mv/ln … SRC… DIR/`：末位是目标目录，落点 = DIR/basename(源)
                d, srcs = cands[-1], cands[:-1]
                if not srcs:
                    return [], True
                return [cls._join_basename(d, s) for s in srcs], False
        if head == "ln":
            # 无 -t：链接名在末位（ln [-s] TARGET LINK_NAME）——源永不被写
            return ([cands[-1]] if cands else []), not cands
        # 常规目标位（最后一个非选项 token）
        return ([cands[-1]] if cands else []), not cands

    @classmethod
    def _extract_write_target(cls, head: str, part: str) -> str | None:
        """破坏性写命令的**显式目标**提取（无目标返回 None——调用方 fail-closed）。

        第六轮复验 A2：`… | xargs truncate -s 0`（目标由管道喂入）→ 无落点 →
        None → 调用方必须 ask，不能放行。
        第十二轮 🔴5③：实现收敛到 _write_landing_targets（与 ② 写动词面同一份，
        终结"两处实现不一致"——ln -t / cp 尾斜杠目录此前只在一处生效）。
        """
        landings, undet = cls._write_landing_targets(head, part.split())
        if undet:
            return None
        return landings[0] if landings else None

    @classmethod
    def _destructive_write_scan(
        cls,
        command: str,
        target_exists: "Callable[[str], bool | None] | None",
        is_inside: "Callable[[str], bool] | None" = None,
        in_sandbox: bool = False,
    ) -> tuple[str, str] | None:
        """能力面（六轮定型）：**覆写/清空已有文件**必问——不再依赖动词表穷举。

        判定分两段：
        ① 重定向（> / >> / >| / >>|）：目标存在 → ask；目标不可核实（越界/变量/
           目录/无法解析）→ ask（fail-closed）；目标不存在 → 放行（写新报告/日志
           是标准写法，此前被旧 redirect 面误伤 6 条）。
        ② 写动词目标：dd 取 of=；tee/truncate 取**全部**目标（多目标逐个判，
           修"tee 只判最后一个"的漏）；cp/mv/install 等取目标位。
           目标存在 → ask；None（不可核实）→ ask——**所有写动词同规则**，
           cp/mv 不再 fail-open（原 _STRICT 白名单导致沙箱 /w 形态静默放行）。
        """
        # ① 重定向面（引号感知扫描器：`> "my file"` 的目标也能看见；支持 >| >>|）
        for chunk in cls._deep_parts(command):
            for raw_tgt in _iter_redir_targets(chunk):
                tgt = raw_tgt.strip(_QUOTES)
                if tgt.lower() in _NULL_TARGETS or _FD_DUP_RE.fullmatch(raw_tgt):
                    continue
                state = cls._target_state(tgt, target_exists)
                if state is True:
                    return ("redirect-overwrite", f"输出重定向将覆写已有文件「{tgt}」，需人工确认")
                if state is None:
                    # 目标不可核实 → fail-closed（亦覆盖无回调的旧语义：重定向到
                    # 真实文件此前一律 ask）
                    return ("redirect-opaque", f"输出重定向目标「{tgt}」无法核实（fail-closed），需人工确认")
                # state is False：写新文件 → 放行（第六轮：消除"写报告/日志"6 条误伤）
        # ② 写动词面（需要存在性回调才能判定；无回调 → 跳过，保持纯审批单测的旧语义。
        #    生产链路由 loop 恒传 _target_exists，安全属性不受影响）
        if target_exists is None:
            return None
        for part in cls._deep_parts(command):
            toks = part.split()
            if not toks:
                continue
            head = cls._head_of(toks[0])
            # ②a 就地改写（第六轮复验②：`perl -i -pe 's/…/' file` 此前全放行）
            if head in _INPLACE_FLAG_HEADS:
                iflag = any(
                    t2.strip(_QUOTES) == "-i"
                    or (t2.strip(_QUOTES).startswith("-i") and not t2.strip(_QUOTES).startswith("--"))
                    for t2 in toks[1:]
                )
                if iflag:
                    cands = [t2.strip(_QUOTES) for t2 in toks[1:] if not t2.startswith("-")]
                    for tgt in ([cands[-1]] if cands else []):
                        st = cls._target_state(tgt, target_exists)
                        if st is None or st is True:
                            return ("inplace-write", f"就地改写文件（{head} -i）「{tgt}」，需人工确认")
            if head not in _DESTRUCTIVE_OVERWRITE_HEADS:
                continue
            # 第十二轮 🔴5③：落点解析走唯一实现（-t / 尾斜杠目录 / 名字段位全在此），
            # 不再各写一份。undeterminable（无可见落点）在此不拦：xargs 喂入形态
            # 由 ②b 阶段（带 xargs 门）fail-closed；裸 `tee`（stdout）等无害形态不误伤。
            targets, _undet = cls._write_landing_targets(head, toks)
            for tgt in targets:
                if tgt in ("/dev/null", "nul", "/dev/zero", "/dev/stdin"):
                    continue
                state = cls._target_state(tgt, target_exists)
                if state is True:
                    return (f"overwrite:{head}", f"写操作（{head}）将覆写/清空已有文件「{tgt}」，需人工确认")
                if state is None:
                    # ★ D1（2026-10-04 实测修）：fail-closed **不分模式**。
                    #   原判据 `state is None and in_sandbox` 的理由是"宿主模式交给④越界
                    #   路径面兜底"——**该前提不成立**：④ 的 `_outside_paths` 只扫
                    #   `_ABS_PATH_RE`（绝对路径），而链接逃逸的典型形态是**相对路径**
                    #   （`escape/target.txt`）⇒ 宿主模式两个面都不拦、静默放行。
                    #   实测（%TEMP% 真 junction + 生产 TaskRun + 生产回调）：
                    #      _target_exists('escape/target.txt') = None（不可核实）
                    #      _path_is_inside('escape/target.txt') = False
                    #      cp /dev/null escape/target.txt    host=PASS(不拦) sandbox=ASK
                    #      truncate -s 0 escape/target.txt   host=PASS(不拦) sandbox=ASK
                    #   可解析的目标（存在/不存在）语义不变（第五轮 xargs 红绿：
                    #   truncate 的新文件目标可解析 → 放行），判别力不受影响。
                    return (f"overwrite:{head}", f"写操作（{head}）目标「{tgt}」无法核实（fail-closed），需人工确认")
        return None

    @staticmethod
    def _target_state(
        tgt: str,
        target_exists: "Callable[[str], bool | None] | None",
    ) -> bool | None:
        """目标存在性：True / False（确定不存在）/ None（不可核实 → 调用方 fail-closed）。"""
        if not tgt or target_exists is None:
            return None  # 无回调（纯单测）→ 不可核实 → fail-closed
        if tgt in ("/dev/null", "nul", "/dev/zero", "/dev/stdin", "/dev/fd/0"):
            return False  # 空设备：不是覆写对象
        try:
            return target_exists(tgt)
        except Exception:
            return None

    @classmethod
    def _script_exec_scan(
        cls,
        command: str,
        approval_required: list[str],
        is_inside: Callable[[str], bool],
        in_sandbox: bool,
        script_reader: Callable[[str], str | None] | None,
    ) -> tuple[str, str] | None:
        """第四/五轮复验：解释器/构建工具跑**脚本文件**——真实行为在文件内容里。

        入口面（第五轮补齐）：脚本文件参数、stdin（`<`、`-`）、模块模式
        （`-m name` → 读 name 源码）、xargs 喂入、`make -C dir`（子目录 Makefile）。
        读不到内容 = 不可判定 → **fail-closed ask**（与文件头"看不懂就必审批"一致）。
        文件不存在（reader 返回 ""）→ 解释器自行报错，不拦。
        沙箱内同样扫描：脚本对挂载的 /w 交付物照样有破坏力。
        """
        for part in cls._deep_parts(command):
            toks = part.split()
            if not toks:
                continue
            head = cls._head_of(toks[0])
            targets: list[str] = []
            opaque = False  # 需要 fail-closed 的不可判定入口
            if head in _SCRIPT_RUNNER_HEADS:
                # stdin 形态：`python < evil.py` / `python -` / 管道喂入——
                # **仅对解释器头部**；tee/truncate 的 `< file` 是输入源不是执行内容
                #（第六轮复验⑧修正：`tee brand_new.txt < /dev/null` 曾被误判 stdin）
                if re.search(r"<\s*\S", part) or (len(toks) > 1 and toks[1].strip(_QUOTES) == "-"):
                    return ("script-stdin", f"{head} 从标准输入读取脚本（内容不可静态判定），需人工确认")
                if any(t2.strip(_QUOTES) in ("-m", "--module") for t2 in toks[1:]):
                    # 模块模式：python -m evilmod（等价于跑 evilmod/__main__.py）
                    mi = next(i for i, t2 in enumerate(toks) if t2.strip(_QUOTES) in ("-m", "--module"))
                    if mi + 1 < len(toks):
                        mod = toks[mi + 1].strip(_QUOTES)
                        if head.startswith(("python", "py")):
                            targets = [f"{mod.replace('.', '/')}.py", f"{mod.replace('.', '/')}/__main__.py"]
                        else:
                            targets = [mod]
                elif head in ("make", "gmake"):
                    cm = re.search(r"(?:^|\s)-C\s+(\S+)", part)
                    cd = cm.group(1).strip(_QUOTES) if cm else ""
                    fm = re.search(r"(?:^|\s)-f\s+(\S+)", part)
                    if fm:
                        st = fm.group(1).strip(_QUOTES)
                        if st in ("-", "/dev/stdin", "/dev/fd/0"):
                            return ("script-stdin", f"{head} 从标准输入读取脚本（内容不可静态判定），需人工确认")
                        targets = [(cd + "/" + st) if cd else st]
                    else:
                        base = (cd + "/") if cd else ""
                        targets = [base + "Makefile", base + "makefile", base + "GNUmakefile"]
                else:
                    for tk in toks[1:]:
                        bare = tk.strip(_QUOTES)
                        if bare.startswith("-"):
                            continue
                        targets = [bare]
                        break
                if not targets:
                    # 纯版本/帮助查询（--version/-V/--help/help）不是"执行内容不可判定"，
                    # 放行；其余情况（未知模块/构建系统）才是 opaque
                    info_flags = {"--version", "-v", "-V", "--help", "-h", "help", "version"}
                    rest_args = [t2.strip(_QUOTES) for t2 in toks[1:]]
                    if rest_args and all(a in info_flags for a in rest_args):
                        continue
                    opaque = True  # 跑起来了但找不到可读目标（未知模块/构建系统）
            elif toks[0].startswith(("./", ".\\")) or _SCRIPT_EXT_RE.search(head):
                targets = [toks[0].strip(_QUOTES)]
            if not targets and not opaque:
                # 入口都没识别到但命中脚本执行者（如 xargs python 的喂入、node 无参数）
                continue
            if opaque:
                return ("script-opaque", f"{head} 的执行内容无法静态判定（fail-closed），需人工确认")
            if script_reader is None:
                continue  # 无 reader（如纯审批单测）→ 交给其它判定面
            for tgt in targets:
                if tgt in ("-", "/dev/stdin", "/dev/fd/0"):
                    return ("script-stdin", "脚本来自标准输入（内容不可静态判定），需人工确认")
                try:
                    content = script_reader(tgt)
                except Exception:
                    content = None
                if content == "":
                    continue  # 文件不存在：解释器自行报错（模块名兜底尝试，全不存在即跳过）
                if content is None:
                    # 读不到内容 = 不可判定 → fail-closed（此前 fail-open 是漏报根因之一）
                    return ("script-unreadable", f"脚本「{tgt}」内容无法读取/解析（fail-closed），需人工确认")
                if content.startswith("\x00TOO_LARGE"):
                    return ("script-big", f"脚本「{tgt}」过大无法静态判定（fail-closed），需人工确认")
                hit = cls._scan_script_text(content, tgt, approval_required, is_inside, in_sandbox)
                if hit:
                    return ("script-danger", f"脚本「{tgt}」包含危险操作（{hit[1]}），需人工确认")
        return None

    @classmethod
    def _scan_script_text(
        cls,
        text: str,
        filename: str,
        approval_required: list[str],
        is_inside: Callable[[str], bool],
        in_sandbox: bool,
    ) -> tuple[str, str] | None:
        """脚本文件内容的危险扫描（第四轮词形 + 第五轮 AST/调用形态）。

        误报修复：Python 走 AST（注释/字符串字面量里的 `rm -rf dist` 是文档不是行为）；
        JS 只匹配**调用形态**（`.rmSync(`）；其余语言退回词形正则。
        补漏修复：Python AST 找 os.system/subprocess.*/shutil.rmtree/os.remove/
        os.unlink/pathlib.unlink；JS 补 fs.rmSync/fs.unlinkSync/rimraf/del。
        """
        low_name = filename.lower()
        if low_name.endswith(".py"):
            hit = cls._scan_python_ast(text)
            if hit:
                return hit
        elif low_name.endswith((".js", ".mjs", ".cjs", ".ts")):
            m = _JS_DELETE_CALL_RE.search(text)
            if m:
                return ("js-call", f"删除/覆写调用「{m.group(0).strip()[:40]}」")
        else:
            m = _SCRIPT_DANGER_RE.search(text)
            if m:
                return ("marker", f"危险调用模式「{m.group(0).strip()[:40]}」")
        # 通用：shell 脚本里的动词/破坏性头（放在行首或分隔符后=执行形态）
        if not low_name.endswith((".py", ".js", ".mjs", ".cjs", ".ts")):
            for line in text.split("\n"):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue  # 注释行跳过
                hits = cls._verb_hits(stripped, approval_required)
                if hits:
                    return ("verb", f"命令动词 {hits[0]}")
                ptoks = stripped.split()
                if ptoks and cls._head_of(ptoks[0]) in _ALWAYS_ASK_HEADS:
                    return ("head", f"破坏性操作 {cls._head_of(ptoks[0])}")
        if not in_sandbox:
            outside = cls._outside_paths(text, is_inside)
            if outside:
                return ("path", f"工作区外路径 {outside[0][0]}")
        return None

    @classmethod
    def _scan_python_ast(cls, text: str) -> tuple[str, str] | None:
        """Python 源码 AST 级危险调用检测（注释/字符串天然被排除）。

        覆盖：os.system / os.popen / os.remove / os.unlink / os.rmdir / os.rename /
        os.replace / subprocess.* / shutil.rmtree / shutil.move / pathlib 的
        unlink/write_text/write_bytes、内置 open(..., 'w')、eval/exec。
        """
        import ast as _ast
        try:
            tree = _ast.parse(text)
        except SyntaxError:
            return None  # 语法错的脚本执行会立刻报错，无风险
        for node in _ast.walk(tree):
            if not isinstance(node, _ast.Call):
                continue
            f = node.func
            name = ""
            if isinstance(f, _ast.Attribute):
                name = f.attr
            elif isinstance(f, _ast.Name):
                name = f.id
            lname = name.lower()
            if lname in ("system", "popen", "rmtree", "remove", "unlink", "rmdir", "rename", "replace", "move", "eval", "exec"):
                return ("py-ast", f"Python 危险调用「{name}(…)」")
            if lname in ("run", "call", "check_output", "check_call", "Popen"):
                return ("py-ast", f"Python 进程调用「{name}(…)」")
            if lname in ("write_text", "write_bytes"):
                return ("py-ast", f"Python 文件覆写「{name}(…)」")
            if lname == "open":
                # open(..., 'w'/'a'/'x') = 写入
                mode = ""
                if len(node.args) > 1 and isinstance(node.args[1], _ast.Constant):
                    mode = str(node.args[1].value)
                for kw in node.keywords:
                    if kw.arg == "mode" and isinstance(kw.value, _ast.Constant):
                        mode = str(kw.value.value)
                if "w" in mode or "a" in mode or "x" in mode:
                    return ("py-ast", f"Python 文件写入 open(…, '{mode}')")
        return None

    @staticmethod
    def _all_localhost(toks: list[str]) -> bool:
        urls = [t for t in toks if _LOCALHOST_RE.match(t)]
        if not urls:
            return False
        hosts = [m.group(1).lower() for m in (_LOCALHOST_RE.match(u) for u in urls) if m]
        return bool(hosts) and all(h in ("localhost", "127.0.0.1", "::1", "[::1]") for h in hosts)

    # ---------- ④ 只读白名单 ----------

    @classmethod
    def is_pure_readonly(cls, command: str) -> bool:
        """整条命令是否落在**只读白名单**内（越界免审批用，见 A 方案说明）。

        白名单判定 = 每个子命令（包装器透视到最内层）的可执行名都在 _READONLY_HEADS，
        且无变量赋值 / 控制流关键字 / 写标记 / 真实文件重定向。

        ★ 2026-10-11（审批降噪）两条修正，都来自真机 84 条命令的重放：
          · **静态赋值前缀先剥掉** ✓（`FF="…"; ls` / `export PYTHONIOENCODING=utf-8`
            是命令噪声，不是"看不懂的内容" ✓ —— 判据见 `_strip_static_assigns` ✓）
          · **变量展开一票否决** ✗（`$VAR`/`${VAR}`/反引号在正文里出现 ⇒ 判不了 ✗）——
            旧写法只在"首 token 以 $ 开头"时挡，`X=1; echo $X` 这种能溜过去 ✗
            （`_readonly_ok` 里另有一道 ✓ 但本函数也被单独用作判据 ⇒ 它得自己站得住 ✓）
        """
        low = command.lower()
        if any(m in low for m in _WRITE_MARKERS):
            return False
        _ASSIGNED_VALUES.clear()                               # ★ 临时黑板：每次判定清零 ✓
        parts = cls._split_top(command)
        _note_static_assigns(parts)                            # ★ 先收一遍静态赋值 ✓
        assigned: set[str] = set()
        for part in parts:
            name = _static_assign_name(part)
            if name:
                assigned.add(name)                             # 静态赋值：记下名字 ✓
            known = frozenset(assigned)
            stripped = _strip_static_assigns(part)
            if stripped and _has_var_expansion(stripped):
                # ★ 变量必须**认得出来**：整段就是变量的（`"$FF"` ✓）或**每一个**
                #   变量都在上文里静态赋过值（`test -f "$CHROME_PATH"` ✓）。
                #   —— 拿整段去 match 永远不中 ✗（我第一版就是这样 ✓ 真机 26 处全判死 ✓）
                if not (_all_vars_known(stripped, known)
                        or _var_only_reference(_split_keeping_quoted_paths(stripped.strip())[0], known)):
                    return False                               # 判定不了的展开 ⇒ 不白 ✗
            if not cls._part_readonly(part, depth=0, assigned=known):
                return False
        return True

    # ★★ 2026-10-11（平台闸修复 ③）：**纯只读 ⇒ 成批放行，不弹审批** ✓
    #
    #   现场（真实任务，60 分钟弹出 44 次审批门 ✓ 审计账 53 条 approval 可查 ✓）：
    #     `cd /w && find . -name '*.py' -type f | wc -l` 这类命令被判「结构嫌疑」必问 ✗
    #     —— 可它**看清了也不可能改任何东西** ✓ 为它在半夜把人叫起来 44 次，正是要治的病 ✓。
    #
    #   ★ 判据只有一条、且**只放"绝对安全"的那一档** ✓：
    #     每一段的首词都落在只读白名单（`is_pure_readonly` ✓ 十二轮加固出来的那张表 ✓），
    #     且全文没有危险动词/固定必问头/破坏性覆写头 ✓。
    #   ★ **不放**：删 / 覆写 / 内联代码 / 命令替换 / heredoc / 联网外传 / 越界写 ✓
    #     ——它们要么被白名单本身挡住（`rm` 不在表里 ✓），要么被下面这两道再挡一次 ✓。
    @classmethod
    def _readonly_ok(cls, command: str, approval_required: Iterable[str]) -> bool:
        _ASSIGNED_VALUES.clear()                               # ★ 临时黑板：每次判定清零 ✓
        _note_static_assigns(cls._split_top(command))
        if not cls.is_pure_readonly(command):
            return False
        # ★ 命令替换 / 反引号 / 进程替换：**一律不算只读** ✓
        #   为什么单独再挡一道（`is_pure_readonly` 里没有这一条 ✗）：
        #   它判的是"**每个子命令的首词**在只读白名单里" ✓ 而 `echo `whoami`` 的
        #   首词是 `echo`（白名单 ✓）—— 里面那段是被替换进来的**另一条命令** ✗
        #   旧代码只在"越界路径"那条路上用它（那里外面还套着别的面 ✓）；
        #   现在它变成**主判据**了 ⇒ 这一条必须自己挡住 ✓（测试里钉着 ✓）。
        if any(t in command for t in ("$(", "`", "<(", ">(")):
            return False
        # ★ 重定向到**真实文件**（含引号里那种 `awk '{print > "out.txt"}'`）⇒ 不是只读 ✗
        #   （红绿门抓出来的：藏在引号里的重定向曾让写文件的 awk 走了只读快路 ✗）
        if _has_real_redirect(command):
            return False
        # ★ 2026-10-11（审批降噪）：正文里的**变量展开**同样一票否决 ✗
        #   `export PATH="/x:$PATH"` / `X=1; echo $X` 这类，值/内容**不是**字面量 ⇒
        #   静态判不出它到底动没动文件 ⇒ 照旧问 ✓（静态赋值那条放宽只认字面量 ✓）。
        #   ★ 例外只有一种：这个变量**上一行刚被静态赋值过**、且这一整段就是 `"$FF"`
        #     （值确定 ⇒ 当字面量看 ✓）—— 真机 26 处 `"$FF"`/`"$V"` 正是这个形态 ✓。
        for part in cls._split_top(command):
            stripped = _strip_static_assigns(part)
            if not _has_var_expansion(stripped):
                continue
            # ★ 只看**首词**：`"$FF" -version` 的变量在首词上 ✓（参数位的变量由
            #   `_part_readonly` 逐段判 ✓）—— 我第一版拿**整段**去 match ✗ ⇒ 永远不中 ✓
            first = _split_keeping_quoted_paths(stripped.strip())[:1]
            if first and _var_only_reference(first[0], frozenset(_ASSIGNED_VALUES)):
                continue
            return False
        flat = cls._flatten(command)
        if cls._DESTRUCTIVE.search(flat):                       # 删/移/改名/写进去…
            return False
        req = {str(v).lower() for v in (approval_required or [])}
        for part in cls._deep_parts(command):
            toks = part.split()
            if not toks:
                continue
            head = cls._head_of(toks[0])
            if head in _ALWAYS_ASK_HEADS:                       # taskkill/shutdown/iex…
                return False
            if head in _DESTRUCTIVE_OVERWRITE_HEADS:            # tee/dd/truncate/cp…
                return False
            if head in req:                                     # 用户点名要问的动词（rm…）
                return False
            # ★ 二十五轮 🔴2 那两组的白名单在这条路上**也要在** ✓：
            #   · 容器 CLI（`grep docker build.log` 这种"容器字样只当文本"的写法）——
            #     容器生命周期面自己会用 `_BENIGN_NONCONTAINER_HEADS` 放行 ✓
            #     可它**排在这条只读快路之后** ✓ ⇒ 我得在这儿同样放行 ✓
            #     （不放的话"清空白名单"那个红绿实验就变绿了 ⇒ 判别力被这次改动静默吃掉 ✗
            #       实测就是这样：`!!FAIL!! 二十五轮🔴2 白名单清空（grep docker 误拦复活）`）
            #   · 未知包装器 + 容器 CLI（`zzznotreal docker system prune -a`）——
            #     那是"执行/喂入方式不可知"的 fail-closed 面 ✓ **不算只读** ✗
            #     （旧代码里只读判定排在它后面 ✓ 顺带也放行了它 ✗ 这条一并堵上 ✓）
            if head in _CONTAINER_CLIS or any(cls._head_of(t) in _CONTAINER_CLIS for t in toks):
                if head in _BENIGN_NONCONTAINER_HEADS:
                    break                                       # 白名单 head：交给容器面自己判 ✓
                return False
        return True

    @classmethod
    def _readonly_reason(cls, command: str) -> str:
        """给审计写清"凭什么说它只读" ✓（人一眼能核对 ✓ 不是一句空话 ✗）。"""
        names: list[str] = []
        for part in cls._deep_parts(command):
            toks = part.split()
            if not toks:
                continue
            head = cls._head_of(toks[0])
            if head and head not in names:
                names.append(head)
        return "程序全在只读白名单：" + "、".join(names[:6]) + ("…" if len(names) > 6 else "")

    # ---------- 「总是允许」的**命令级**记忆（2026-10-11 平台闸修复 ③）----------

    @staticmethod
    def _command_fingerprint(command: str) -> str:
        """命令原文的指纹：归一化空白后取 sha1 前 16 位 ✓（**只认一模一样的那条** ✓）。

        ★ 为什么用指纹而不是原文：命令可能很长（几条 heredoc 加起来上千字 ✓）
          且可能带密钥/路径 —— 存指纹既不涨内存 ✓ 也不多留一份敏感原文 ✓。
        ★ 归一化只做**空白折叠 + 去首尾** ✓ 刻意**不**做"参数等价化" ✗ ——
          这次要解决的是"同一条命令被反复问" ✓ 不是"相似命令自动放行" ✗。
        """
        raw = re.sub(r"\s+", " ", str(command or "").strip())
        if not raw:
            return ""
        return hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()[:16]

    def _command_remembered(self, task_id: str, command: str) -> bool:
        """这条**原文命令**在这个任务里被用户点过「总是允许」吗 ✓。

        ★ 存的是 `"cmd:<指纹>"` ✓ 查的也必须带同一个前缀 ✗ ——
          我第一版查的是**裸指纹** ✗ 于是"永远查不到"✓ 功能看着接上了、实际从没生效 ✓
          （**这条测试把我拦下了** ✓ —— 所以它值得留在注释里 ✓）。
        """
        fp = self._command_fingerprint(command)
        if not fp:
            return False
        return ("cmd:" + fp) in self._always.get(task_id, set())

    @classmethod
    def _part_readonly(cls, part: str, depth: int, assigned: frozenset[str] = frozenset()) -> bool:
        """这一**段**是不是只读（`assigned` = 前面已经静态赋过值的变量名 ✓）。

        ★ 2026-10-11（审批降噪）改了四处，全部来自真机 84 条命令的重放：
          ① 静态赋值前缀先剥（`FF="…"` / `export X=y`）✓ 剥完为空 ⇒ 这行只是赋值 ✓
          ② `[` 归一到 `test` ✓
          ③ 白名单 head 再走一遍**用法级**复核（`sed -n` ✓ / `sed -i` ✗、`ffmpeg -version` ✓ /
             `ffmpeg -i in out` ✗）——判据见 `_readonly_safe_head` ✓
          ④ `"$FF"` 这种"整段就是一个**静态赋过的**变量" ⇒ 当字面量看 ✓
        """
        if depth > 6:
            return False
        cur = _strip_wrapping_quotes(part)
        toks = _split_keeping_quoted_paths(cur)      # ★ 带引号的路径不切碎 ✓（见它的说明）
        if not toks:
            return True
        # 本层重定向先查（同 _part_suspicion：不能被包装器递归短路）
        bare = _QUOTED_RE.sub("", cur)
        for m in _REDIRECT_TOKEN_RE.finditer(bare):
            tgt = m.group(1)
            if tgt.lower() in _NULL_TARGETS or _FD_DUP_RE.fullmatch(tgt):
                continue
            return False  # 重定向到真实文件 = 写
        raw = cls._raw_first_token(cur)
        if "=" in raw or raw.startswith("$") or _is_assign_shaped(cur):
            # ★ 2026-10-11（审批降噪）：**静态赋值前缀先剥掉再判** ✓
            #   旧写法一见 `=` 就判"脚本片段，判定不了" ✗ ⇒ `FF="…"` 这种纯噪声
            #   把整条命令的只读资格拿走 ✗（真机 32 处 `NAME=` + 53 处 `export`✓）。
            #   剥掉之后**还有内容** ⇒ 继续按内容判 ✓；剥完是空的 ⇒ 这行只是赋值 ✓ 放行 ✓
            #   （值带 `$`/反引号的**剥不掉** ⇒ 落到 `return False` ✓ 照旧问 ✓）
            #   ★ `_is_assign_shaped` 那一支是补的 ✗：`export NAME=…` 的**首 token 是
            #     `export`**（不带 `=` ✓）⇒ 只看 `raw0` 会整段跳过剥离 ✗
            #     —— 于是"唯一一处非只读 head 就是 export"把命令判死 ✓（真机 53 处 ✓）。
            rest = _strip_static_assigns(cur)
            if not rest:
                return True
            if rest == cur:
                return False  # 剥不动（含 $ 展开 / 不是赋值形态）⇒ 判定不了 ✗
            cur = rest
            toks = cur.split()
            if not toks:
                return True
        # ★ `"$FF"` / `$V`（**首词**就是一个静态赋过的变量）⇒ 当字面量看 ✓
        #   ★ 顺序讲究：**先认"整段就是变量"再做通用否决** ✗ —— 反过来的话
        #     `"$FF" -version` 会被当成"看不懂的展开"直接判死 ✓（我第一版就是这样 ✓）
        #   ★ 判据：`_var_only_reference` **自带剥引号** ✓ —— 这里传**裸 token** ✓
        #     （先 `_strip_wrapping_quotes` 会把 `"$FF"` 变成 `$FF` ⇒ 反而判不出来 ✓）
        ref = _var_only_reference(toks[0], assigned)
        if not ref and _has_var_expansion(toks[0]):
            return False                                       # 不认识的变量当命令 ⇒ 判不了 ✗
        if ref:
            # 变量"身份" = 它被赋的那个字面量（`FF="…/ffmpeg.exe"` ⇒ ffmpeg ✓）
            #   —— 认不出这个身份 ⇒ 判不了 ⇒ 照旧问 ✗（`X=…; "$X"` 这类 ✓）
            head = cls._head_of(ref)
            if head not in _READONLY_HEADS:
                return False
            if len(toks) == 1:
                return True                                    # `"$FF"` 单独一个词 ✓
            # `"$FF" -version` ⇒ 首词换成中性字面量，**继续按参数判** ✓
            # （`-i`/输出文件这些仍然看得见 ⇒ `sed -i`、`ffmpeg … out.mp4` 照旧拦 ✗）
            toks = ["STATICVALUE"] + toks[1:]
            cur = " ".join(toks)
        elif _has_var_expansion(cur):
            # ★ 变量在**参数位**（`test -f "$CHROME_PATH"` ✓）：只要每个变量都认得出来
            #   （上文静态赋过 ✓），就把它们当**中性字面量**继续逐段判 ✓
            #   —— 参数是"看一眼的目标"还是"要改的文件"由 head 决定 ✓ 不受影响 ✓。
            #   （`sed -n "$P"` ✓ 放行 / `sed -i "$P"` ✗ 照旧拦 ✓）
            if not _all_vars_known(cur, assigned):
                return False                                   # 有认不出的变量 ⇒ 判不了 ✗
            cur = _mask_known_vars(cur, assigned)
            toks = _split_keeping_quoted_paths(cur)
            if not toks:
                return True
            head = cls._head_of(toks[0])
        else:
            head = cls._head_of(toks[0])
        if head in _WRAPPER_HEADS:
            m = _WRAPPER_RE.match(cur)
            if not m:
                return False  # 透视不了 = 不白
            inner = _strip_wrapping_quotes(m.group(1))
            if not inner.strip():
                return False
            sub_assigned = set(assigned)
            for sub in cls._split_top(inner):
                nm = _static_assign_name(sub)
                if nm:
                    sub_assigned.add(nm)
            return all(cls._part_readonly(sub, depth + 1, frozenset(sub_assigned))
                       for sub in cls._split_top(inner))
        if head in _OPAQUE_WRAPPERS or head in _SHELL_KEYWORDS:
            return False
        if head in _TRANSPARENT_WRAPPERS:
            # 复审 P0 误伤修复：透明包装递归内层（timeout 60 ls → ls 白名单 → 白）
            # ★ 2026-10-11：`command -v x` / `command -V x` = 内建查询（"x 在哪"）✓
            #   `-v` 是**布尔**开关 ✗ 不吃参数 ✓ —— 上面那步"短选项带值"会把 `x` 当成
            #   `-v` 的值一起吞掉 ⇒ 内层变成残缺片段 ⇒ 一条纯查询被判"判定不了" ✗
            #   （真机 `command -v ffmpeg` 正是这形态 ✓）。
            if head == "command" and len(toks) >= 3 and toks[1].strip(_QUOTES) in _BOOL_ONLY_FLAGS:
                return cls._part_readonly(toks[2].strip(_QUOTES), depth + 1, assigned)
            inner = _strip_wrapper_prefix(toks)
            if not inner:
                return False
            return cls._part_readonly(inner, depth + 1, assigned)
        head = _READONLY_ALIASES.get(head, head)          # `[` → `test` ✓
        if head not in _READONLY_HEADS:
            return False
        # ★ 2026-10-11：同一个程序换个开关就从"看"变"写" ✗ ⇒ 补一层用法复核 ✓
        return _readonly_safe_head(head, toks, cur)

    # ---------- 判定入口 ----------

    def check(
        self,
        task_id: str,
        command: str,
        approval_required: list[str],
        is_inside: Callable[[str], bool],
        *,
        in_sandbox: bool = False,
        network_disabled: bool = False,
        script_reader: "Callable[[str], str | None] | None" = None,
        target_exists: "Callable[[str], bool | None] | None" = None,
    ) -> Verdict | None:
        """需要人确认时返回 `Verdict(action="ask")`；白名单只读越界返回 `action="allow_readonly"`；否则 None。

        ★ 2026-10-05「本任务全部允许」：用户在一个**具体任务**上明确按过那个按钮之后，
          这个任务的命令**不再逐条询问**（含 `$()`/heredoc 这类结构性命令 —— 它们平时是
          **故意不记忆**的，见 `remember()` 的审计 P1）。这时返回 `action="allow_all"`：
          照样**留审计事件**（可追溯），只是不打断人。
          **生命周期**：只活在内存里，后端一重启就失效（安全边界不变）。

        in_sandbox：命令将进容器执行——跳过"越界路径"面（容器摸不到宿主路径）；
        network_disabled：容器断网——跳过"联网命令"面（发了也发不出，问了纯噪音）。
        危险动词与结构嫌疑**两种模式下都要问**：沙箱隔离不了挂载的 /w 工作区
        （验证报告 23 P0-B）。
        """
        if not command.strip():
            return None
        # ★ 2026-10-11（审批降噪）：先把这条命令里的**静态赋值**收进临时黑板 ✓ ——
        #   `V="/d/…/python.exe"` 之后 `"$V" …` 这种写法要靠它才认得出来 ✓（越界面用 ✓）。
        _ASSIGNED_VALUES.clear()
        _note_static_assigns(self._split_top(command))
        # ★★ 2026-10-06（夜里实测：venv 里的 python.exe 不见了，只剩 pythonw.exe ✗）：
        #   任务跑的是**宿主上的真命令**（沙箱关着），而实测它们会跑 `rm -rf data todo.json`
        #   这类清理命令 ⇒ 只要路径写歪一点，就能删到**应用自己的安装目录** ✗✗。
        #   所以这里加一道**硬保护**：命令里出现应用关键目录（app/.venv/scripts/frontend/config…）
        #   且**不在本任务的工作区里** ⇒ **一律要人工确认**，连"本任务全部允许"也不豁免 ✓。
        #   宁可多问一句，也不能让一次手滑把装好的环境删掉。
        protected = self._protected_hit(command, is_inside)
        if protected:
            return Verdict(f"命令触到应用自身的目录（{protected}）—— 这类操作一律要人工确认",
                           "protect:" + command[:60], "ask")
        if task_id in self._allow_all:
            return Verdict("该任务已按「本任务全部允许」放行", "allowall:" + command[:60], "allow_all")
        # ★★ 2026-10-11（平台闸修复 ③）-1：**用户点过「总是允许」的那条命令，同任务内不再问** ✓
        #   现场：审计账里 11 次 `always` 有相当一部分是**白点的** ✗ ——
        #   `remember()` 对 `struct:*` 键故意不记（那条安全决定本身没错 ✓）
        #   可真实任务的命令**大多数都带一点结构**（`cd X && …`）⇒ 点了也不生效 ✗。
        #   这里按"**命令原文指纹**"记：只认**一模一样**的那条 ✓ 只认**这个任务** ✓。
        #   ★ 位置讲究 ✓：**在硬保护之后**（碰应用目录的命令连"本任务全部允许"都不豁免 ✓
        #     见上面 `_protected_hit` ✓）—— 所以记忆同样豁免不了它 ✓。
        if self._command_remembered(task_id, command):
            return None
        # ★★ 2026-10-11（平台闸修复 ③）-2：**能证明纯只读 ⇒ 直接放行** ✓
        #   这些命令（`ls -la` / `cd /w && find … | wc -l`）本来就是"免审批"那一档 ✓
        #   ——它们过去在**工作区内**时走的是"一路判下来 None"✓ 在**越界**时走
        #   `allow_readonly`（A 方案 ✓ 留一条审计 ✓）。
        #   现在多出来的只有一件事：**带结构（管道/`&&`）的纯只读命令不再被判"结构嫌疑"问人** ✓
        #   ⇒ 这就是题面要的"把只读类命令批量放行" ✓（判据见 `_readonly_ok` ✓ 窄得很 ✓）。
        #   ★ 这里返回 `None` 而不是 `allow_readonly`：**与工作区内只读命令的既有语义一致** ✗✓
        #     （`test_approval_failclosed` / `test_approval_reaudit` / `test_approval_security`
        #      三组既有测试钉着"工作区内的正常只读命令 → None"✓ 那是**既定契约** ✓
        #      而且用户不需要看"N 条只读命令已放行"的刷屏 ✓）。
        #   ★ **越界路径的命令不走这条** ✓ —— 那种命令有自己的一档语义（`allow_readonly`
        #     在 ④ 越界面 ✓），这里抢答会把两档口径搅乱 ✗。
        if not self._outside_paths(command, is_inside) and self._readonly_ok(command, approval_required):
            return None
        # ★★ 2026-10-11（审批降噪 · 真机 176 次弹门）：**只读快路不再被"越界"挡住** ✓
        #   现场：真机命令**每一条都带工作区之外的绝对路径**（`cd /d/测试-Flash对比/…`、
        #   `"/d/AI/agent-shell/backend/.venv/Scripts/python.exe"`、`/d/MiniMax Design/…`）
        #   ⇒ 上面那一行的 `not self._outside_paths(...)` 永假 ⇒ **`_readonly_ok` 一次
        #   都没被求值** ✗ —— 上一班接的"只读批量放行"在这条任务上等于没接上 ✗
        #   （诊断复算：84 条真命令里 `readonly_ok` 列**全是 False** ✓ 78 条判 ask ✓）。
        #   ★ 判据本身**一个字没松** ✓：能走到这里 ⇒ 上面十道面（结构/动词/联网/内联/
        #     容器/脚本/覆写/越界写…）**全都判过"不拦"**了 ✓；`_readonly_ok` 再要求
        #     "每一段都在只读白名单里、无 `$()`/heredoc/内联代码/变量展开、无危险动词" ✓
        #     ⇒ 剩下唯一的差别只是"**这些只读命令看的是工作区外的目录**" ✓
        #     （看 ≠ 改 ✓；要改的部分由上面那些面照旧拦 ✗ 测试里逐条钉着 ✓）。
        #   ★ 返回 `allow_readonly` 而不是 `None`：与 ④ 越界面**同一档语义** ✓
        #     —— 越界只读要**留一条审计**（可追溯 ✓ 用户能看见"它读了哪儿" ✓）。
        #   ★ 工作区**内**的纯只读命令仍按**既定契约**返回 `None` ✗✓（不是"已放行"的
        #     刷屏 ✓ —— `test_approval_failclosed` / `test_approval_reaudit` /
        #     `test_approval_security` 三组都钉着这个语义 ✓ 不许动 ✓）。
        if self._readonly_ok(command, approval_required):
            outside = self._outside_paths(command, is_inside)
            if not outside:
                return None
            # ★ 键**沿用既有的"目录级"口径** ✗✓（`test_approval_security.py` 的
            #   `test_outside_path_memory_keyed_by_directory` / `..._unifies_equivalent_path_spellings`
            #   两条钉着它 ✓）：同一个目录的**不同只读命令**（`ls` / `find -type f`）
            #   算同一个键 ⇒ 用户点过一次「总是允许」之后，同一目录换个命令也不再问 ✓。
            #   ⚠️ 我第一版用 `"readonly:" + command[:60]`（**按命令**）✗ ⇒ 那两条既有用例
            #      当场变红 ✓（红绿门拦住的就是这种"顺手改掉别人契约"✓）。
            key = self._outside_key(outside[0][1])
            if key in self._always.get(task_id, set()):
                return None                                    # 这个目录用户已经放行过 ✓
            return Verdict(self._readonly_reason(command), key, "allow_readonly")
        # ★★ 2026-10-06「这类以后都别问」（**跨任务、持久化** ✓）：
        #   背景：实测一个 10 分钟的小活要用户点 **2–5 次**审批 ✓（等审批常常比干活还久 ✓）。
        #   已有的两档都**不跨任务**：`allow_all` 只活在本任务内存里 ✓、`remember` 只记本任务 ✓
        #   ⇒ 下一个任务又从零开始问 ✓✓。
        #   这一档：用户在某个命令上明确按过「这类以后都别问」⇒ 记住**这个程序**（命令首词 ✓，
        #   如 `python` / `pytest` / `git` ✓），**新任务也不再问** ✓，落盘保存（重启仍在 ✓）。
        #   安全边界（三道，一个都不少 ✓）：
        #     ① 硬保护在它**之前**就判了 ✓（碰应用目录的命令连"全部允许"都不豁免 ✓）
        #     ② **破坏性动词不适用** ✗（`rm`/`del`/`move`/`set-content`… 一律照问 ✓）
        #     ③ **结构性命令不适用** ✗（`$()`/heredoc/eval… 一次豁免 = 整类任意执行 ✓ 审计 P1 ✓）
        _fw = self._forever_key(command)
        if _fw and _fw in self._forever:
            _flat0 = self._flatten(command)
            _struct = any(self._structural_suspicion(v) for v in (command, _flat0))
            if not _struct and not self._forever_unsafe(command):
                return Verdict(f"你之前说过「{_fw} 这类以后都别问」—— 这条直接放行",
                               "forever:" + _fw, "allow_forever")
        remembered = self._always.get(task_id, set())
        # 复审 P0：反斜杠拆字（c\u\r\l）/零宽字符 —— 动词与联网判定同时跑
        # 归一化副本；越界路径判定只用原文（C:\Users 的反斜杠不能剥）
        flat = self._flatten(command)
        struct_variants = (command, flat)

        # ① 结构嫌疑：看不懂就必审批（fail-closed 核心）
        for variant in struct_variants:
            susp = self._structural_suspicion(variant)
            if susp:
                slug, reason = susp
                key = f"struct:{slug}"
                if key not in remembered:
                    return Verdict(reason, key, "ask")

        # ①a 复审 P0（24 号报告）：破坏性系统操作——无路径上下文也必问
        #   （shutdown 类没有路径可分析；git clean/reset --hard 在动词兜底里拦）
        for variant in struct_variants:
            for part in self._deep_parts(variant):
                toks = part.split()
                if toks and self._head_of(toks[0]) in _ALWAYS_ASK_HEADS:
                    h = self._head_of(toks[0])
                    key = f"verb:{h}"
                    if key not in remembered:
                        return Verdict(f"破坏性系统操作（{h}），需人工确认", key, "ask")

        # ①b 引号内脚本执行语义（审计 P0：awk system()/引号内重定向曾只看越界路径）
        inq = self._inquote_exec(command)
        if inq:
            slug, reason = inq
            key = f"struct:{slug}"
            if key not in remembered:
                return Verdict(reason, key, "ask")

        # ①c 复审 P0：远程代码执行类工具（mshta/certutil -urlcache/rundll32…）
        for variant in struct_variants:
            remote = self._net_or_inline_remote_only(variant)
            if remote:
                slug, reason = remote
                key = f"struct:{slug}"
                if key not in remembered:
                    return Verdict(reason, key, "ask")

        # ①d 二十六轮第 7 批：git 配置注入的【环境变量】面——payload 不在参数里，
        #    参数面永远拦不到，只能看命令开头的赋值前缀（见 _git_config_env_injection）
        for variant in struct_variants:
            ginj = self._git_config_env_injection(variant)
            if ginj:
                slug, reason = ginj
                key = f"struct:{slug}"
                if key not in remembered:
                    return Verdict(reason, key, "ask")

        # ② 危险动词（P1-5 保留）
        for variant in struct_variants:
            hits = self._verb_hits(variant, approval_required)
            if hits:
                key = "verb:" + hits[0].lower()
                if key not in remembered:
                    return Verdict(f"命中危险动作 {hits[0]}", key, "ask")

        # ②b 复审 P0：写动词 + 不可静态判定路径（$VAR）/ find -delete / sed -i
        for variant in struct_variants:
            opq = self._opaque_path_write(variant)
            if opq:
                slug, reason = opq
                key = f"struct:{slug}"
                if key not in remembered:
                    return Verdict(reason, key, "ask")

        # ②d 容器生命周期（第十三轮 🔴1）：独立于联网面——沙箱+断网
        # （network_disabled=True，生产默认 sandbox_network="none"）会跳过 ③，
        # 但容器 run/exec/up 依然是"拉镜像+执行任意代码"的换入口，必须恒问。
        # 拍板口径（十二轮提出、十三轮落实）：compose up/build/pull 与
        # docker/podman/nerdctl/ctr 的 run/exec/pull/build/create 恒 ASK，
        # 与 sandbox_network 无关；struct 键不允许"总是允许"。
        for variant in struct_variants:
            life = self._container_lifecycle(variant)
            if life:
                slug, reason = life
                key = f"struct:{slug}"
                if key not in remembered:
                    return Verdict(reason, key, "ask")

        # ③ 联网 / 内联代码：外传与任意代码执行不靠事后审计（容器断网时跳过联网面）
        for variant in struct_variants:
            net = self._net_or_inline(variant)
            if net and not (network_disabled and not net[0].startswith("inline:")):
                slug, reason = net
                key = f"net:{slug}"
                if key not in remembered:
                    return Verdict(reason, key, "ask")

        # ②c 破坏性写能力面（第五轮复验）：覆写/清空已有文件 → ask（沙箱内外一致，
        #    /w 是真实挂载；换入口（sh -c/xargs/docker compose）都逃不出这个能力面）
        dw = self._destructive_write_scan(command, target_exists, is_inside, in_sandbox)
        if dw:
            slug, reason = dw
            key = f"struct:{slug}"
            if key not in remembered:
                return Verdict(reason, key, "ask")

        # ②b xargs 管道喂入（第八轮复验 A2）：`… | xargs truncate -s 0` —— 目标由
        #    管道喂入【实现侧残留风险声明（十三轮口径）】：xargs 下 cp/install 家族
        #    的【可见末位操作数】视为目标、按存在性正常判定（与 §A4 锚点
        #    `echo src.txt | xargs cp dst.txt → PASS` 一致）；多源投喂"目录型 dest"
        #    时真实落点 dest/basename(源) 不可判定——本口径宁少问不误伤，残余风险
        #    由 tests/test_round9_fixes.py::test_xargs_visible_target_judged_normally 钉住。
        #    管道喂入，token 里只剩动词 → 目标提取失败 → 既不 ask 也 fail-closed = 漏。
        #    规则：透明包装的内层是破坏性写动词且提不出显式目标 → fail-closed ask。
        #    第九轮复验⑦：本阶段**只在命令真的含 xargs 时执行**——此前对全部命令
        #    生效，把 `tee /dev/null < src`（无 xargs）也按"目标喂入"误拦。
        if re.search(r"(?:^|[\s|;&(])xargs(?:\s|$)", command):
            for dpart in self._deep_parts(command):
                dtoks = dpart.split()
                if not dtoks:
                    continue
                dhead = self._head_of(dtoks[0])
                # 第九轮复验②：xargs + perl/ruby/sed -i（就地改写由管道喂入的文件）
                if dhead in _INPLACE_FLAG_HEADS and any(
                    t2.strip(_QUOTES) == "-i"
                    or (t2.strip(_QUOTES).startswith("-i") and not t2.strip(_QUOTES).startswith("--"))
                    for t2 in dtoks[1:]
                ):
                    return Verdict(f"就地改写（{dhead} -i）的目标由管道/上游喂入，无法静态判定，需人工确认", f"struct:inplace:{dhead}")
                if dhead in _DESTRUCTIVE_OVERWRITE_HEADS:
                    tgt_found = self._extract_write_target(dhead, dpart)
                    if not tgt_found or tgt_found in ("/dev/null", "nul", "/dev/zero", "/dev/stdin"):
                        # A2 第六轮：目标由管道喂入（token 里只剩动词）或目标位是
                        # /dev/null（`xargs cp /dev/null` —— 真正的写目标是喂入的
                        # 文件流，命令行里看不见）→ 无法静态判定 → fail-closed ask
                        return Verdict(f"破坏性写（{dhead}）的目标由管道/上游喂入，无法静态判定，需人工确认", f"struct:overwrite:{dhead}")
                    # 第八轮复验 A2 第 4 变体：`xargs cp /dev/null`（源 /dev/null 且**无
                    # 显式目标**的变体被上面拦；但审查实测的是 find|xargs cp /dev/null
                    # → 源 /dev/null + 无目标。上面 `not tgt_found` 已覆盖。）
                    st = self._target_state(tgt_found, target_exists)
                    if st is None:
                        # ★ D1（审计台账，2026-10-04 实测修）：fail-closed 不该分模式。
                        #   原判据是 `if st is None and in_sandbox:` —— 宿主模式下这一支
                        #   不生效，而**相对路径**经 symlink/junction 逃出工作区时也进不了
                        #   ④越界面（那只扫 `_ABS_PATH_RE` 的绝对路径）⇒ 宿主模式静默放行：
                        #     实测（%TEMP% 真 junction + 生产 TaskRun）：
                        #       target_exists('escape/target.txt') = None（不可核实）
                        #       cp /dev/null escape/target.txt        host=PASS  sandbox=ASK
                        #       truncate -s 0 escape/target.txt       host=PASS  sandbox=ASK
                        #   可解析的目标（存在/不存在）仍走②常规判定（第五轮 xargs 红绿：
                        #   truncate 的新文件目标可解析 → 放行），判别力不受影响。
                        return Verdict(f"破坏性写（{dhead}）的目标「{tgt_found}」无法核实（fail-closed），需人工确认", f"struct:overwrite:{dhead}")
                    if st is True:
                        return Verdict(f"破坏性写（{dhead}）将覆写已有文件「{tgt_found}」（管道喂入，目标不可见），需人工确认", f"struct:overwrite:{dhead}")

# ③b 脚本执行扫描（第四轮复验）：解释器/构建工具跑工作区脚本 → 读内容判定
        for variant in struct_variants:
            scr = self._script_exec_scan(variant, approval_required, is_inside, in_sandbox, script_reader)
            if scr:
                slug, reason = scr
                key = f"struct:{slug}"
                if key not in remembered:
                    return Verdict(reason, key, "ask")

        # ④ 越界路径（P1-5 保留 + 白名单化 A 方案；沙箱内跳过——摸不到宿主路径）
        if in_sandbox:
            return None
        outside = self._outside_paths(command, is_inside)
        if outside:
            raw, norm = outside[0]
            # ★ 2026-10-06：**解释器不算"越界路径"** —— 任务要用后端自己的 python 跑工作区里的脚本
            #   （那是环境交底教它们的用法 ✓），可它在工作区之外 ⇒ 每跑一次问一次 ✗
            #   （实测：终验那一步为此被问了 3 次，用户睡觉时全靠自动放行器顶着）。
            #   放宽的边界很窄：**只是"执行"这个解释器**、且命令里没有破坏性动作 ✓；
            #   要删/移/写解释器本身，仍由"应用目录硬保护"拦下 ✓。
            if self._iterpreter_only(raw, command):
                return None
            # 记忆键按**归一化后**的目录前几段：同一目录的不同写法算同一个键
            key = self._outside_key(norm)
            if key not in remembered:
                if self.is_pure_readonly(command):
                    return Verdict(f"只读访问工作区之外：{raw}", key, "allow_readonly")
                return Verdict(f"命令涉及工作区之外的路径：{raw}", key, "ask")

        return None

    # ---------- 等待 / 决议（P1-6：按任务隔离）----------

    async def wait_decision(self, task_id: str, call_id: str) -> str:
        """loop 在此挂起（任务状态已置 waiting_approval），直到**该任务**的 /approve 到达。"""
        fut: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self._pending[(task_id, call_id)] = fut
        try:
            return await fut
        finally:
            self._pending.pop((task_id, call_id), None)
            self._cmds.pop((task_id, call_id), None)      # ★ 收尾一起清 ✓（别让它无限涨 ✗）

    # ---------- ★ 2026-10-07（第 9 项补）：那条命令，**由服务端自己记** ----------
    #
    # 为什么必须存在：审计账要回答"我批的**是哪条命令**" ✓
    #   而原来这条信息只在**界面**手里 ✗（端点靠 `getattr(req, "command", "")` 拿 ✓
    #   而界面压根没传 ⇒ 账本里只剩一句"批过一次"✓ 那这本账就白记了 ✓）
    #
    # ★ 方向不能反 ✗：命令必须**由服务端在请求审批那一刻记下来** ✓
    #   绝不能改成"让界面把命令传上来当账本内容" ✗ ——
    #   审计账的内容**不能由客户端说了算** ✓ 否则这本账就不可信了 ✓
    #   （客户端传来的只能当**提示** ✓ 见 `_do_approve` ✓）

    def note_command(self, task_id: str, call_id: str, command: str) -> None:
        """记下"这次审批问的是哪条命令" ✓（由 loop 在挂起等待之前调用 ✓）。"""
        if command:
            self._cmds[(task_id, call_id)] = str(command)[:2000]

    def command_of(self, task_id: str, call_id: str) -> str:
        """取回那条命令 ✓（服务端自己记的那份 ✓ 没有就返回空串 ✓）。"""
        return self._cmds.get((task_id, call_id), "")

    def resolve(self, task_id: str, call_id: str, decision: str) -> bool:
        fut = self._pending.get((task_id, call_id))
        if fut is None or fut.done():
            return False
        fut.set_result(decision)
        return True

    def pending_count(self) -> int:
        return len(self._pending)

    def pending_for(self, task_id: str) -> list[str]:
        """这个任务当前有哪些 call_id 在等审批（**给群里的审批卡片用**）。

        为什么需要：任务页是"点开任务才看到审批"，而群里要主动把审批播报出来 ——
        没有这个查询，看门的那条链就不知道"它卡在等审批"。
        """
        return [cid for (tid, cid) in list(self._pending.keys()) if tid == task_id]

    # ---------- 记忆 ----------

    def remember(self, task_id: str, key: str, command: str = "") -> None:
        """用户点了「总是允许」时记下两样东西 ✓（2026-10-11 平台闸修复 ③）。

        ① `key`（**这一类的记忆键** ✓）—— 只接受**本来就该被记住的那几类** ✗✓
        ② `command` 的**原文指纹**（**新** ✓）—— 只覆盖"**同一条命令**、**同一个任务**" ✓。

        ★ 为什么必须补 ②（实测）：
          真实任务的命令**大多数都带一点结构**（`cd X && node -e …` ✓）⇒ ① 几乎从不生效 ✗
          ⇒ 用户点了「总是允许」、下一条几乎一样的命令又被问 ✓（审计账里 11 次 `always` ✓）
          ⇒ 这次要治的"44 次审批门"有相当一部分就是这么来的 ✓。

        ★ ① 的**可记忆前缀白名单**（本次一并收紧 ✓ —— 原来只排除了 `struct:` ✗）：
          · `verb:`     —— "rm 这类以后都别问" ✓ 用户点它时看的就是那个危险动词 ✓
          · `outside:`  —— "这个目录以后都别问" ✓ 用户点它时看的就是那个路径 ✓
          · `confirm:`  —— 变更前确认模式（那是模式语义 ✓ 交给它自己管 ✓）
          · `protect:` / `allowall:` —— 硬保护与"全部允许"（它们各自在更前面判 ✓
            轮不到这里 ✓ 列出来是为了"看到就懂"✓）
          **其余一律只记原文指纹** ✗ —— 尤其是 `net:`（联网/内联代码）与
          `struct:`（结构嫌疑）：放行它们的**一整类** = 从此任意联网/任意代码免审批 ✗
          （审计 P1 那条老规矩 ✓ 本次实测发现 `net:` 曾经漏在门外 ✗ 已补 ✓）。

        ★ 安全边界（三条都留着 ✓）：
          · **只限本任务** ✓（跨任务照旧走 `forever` 名单 ✓ 它的三道边界一个不动 ✓）
          · **要一模一样** ✗（换一个字就是新命令 ⇒ 照问 ✓）
          · **绕不过应用目录硬保护** ✓（它在 `check` 里判在记忆**之前** ✓）
        """
        if key.startswith(_REMEMBERABLE_KEY_PREFIXES):
            bucket = self._always.setdefault(task_id, set())
            bucket.add(key)
            self._trim_task_memory(bucket)
        self._remember_command(task_id, command)

    def _remember_command(self, task_id: str, command: str) -> None:
        fp = self._command_fingerprint(command)
        if not fp:
            return
        bucket = self._always.setdefault(task_id, set())
        bucket.add("cmd:" + fp)
        self._trim_task_memory(bucket)

    @staticmethod
    def _trim_task_memory(bucket: set[str]) -> None:
        """**有上限** ✓ —— 长任务会把命令指纹一直堆下去 ✗（本项目一贯的"内存里的东西都得有主"✓）。

        只丢**命令指纹**（`cmd:` 前缀 ✓ 最老的那些先丢 ✓）；
        按类型的记忆键（`verb:rm` / `outside:…` …）**一个都不丢** ✓
        —— 那些是用户点过的语义授权 ✓ 数量本来就有限（每种一条 ✓）。
        """
        caps = [k for k in bucket if k.startswith("cmd:")]
        if len(caps) <= _MAX_CMD_MEMORY:
            return
        # set 无序 ⇒ 用"先来的先留"没法做到精确 LRU ✓；这里按**字典序**稳定地丢一部分 ✓
        # （目的只是"不无限涨" ✓ 不是精确淘汰 ✓ —— 写在注释里，免得下一个人误会 ✓）
        for k in sorted(caps)[:_MAX_CMD_MEMORY // 2]:
            bucket.discard(k)

    # ---------- 硬保护：应用自己的目录不许被任务碰 ----------

    @staticmethod
    def _protected_dirs() -> list[str]:
        """应用关键目录（绝对路径小写，正斜杠）。工作区不在其中（它本来就允许写）。"""
        here = Path(__file__).resolve()               # <app>/approval.py
        app_dir = here.parent                          # <app>
        backend = app_dir.parent                       # <backend>
        repo = backend.parent                          # <repo>
        cands = [app_dir, backend / ".venv", backend / "data" / "tasks",
                 repo / "scripts", repo / "frontend", backend / "config.json",
                 repo / "config.json"]
        return [str(c).replace("\\", "/").lower() for c in cands]

    # 破坏性动作（删/移/改名/写进去…）。只有它们**配上**受保护路径才要人工确认；
    # ★ 光是"调用"受保护的解释器不算 —— 那正是我们自己在环境交底里教它们的用法 ✓
    #   （2026-10-06 实测：不加这条，验收任务每跑一次 `…\.venv\Scripts\python.exe xxx.py` 都要问一次 ✗）
    _DESTRUCTIVE = re.compile(
        r"(?i)(^|[\s;&|(])(rm|del|erase|rmdir|rd|move|mv|ren|rename|copy|cp|xcopy|robocopy|"
        r"remove-item|move-item|copy-item|set-content|out-file|tee|truncate|dd|shred|format)"
        r"(\s|$)|>>?\s*\S*(\.venv|/app/|\\app\\|/scripts/|\\scripts\\)")

    @staticmethod
    def _iterpreter_only(path_raw: str, command: str) -> bool:
        """`path_raw` 是不是**只是在被当作解释器执行**（且命令里没有破坏性动作）。

        判据（窄）：
        · 这个路径本身就是解释器（`python*.exe` / `node` / `bash` / `sh` …）
        · 命令里它的出现方式像"执行"（出现在开头、或在 `&&`/`;`/`|`/`(` 之后）
        · 命令里**没有**破坏性动词（删/移/改名/写文件）

        这样"用后端 python 跑工作区里的脚本"就不用问 ✓；
        而"删掉这个 python.exe"仍然被应用目录硬保护拦下 ✓。

        ★ 2026-10-11（审批降噪 · 真机实测）：两个**没认出来**的写法补上 ✓ ——
          · 路径被**引号**包着（`"/d/…/python.exe" tools/tts.py` ✓ 路径里有空格时必然这么写 ✓）
          · 路径存在**变量**里（`V="/d/…/python.exe"` 之后 `"$V" -V` ✓）
          旧写法拿"`_ABS_PATH_RE` 扫出来的那一段"当路径 ✗ ⇒ 带引号时扫出来的是
          `"/d/…/python.exe`（少个尾巴引号 ✓ 还带着引号 ✓）⇒ `basename` 认不出解释器 ✗
          ⇒ 真机 84 条里有 **20 条**反复弹门（"用后端 python 跑工作区脚本"这一件事 ✓）。
        """
        low_path = str(path_raw).replace("\\", "/").lower().strip("\"'` ")
        # ★ 变量形态（`"$V"`）：换成它被静态赋的那个值再认 ✓（认不出就照旧判 ✗）
        ref = _var_only_reference(path_raw)
        if ref:
            low_path = str(ref).replace("\\", "/").lower().strip("\"'` ")
        head = low_path.rsplit("/", 1)[-1]
        if not (head.startswith("python") or head in ("node", "node.exe", "bash", "sh", "zsh",
                                                      "pwsh", "powershell", "cmd", "cmd.exe")):
            return False
        if ApprovalManager._DESTRUCTIVE.search(str(command)):
            return False
        # 出现在"命令开头"或"分隔符之后" ⇒ 当执行用
        try:
            # ★ 路径比较前**统一分隔符**（命令里通常是反斜杠，键是正斜杠 —— 本班就踩了这个：
            #   不归一化的话 find 永远找不到，判据形同虚设 ✗）
            c = str(command).replace("\\", "/").lower()
        except Exception:
            return False
        for sep in (" ", "\t", "&&", "||", ";", "|", "(", "&"):
            idx = c.find(low_path)
            if idx == -1:
                continue
            # 前文去掉引号**再**去空白再判：`"D:\...\python.exe" x.py` 前文只有一个引号 ✓；
            # 而 `cd /w && "…python.exe"` 前文是 `cd /w &&` ✓（先 strip 空白会把引号留在末尾 ✗，本班踩过）
            # ★ 2026-10-11：**换行也是分隔符** ✗ —— 真机那批命令全是
            #   `cd /w\n"/d/…/python.exe" script.py` 这种"多行脚本"写法 ✓
            #   （`.strip()` 会把结尾的换行吃掉 ⇒ 前文成了 `cd /w` ⇒ 判"不是执行"✗）。
            before = c[:idx].strip("\"'").rstrip(" \t")       # 只去行内空白，保留换行判据 ✓
            if (not before
                    or before.endswith(("&&", "||", ";", "|", "&", "(", "\n", "\r"))):
                return True
        return False

    def _protected_hit(self, command: str, is_inside: Callable[[str], bool]) -> str:
        """命令里是否**破坏性地**碰到"应用自身目录"、且不在本任务工作区 ⇒ 返回命中的那一段。

        ★ 实测教训（2026-10-06 夜）：任务在宿主上跑真命令，`rm -rf` 一类的清理命令写歪了
          就能删到装好的环境（venv 里的 python.exe 就是这么没的 ✗）。
        ★ 但**不能只要提到就拦**：任务正常也要用 `<backend>/.venv/Scripts/python.exe` 跑脚本 ✓
          （那是环境交底教它们的用法），一拦就变成"每跑一次问一次" ✗。
          所以判据是**破坏性动词 + 受保护路径同时出现** —— 既保住安全，又不误伤日常 ✓。
        """
        try:
            low = str(command).replace("\\", "/").lower()
        except Exception:
            return ""
        if not self._DESTRUCTIVE.search(str(command)):
            return ""
        for d in self._protected_dirs():
            if not d or d not in low:
                continue
            try:
                if is_inside(d):
                    continue          # 在工作区里（工作区本身就在 data 目录下）⇒ 正常
            except Exception:
                pass
            return d
        return ""

    def allow_all(self, task_id: str) -> None:
        """「本任务全部允许」：这个任务的命令不再逐条询问（**只在内存，重启失效**）。

        与 `remember()` 的区别：`remember` 按命令类型记忆，而且**故意拒绝记忆**
        结构性命令（`$()`/heredoc/eval —— 审计 P1：一次豁免 = 整类任意执行）。
        实测后果：一个写脚本的任务**每条命令都要人点一次**（一晚 25 次 ✗）。
        这里是用户在**具体任务**上的明确授权：照样留审计事件，只是不打断人。

        ★ 但**应用自身目录的硬保护不受它影响**（见 `_protected_hit`）——
          那条在任何授权之前就先判了 ✓。
        """
        if task_id:
            self._allow_all.add(str(task_id))

    def allow_all_tasks(self) -> list[str]:
        """当前开着『全部允许』的任务（给界面/接口展示与收回用）。"""
        return sorted(self._allow_all)

    def revoke_all(self, task_id: str) -> None:
        """收回『全部允许』（用户随时能关掉）。"""
        self._allow_all.discard(str(task_id))

    def forget_task(self, task_id: str) -> None:
        """任务结束清理"总是允许"记忆（审计 §8.9：此前按 task_id 只增不减）。"""
        self._always.pop(task_id, None)
