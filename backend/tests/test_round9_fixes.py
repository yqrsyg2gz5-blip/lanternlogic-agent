# -*- coding: utf-8 -*-
"""第九轮复验修复的**回退锚点**（⑨：每处修复配一个"回滚必红"的测试）。

审查第九轮的结论：多条"已修"没有锚点——把实现改回去测试仍绿，等于没修。
本文件的每个用例都对应一处具体修复，并已用"临时回滚实现"实测变红：

  ① xargs + perl/ruby/sed -i  → 回滚 xargs 阶段 → test_xargs_inplace_asks 红
  ② ②b ln/mklink 双实现统一   → 回滚为"判全部 cands" → test_ln_* 红
  ②b cp/install -t 目标位     → 回滚为目标位取 cands[-1] → test_cp_dash_t_* 红
  ③ 目录目标=存在（is_file 回退）→ 回滚 is_file → test_dir_target_asks 红
  ③ _path_is_inside fail-open → 回滚 return True → test_outside_write_asks 红
  ⑥ 容器 CLI 按形态           → 回滚为只认 docker → test_container_runtimes 红
  ⑦ tee `< src` 切输入源      → 回滚 ② 面不切 → test_tee_stdin_source 红
  ⑦ 裸 env 放行               → 回滚 env 特判 → test_bare_env_passes 红
  ⑦ tee /dev/null 放行        → 回滚 xargs 阶段门 → test_tee_devnull_passes 红
"""
from __future__ import annotations

from pathlib import Path

import pathlib

import pytest

from app.approval import ApprovalManager

from tests.test_approval_capability_matrix import (  # noqa: E402
    REQ, _is_ask, _make_workspace, _real_run,
)


@pytest.fixture()
def ws():
    _ws, _out = _make_workspace()
    return _ws


def _run_cmd(ws, cmd, *, in_sandbox=False, scripts=None, network_disabled=False):
    """用**真实** TaskRun 跑一次审批——三个回调全部生产实现。

    十二轮 🔴5① 修正：script_reader 此前是 stub（scripts={} 恒 None），
    会落进 script-unreadable 兜底——替真规则打掩护（stub ASK 而生产 PASS）。
    现在 scripts 写进真实工作区，reader=run._read_script_for_scan（生产）。
    """
    run = _real_run(ws)
    for name, content in (scripts or {}).items():
        target = pathlib.Path(run.workdir) / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return run.approval.check(
        "task_20261003_r9", cmd, REQ, run._path_is_inside,
        in_sandbox=in_sandbox,
        network_disabled=network_disabled,
        target_exists=run._target_exists,
        script_reader=run._read_script_for_scan,
    )


# ═══ ① xargs + perl/ruby/sed -i（容器实测真改写交付物） ═══

# (命令, 必须命中的判定键前缀)——key 断言防止"兜底 ASK 冒充真规则"：
# 十二轮 🔴5①：stub reader 时代，删掉真规则后测试仍经 script-unreadable 兜底变绿；
# 换生产 reader 后，生产路径上唯一拦截就是 ②b xargs 阶段的真规则——
# 锚点必须断言 key，规则被删时（生产 reader 下无兜底）必红。
XARGS_INPLACE = [
    ("echo f.txt | xargs perl -i -pe s/a/b/", "struct:inplace:perl"),
    ("find . -type f | xargs ruby -i -pe puts", "struct:inplace:ruby"),
    # sed 另有更早触发的真规则 struct:sed-i（结构嫌疑面），锚定它
    ("find . -type f | xargs sed -i s/a/b/", "struct:sed-i"),
    ("find . -type f | xargs truncate -s 0", "struct:overwrite:truncate"),
    ("find . -type f | xargs cp /dev/null", "struct:overwrite:cp"),
]


@pytest.mark.parametrize("cmd,want_key", XARGS_INPLACE)
def test_xargs_inplace_asks(ws, cmd, want_key):
    """回滚锚点（十二轮 🔴5① 加固）：xargs 阶段规则被删 → 生产 reader 下无兜底 → 必红。"""
    v = _run_cmd(ws, cmd)
    assert _is_ask(v), f"xargs 喂入的就地改写/破坏写必须 ask：{cmd} → {v}"
    assert v.key.startswith(want_key), (
        f"{cmd} 命中的是兜底（{v.key}）而不是真规则（{want_key}）——锚点失效：{v}")


# ═══ ②b ln / mklink 双实现统一（名字位：ln 末位 / mklink 首位） ═══

def test_ln_new_link_name_passes(ws):
    """`ln -s 已有源 新链接名`：源永不被写——回滚为"判全部 cands"时源存在→误拦，本测试红。"""
    v = _run_cmd(ws, "ln -s exist.txt link_new")
    assert not _is_ask(v), f"ln 建新链接名不应拦（源不会被写）：{v}"


def test_ln_existing_name_asks(ws):
    """`ln -s 源 已存在名`：目标名被占用 → ask。回滚为"只看 cands[-1] 之外"的旧实现仍红/绿不定，但删掉 ln 分支必红。"""
    v = _run_cmd(ws, "ln -s exist.txt deliverable.txt")
    assert _is_ask(v), f"ln 覆盖已存在的链接名必须 ask：{v}"


def test_mklink_name_first_asks(ws):
    """cmd 形态 `mklink Link Target`：命名位在**首位**——回滚为 ln 式末位/全判，deliverable.txt（第二位=源）会被误拦或 link 名漏判，本测试红。"""
    v = _run_cmd(ws, "mklink deliverable.txt exist.txt")
    assert _is_ask(v), f"mklink 链接名已存在必须 ask：{v}"


def test_mklink_new_name_passes(ws):
    v = _run_cmd(ws, "mklink link_new exist.txt")
    assert not _is_ask(v), f"mklink 建新链接名不应拦：{v}"


# ═══ ②b cp/install/mv -t 目标位（落点 DIR/basename(源) 而非源本身） ═══

@pytest.mark.parametrize("cmd", [
    "cp -t backup exist.txt",
    "install -t backup exist.txt",
    "mv -t backup exist.txt",
    "cp --target-directory=backup exist.txt",
])
def test_cp_dash_t_existing_landing_asks(ws, cmd):
    """`-t backup src` 的落点是 backup/exist.txt（已存在）→ ask。回滚为目标位取 cands[-1]（= exist.txt 源）仍 ask——需用"新落点"用例定义真锚点（见下一个）。"""
    (ws / "backup").mkdir(exist_ok=True)
    (ws / "backup" / "exist.txt").write_text("OLD", encoding="utf-8")
    v = _run_cmd(ws, cmd)
    assert _is_ask(v), f"-t 落点已存在必须 ask：{cmd} → {v}"


def test_cp_dash_t_new_landing_passes(ws):
    """`-t newdir src` 落点不存在 → 放行。**真锚点**：回滚取 cands[-1]（=源 exist.txt，存在）→ 误拦变红。"""
    (ws / "newdir").mkdir(exist_ok=True)
    v = _run_cmd(ws, "cp -t newdir exist.txt")
    assert not _is_ask(v), f"-t 新落点不应拦（源存在≠目标存在）：{v}"


def test_cp_dash_t_fed_by_xargs_fail_closed(ws):
    """源由管道喂入（xargs cp -t dir）→ 落点不可判定 → fail-closed。"""
    (ws / "backup").mkdir(exist_ok=True)
    v = _run_cmd(ws, "find . | xargs cp -t backup")
    assert _is_ask(v), f"xargs cp -t 无可见源必须 fail-closed：{v}"


# ═══ ③ 回退锚点二：目录目标 = 存在（is_file 回退） ═══

def test_dir_target_asks(ws):
    """目录被写动词指向也算"存在"→ ask。

    真锚点：把 `_target_exists` 的 `real.exists()` 回滚成 `real.is_file()` →
    目录返回 False = "创建新文件" → 放行 → 本测试红（第六轮就修过的回归点）。
    """
    (ws / "ddir").mkdir(exist_ok=True)
    v = _run_cmd(ws, "truncate -s 0 ddir")
    assert _is_ask(v), f"目录目标必须按'已存在'拦：{v}"
    v2 = _run_cmd(ws, "cp /dev/null ddir")
    assert _is_ask(v2), f"cp 到已存在目录必须 ask：{v2}"


# ═══ ③ 回退锚点三：_path_is_inside 不许 fail-open ═══

def test_outside_write_asks(ws):
    """工作区外的写目标 → ask。

    真锚点：把 `_path_is_inside` 的 except 分支改成 `return True`（fail-open）→
    越界路径被当"工作区内" → 放行 → 本测试红（审查第九轮点名的不存在锚点）。
    """
    outside = Path(ws.parent) / "definitely-outside-target.txt"
    outside.write_text("VICTIM", encoding="utf-8")
    v = _run_cmd(ws, f"truncate -s 0 {outside}")
    assert _is_ask(v), f"工作区外写目标必须 ask：{v}"


# ═══ ⑥ 容器 CLI 按形态（podman/nerdctl/ctr/compose 带全局选项） ═══

CONTAINER_ASKS = [
    "podman run --rm img rm -rf deliverable.txt",
    "nerdctl run --rm img truncate -s 0 deliverable.txt",
    "ctr run --rm img c1 rm -rf deliverable.txt",
    "ctr task exec --exec-id 1 c1 rm -rf deliverable.txt",
    "docker compose --profile prod run --rm app rm -rf /w/deliverable.txt",
    "docker compose -f docker-compose.yml run --rm app rm -rf deliverable.txt",
    "docker compose --profile dev exec -T app rm -rf deliverable.txt",
]
CONTAINER_PASSES = [
    "podman --version",
    "docker compose --profile dev ps",
    "docker compose -f x.yml config",
    "docker compose --profile dev logs",
    "ctr images ls",
]


@pytest.mark.parametrize("cmd", CONTAINER_ASKS)
def test_container_runtimes_ask(ws, cmd):
    """回滚锚点：只认 docker 头部/固定 toks[2] 的旧实现下，这些全放行 → 红。"""
    v = _run_cmd(ws, cmd)
    assert _is_ask(v), f"容器换入口的危险命令必须 ask：{cmd} → {v}"


@pytest.mark.parametrize("cmd", CONTAINER_PASSES)
def test_container_observe_passes(ws, cmd):
    v = _run_cmd(ws, cmd)
    assert not _is_ask(v), f"容器观察类命令不应拦：{cmd} → {v}"


# ═══ ⑦ tee `< src` 输入源 / tee /dev/null / 裸 env ═══

def test_tee_stdin_source_new_file_passes(ws):
    """`tee out.txt < src.txt`：src 是输入源不是写目标，out 新文件 → 放行。

    回滚锚点：② 面若不切 `<`，cands 里 src.txt（存在）被当写目标 → 误拦 → 红。
    """
    v = _run_cmd(ws, "tee out.txt < exist.txt")
    assert not _is_ask(v), f"tee 新文件 + 输入源存在不应误拦：{v}"


def test_tee_stdin_source_existing_target_asks(ws):
    """`tee deliverable.txt < exist.txt`：写目标是已存在的 deliverable → ask（真风险不放过）。"""
    v = _run_cmd(ws, "tee deliverable.txt < exist.txt")
    assert _is_ask(v), f"tee 覆写已有文件必须 ask：{v}"


def test_tee_devnull_passes(ws):
    """`tee /dev/null < src`（纯丢弃）/ `echo | tee /dev/null` → 放行。

    回滚锚点：xargs 阶段若不设门（对全部命令生效），这两条被"目标由管道喂入"误拦 → 红。
    """
    for cmd in ("tee /dev/null < exist.txt", "echo hi | tee /dev/null", "tee /dev/null"):
        v = _run_cmd(ws, cmd)
        assert not _is_ask(v), f"tee 到空设备不应拦：{cmd} → {v}"


def test_bare_env_passes(ws):
    """裸 env = 打印环境变量（只读）→ 放行；env 带内层命令仍拦。"""
    v = _run_cmd(ws, "env")
    assert not _is_ask(v), f"裸 env 只读不应拦：{v}"
    v2 = _run_cmd(ws, "env | grep PATH")
    assert not _is_ask(v2), f"env 管道查询不应拦：{v2}"
    v3 = _run_cmd(ws, "env VAR=1 rm -rf x")
    assert _is_ask(v3), f"env 带内层命令必须拦：{v3}"


# ═══ ④ A4 快照敏感文件排除（扩展名 + 精确名 + 前缀，第十轮 B1/B2 重做） ═══

# 必须**排除**（不入快照）——上轮点名的 11 条 + 第九轮 18 条清单 + B2 补漏 3 条，
# 全部收进本列表；报告口径一律用这里的条数，不再用自评数。
SENSITIVE_MUST_EXCLUDE = [
    # 第九轮点名（11 条中进入本清单的部分）
    ".env.production", ".env.local", "credentials.json", "secrets.yaml",
    "API.TOKENS", "db.PASSWORDS", "my_private_key.txt", "appsettings.token",
    "server.key", "cert.pem", "id_rsa",
    # 第九轮测试清单其余条目
    ".env", "id_ed25519.pub", "config.json", "settings.json", ".npmrc", ".netrc", ".pgpass",
    # 第十轮 B2 补漏（上轮仍漏 3 条）
    "settings.local.json", "service-account.json", ".htpasswd",
    # 第十二轮 🔴6 补漏（审查实测 15 条；嵌套那条按 basename 命中）
    "production.env", "prod.env", "dev.env", "staging.env",
    "secret.env", "secret.env.local",
    ".netrc.bak", "auth.json", "token.json", "my_credentials.json",
    "api_tokens.json", "passwords.txt", "kubeconfig", "APPLICATION.PROPERTIES",
    # 第十四轮 🔴1：.env 族全变体表（十三轮回归的那批——此前不在清单才被全绿掩盖）
    ".env.development", ".env.test", ".env.prod", ".env.qa", ".env.ci",
    ".env.uat", ".env.preprod", "app.env.development", "secret.env.prod",
    "config/.env.test",
    # 第十三轮 🔴3：备份后缀族（剥离 .bak/.old/.1/~ 后按原名判，全部应排除）
    "auth.json.bak", "token.json.bak", "credentials.json.bak", "kubeconfig.bak",
    ".npmrc.bak", "secrets.yaml.bak", "api_tokens.yml.bak",
    "my_credentials.json.old", "auth.json.1", "token.json~",
    "config.json.bak",
    # 第十四轮 🟡4：裸名/同族补全（passwd/pwd/secret/secrets/token/tokens/auth）
    "passwd", "passwd.txt", "pwd", "pwd.txt", "secret", "secrets", "token",
    "tokens", "auth",
]
# 必须**入快照**（误杀防护，第十轮 B1）——审查实测被裸子串误杀的文件 + 原常规用例
NORMAL_MUST_INCLUDE = [
    # 审查第十轮 B1 点名（曾被 "secret"/"credential"/".env"/"token"/"password"/
    # "private" 子串误杀）
    "secret-santa.md", "my-credentials-guide.md", ".envrc", "tokens.md",
    "password-policy.md", "private.md", "password_reset.py", "tokenizer.py",
    "my.settings.json",  # config.json.bak 已按十三轮口径移入排除表（备份按原名判）
    # 原常规用例（config.json.bak 已按十三轮 🔴3 统一口径移入排除表：
    # 【备份一律按原名判】——不再"一半排一半留"）
    "normal.txt", "README.md", "report.csv",
    # 第十二轮：新增规则的误杀防护（auth 词根 × .md 不配对 → 文档安全）
    "auth-guide.md", "production-notes.md",
    # 第十三轮 🔴2：配对扩展名收窄后的误杀防护（.txt 文档不再杀）
    "secret-santa.txt", "auth-notes.txt", "token-notes.txt",
    "password-notes.txt", "README.env.md",
    # 第十四轮 🔴3：词尾边界的误杀防护（子串匹配会杀掉这七个普通词文件）
    "secretary.json", "authentication.json", "tokenizer.ini", "auth.ini",
    "token_budget.conf", "secret.ini", "secrets.conf",
]


def test_snapshot_exclusion_rules(tmp_path):
    """排除与误杀放进**同一个**参数化断言：两边都必须过。

    回滚锚点：
    · 回滚成裸子串匹配（第九轮版）→ NORMAL_MUST_INCLUDE 大量被误杀 → 红
    · 回滚成精确名匹配（第八轮版）→ SENSITIVE_MUST_EXCLUDE 的 .env.production 等漏网 → 红
    """
    from app.bus import EventBus
    from app.executors.local import LocalExecutor
    from app.loop import TaskRun
    from app.schemas import TaskSummary
    from app.store import FsStore
    from types import SimpleNamespace

    store = FsStore(tmp_path / "data")
    run = TaskRun(
        TaskSummary(id="task_20261003_sn9", title="snap", created_at="2026-10-03T00:00:00Z",
                    updated_at="2026-10-03T00:00:00Z"),
        "快照", store=store, bus=EventBus(), provider=None,
        executor=LocalExecutor(SimpleNamespace(
            type="local", workspace_root=str(tmp_path), allowed_dirs=[str(tmp_path)],
            timeout_seconds=30, sandbox="off", shell=None, cfg_shell="", search_url="",
            searxng_url="", browser_channel="msedge", comfyui_url="http://127.0.0.1:9",
            image_checkpoint="x", sandbox_image="python:3.12-slim",
            sandbox_network="none", sandbox_memory="512m",
        )),
        approval=ApprovalManager(), tools=[], max_iterations=1, timeout_seconds=1.0,
        approval_required=[], on_finish=lambda r: None,
    )
    wd = run.workdir
    for name in SENSITIVE_MUST_EXCLUDE + NORMAL_MUST_INCLUDE:
        f = wd / name
        f.parent.mkdir(parents=True, exist_ok=True)  # 支持 "config/.env.test" 等带目录的变体
        f.write_text("x", encoding="utf-8")
    (wd / "deep" / "nested").mkdir(parents=True, exist_ok=True)
    (wd / "deep" / "nested" / "secret.env").write_text("x", encoding="utf-8")
    run._snapshot_workspace()
    snaps = sorted(store.snapshots_dir(run.task.id).iterdir())
    # 十四轮教训：_snapshot_workspace 外层 except 会静默吞掉规则里的运行时错误
    # （本轮 skip_exts 丢失 → NameError → 快照变空）——这里必须对"零复制"亮红灯
    assert snaps, "快照目录为空——快照根本没建（规则抛错被静默吞掉？）"
    # 十五轮 🟠2：双集合断言——裸名（兼容旧清单）+ 相对路径（带目录条目
    # "config/.env.test" 用 basename 比对永远命中不了，结构性盲区）
    copied_names = {p.name for p in snaps[-1].rglob("*") if p.is_file()}
    copied_rel = {str(p.relative_to(snaps[-1])).replace("\\", "/")
                  for p in snaps[-1].rglob("*") if p.is_file()}
    all_copied = copied_names | copied_rel
    leaked = [n for n in SENSITIVE_MUST_EXCLUDE if n in all_copied]
    killed = [n for n in NORMAL_MUST_INCLUDE if n not in all_copied]
    assert not leaked, f"敏感文件进入了快照（{len(SENSITIVE_MUST_EXCLUDE)} 条清单）：{leaked}"
    assert not killed, f"正常文件被误杀（{len(NORMAL_MUST_INCLUDE)} 条清单）：{killed}"

# ═══ 十二轮 🔴5③：SRC… DIR/ 尾斜杠目录 + ln -t/--target-directory + install -d ═══

DIR_FORM_ALLOW = [
    "cp a.txt destdir/",
    "cp *.txt destdir/",
    "cp -r srcdir destdir/",
    "mv a.txt destdir/",
    "install -d destdir",
    "ln -t destdir a.txt",
    "ln --target-directory=destdir a.txt",
]


@pytest.mark.parametrize("cmd", DIR_FORM_ALLOW)
def test_dir_form_landings_allow(ws, cmd):
    """`SRC… DIR/` 的落点是 DIR/basename(源)——源/目录存在不再是拦截理由。

    回滚锚点：恢复旧实现（目标位取 cands[-1]=「dir/」本身，目录存在→True）时
    本参数化整组变红。
    """
    (ws / "destdir").mkdir(exist_ok=True)
    (ws / "a.txt").write_text("X", encoding="utf-8")
    (ws / "srcdir").mkdir(exist_ok=True)
    v = _run_cmd(ws, cmd)
    assert not _is_ask(v), f"目录落点形态不应拦（落点不存在）：{cmd} → {v}"


def test_dir_form_landings_asks_when_landing_exists(ws):
    """落点 dir/basename(源) 已存在 → 照样 ask（fix 不是放水）。"""
    (ws / "wsdir").mkdir(exist_ok=True)
    (ws / "wsdir" / "a.txt").write_text("OLD", encoding="utf-8")
    (ws / "a.txt").write_text("X", encoding="utf-8")
    for cmd in ("ln -t wsdir a.txt", "ln --target-directory=wsdir a.txt", "cp a.txt wsdir/"):
        v = _run_cmd(ws, cmd)
        assert _is_ask(v), f"落点已存在必须 ask：{cmd} → {v}"


# 十三轮 🔴1：三种模式全参数化——宿主 / 沙箱 / 沙箱+断网（生产默认组合）。
# compose 生命周期判定已从 net 面独立（_container_lifecycle），断网不再放行。
_LIFE_MODES = [
    (False, False),  # 宿主
    (True, False),   # 沙箱
    (True, True),    # 沙箱+断网（sandbox_network="none" = 生产默认）
]


# 二十轮 🔴0：Docker 规范形态（对象名+动词）全表——此前 toks[1] 写死导致整族绕过
_BYPASS_FAMILY = [
    # 二十/二十一轮：VAR 前缀 + 包装器形态（审查员实测绕过）
    "DOCKER_HOST=tcp://1.2.3.4:2375 docker image pull alpine",
    "DOCKER_HOST=x sudo docker system prune -a",
    "FOO= docker system prune -a",
    "FOO='a b' docker system prune -a",
    "docker image pull alpine",
    "docker container run --rm alpine ls",
    "docker system prune -a",
    "docker volume rm mydata",
    "docker rmi alpine",
    "docker rm -f c1",
    "podman system prune -a",
    "ctr images pull alpine",
    # 生命周期（原有）
    "docker compose up",
    "docker compose build",
    "docker compose pull",
    "docker compose --profile dev up -d",
    "docker compose --profile prod run --rm app ls",
    "docker run --rm img rm -rf deliverable.txt",
    "podman pull alpine",
]
# 观察类（必须 PASS）——回归哨兵
_OBSERVE_FAMILY = [
    "docker ps", "docker images", "docker system df",
    "docker container ls", "docker image ls", "docker volume ls",
    "ctr images ls",
    # 二十四轮：白名单 5 条误拦修复哨兵
    "docker buildx du", "docker stack services mystack", "docker swarm ca",
    "docker context show", "docker compose alpha dry-run",
    # 二十五轮：`ctr -v images pull alpine` 撤出观察类——-v 前缀形态
    # 一律不再豁免（挪入 _FLAG_PREFIX_ASKS）
]


# 二十五轮 🔴1：帮助/版本 flag 前缀 + 子命令——真机实测（docker 29.8.0，
# 2026-10-03，仅用安全观察子命令）：`docker -v ps` / `--version ps` /
# `-v system df` → daemon 连接错误 = 子命令【真执行】（-v/--version 不阻断）；
# `docker --help ps` / `-h ps` → 打印主题帮助不执行；compose 同族
# `docker compose -v ps` → "no configuration file provided" = 真执行。
# 拍板：flag 之后只要还有子命令一律 ASK（9 条 docker × 三模式 + compose 同族）。
_FLAG_PREFIX_ASKS = [
    "docker -v ps",
    "docker --version ps",
    "docker -h ps",
    "docker --help ps",
    "docker -v images",
    "docker --help images",
    "docker -v version",
    "docker --version version",
    "docker -v system df",
    "ctr -v images pull alpine",
    "docker compose -v ps",
    "docker compose --version ps",
]
# 裸自述（flag 后无子命令）放行哨兵——实测均只打印版本/帮助，防过度拦截
_FLAG_PREFIX_PASSES = [
    "docker -v",
    "docker --version",
    "docker --help",
    "docker -h",
    "docker --debug -h",
    "docker --context default -v",
    "docker -v --help",
    "podman --version",
    "docker compose -v",
    "docker compose --version",
]

# 二十五轮 🔴2：fail-closed 落到格式识别入口——剥完包装器取到 head 的那一刻判。
# 表外 head（不在 _WRAPPER_DEF / _CONTAINER_CLIS / 无关命令白名单）+ 容器 CLI
# ⇒ 未知包装器 → ASK（此前 16/16 表外包装器零审批放行）。
_UNKNOWN_WRAPPER_ASKS = [
    "zzznotreal docker system prune -a",
    "zzznotreal docker ps",
    "fooexec docker run --rm img rm -rf deliverable.txt",
    "VAR=1 zzzwrap podman system prune -a",
    "timeout 5 zzzwrap docker system prune -a",
    # 二十六轮第2处（验证员硬要求：锚点必须单因可归）：这两条【只有入口能拦】
    # ——实测（本班自验）：入口回滚后三模式全 PASS（net 面不兜底），
    # ASK→PASS 翻转 100% 归因入口门；system prune 类 3 条回滚后会被
    # net 面兜住仍 ASK，不能单独归因。
    "zzznotreal docker --help",
    "zzznotreal docker info",
]
# "完全无关普通命令"白名单哨兵——head 后出现容器 CLI 字样只是文本操作
_UNKNOWN_WRAPPER_PASSES = [
    "grep docker build.log",
    "cat docker-compose.yml",
    "echo docker ps",
    "git log docker",
    "ls docker",
]

# 二十五轮 🔴3（回滚锚点配套）：透明包装/VAR 前缀/无值全局 flag 的
# 【观察类放行】哨兵——redgreen_check.py 把对应保护拆掉时本组必红（变 ASK）：
#   · timeout/nice 透明包装（argjump 参数跳过）
#   · flock 锁文件（1 个位置参数跳过）
#   · VAR= 前缀与引号含空格残片剥离
#   · --debug/-D 无值单跳（真机实测：`docker --debug ps` 正常执行）
_WRAPPER_VAR_PASSES = [
    "timeout 5 docker ps",
    "nice -n 10 docker ps",
    "flock /tmp/l.lock docker ps",
    "VAR=x docker ps",
    "FOO='a b' docker ps",
    "docker --debug ps",
    "docker -D ps",
]


@pytest.mark.parametrize("in_sandbox,network_disabled", _LIFE_MODES)
@pytest.mark.parametrize("cmd", _BYPASS_FAMILY)
def test_compose_lifecycle_decided_ask(ws, cmd, in_sandbox, network_disabled):
    """拍板固定为回归锚点（二十轮 🔴0 扩表）：容器生命周期/破坏类恒 ASK
    （对象名形态 image pull / container run / system prune / volume rm 全覆盖），
    与 sandbox_network 无关——15 命令 × 3 模式 = 45 组合全部必问。"""
    v = _run_cmd(ws, cmd, in_sandbox=in_sandbox, network_disabled=network_disabled)
    assert _is_ask(v), (
        f"容器生命周期/破坏类必须 ask（模式：沙箱={in_sandbox} 断网={network_disabled}）：{cmd} → {v}")
    # key 容差：verb:rm 会先命中（危险动词面）——但容器面兜住记忆链
    # （十七轮验证员实测：remember('verb:rm') 后第二次仍 ASK，key 变 struct:docker-run）
    if not (v.key.startswith("struct:") or v.key.startswith("verb:")):
        assert False, f"生命周期键必须为 struct:*/verb:*：{v.key}"


@pytest.mark.parametrize("in_sandbox,network_disabled", _LIFE_MODES)
@pytest.mark.parametrize("cmd", _OBSERVE_FAMILY)
def test_container_observe_family_pass(ws, cmd, in_sandbox, network_disabled):
    """观察类（ps/images/df/ls）三模式全放行——防过度拦截的回归哨兵。"""
    v = _run_cmd(ws, cmd, in_sandbox=in_sandbox, network_disabled=network_disabled)
    assert not _is_ask(v), f"容器观察类不应拦：{cmd} → {v}"


@pytest.mark.parametrize("in_sandbox,network_disabled", _LIFE_MODES)
@pytest.mark.parametrize("cmd", _FLAG_PREFIX_ASKS)
def test_flag_prefix_with_subcommand_asks(ws, cmd, in_sandbox, network_disabled):
    """二十五轮 🔴1：帮助/版本 flag 前缀 + 子命令——真机实测 flag 语义
    （-v/--version 不阻断执行）后拍板：一律 ASK，与模式无关。"""
    v = _run_cmd(ws, cmd, in_sandbox=in_sandbox, network_disabled=network_disabled)
    assert _is_ask(v), f"flag 前缀 + 子命令必须 ask（沙箱={in_sandbox} 断网={network_disabled}）：{cmd} → {v}"


@pytest.mark.parametrize("in_sandbox,network_disabled", _LIFE_MODES)
@pytest.mark.parametrize("cmd", _FLAG_PREFIX_PASSES)
def test_flag_prefix_bare_selfdescription_passes(ws, cmd, in_sandbox, network_disabled):
    """二十五轮 🔴1 裸自述哨兵：flag 后无子命令 = 纯版本/帮助，放行。"""
    v = _run_cmd(ws, cmd, in_sandbox=in_sandbox, network_disabled=network_disabled)
    assert not _is_ask(v), f"裸自述（flag 后无子命令）不应拦：{cmd} → {v}"


@pytest.mark.parametrize("in_sandbox,network_disabled", _LIFE_MODES)
@pytest.mark.parametrize("cmd", _UNKNOWN_WRAPPER_ASKS)
def test_unknown_wrapper_with_container_asks(ws, cmd, in_sandbox, network_disabled):
    """二十五轮 🔴2：表外 head（未知包装器）+ 容器命令必须 ASK——
    fail-closed 落到格式识别入口（剥完包装器取到 head 的那一刻判）。"""
    v = _run_cmd(ws, cmd, in_sandbox=in_sandbox, network_disabled=network_disabled)
    assert _is_ask(v), f"未知包装器 + 容器命令必须 ask：{cmd} → {v}"


@pytest.mark.parametrize("in_sandbox,network_disabled", _LIFE_MODES)
@pytest.mark.parametrize("cmd", _UNKNOWN_WRAPPER_PASSES)
def test_benign_head_with_container_literal_passes(ws, cmd, in_sandbox, network_disabled):
    """二十五轮 🔴2 白名单哨兵：无关普通命令 + 容器 CLI 字样 = 文本操作，放行。"""
    v = _run_cmd(ws, cmd, in_sandbox=in_sandbox, network_disabled=network_disabled)
    assert not _is_ask(v), f"无关普通命令不应拦：{cmd} → {v}"


@pytest.mark.parametrize("cmd", _WRAPPER_VAR_PASSES)
def test_lifecycle_pass_sentinels(ws, cmd):
    """二十五轮 🔴3：透明包装/VAR/无值 flag 的观察类放行哨兵（回滚必红配套）。"""
    v = _run_cmd(ws, cmd)
    assert not _is_ask(v), f"观察类透明形态不应拦：{cmd} → {v}"

# ═══ 十三轮遗留口径：xargs 下【可见目标】按存在性正常判定 ═══

@pytest.mark.parametrize("cmd", [
    "echo src.txt | xargs cp dst.txt",   # §A4 既定锚点（十二轮 §A 复验过）
    "find . | xargs install -m 644 x",   # 审查实测三模式 PASS（A/B：旧版同 PASS，非回归）
])
def test_xargs_visible_target_judged_normally(ws, cmd):
    """口径（十三轮拍板）：xargs 下 cp/install 家族的【可见末位操作数 = 目标】，
    按其存在性正常判定（不存在 → 放行）；仅【无可见目标】才 fail-closed。

    残余风险（如实记录）：多源投喂到"目录型 dest"时真实落点 dest/basename(源)
    不可判定——本口径宁少问不误伤，与 §A4 锚点保持一致。
    """
    v = _run_cmd(ws, cmd)
    assert not _is_ask(v), f"可见目标按存在性判定（口径锚点）：{cmd} → {v}"

def test_snapshot_rule_error_is_observable(tmp_path):
    """十五轮 🔴1 出血点修复锚点：规则阶段抛错必须【可观测】（knowledge 事件 + 日志）。

    回滚实验：把 _snapshot_workspace 的 except 改回 `pass`（静默吞）→
    本测试必红（ knowledge 事件消失）。审查员实测：旧行为下 stdout/stderr
    零输出、快照静默变空——"强化断言文案"拦不住生产路径，只有可观测算修好。
    """
    from app.bus import EventBus
    from app.executors.local import LocalExecutor
    from app.loop import TaskRun
    from app.schemas import TaskSummary
    from app.store import FsStore
    from types import SimpleNamespace

    store = FsStore(tmp_path / "data")
    run = TaskRun(
        TaskSummary(id="task_20261003_obsv", title="obs", created_at="2026-10-03T00:00:00Z",
                    updated_at="2026-10-03T00:00:00Z"),
        "观测", store=store, bus=EventBus(), provider=None,
        executor=LocalExecutor(SimpleNamespace(
            type="local", workspace_root=str(tmp_path), allowed_dirs=[str(tmp_path)],
            timeout_seconds=30, sandbox="off", shell=None, cfg_shell="", search_url="",
            searxng_url="", browser_channel="msedge", comfyui_url="http://127.0.0.1:9",
            image_checkpoint="x", sandbox_image="python:3.12-slim",
            sandbox_network="none", sandbox_memory="512m",
        )),
        approval=ApprovalManager(), tools=[], max_iterations=1, timeout_seconds=1.0,
        approval_required=[], on_finish=lambda r: None,
    )
    (run.workdir / "a.txt").write_text("x", encoding="utf-8")

    def boom(*_a, **_k):
        raise RuntimeError("simulated-rule-bug")

    run.store.snapshots_dir = boom  # 让快照 setup 阶段抛错（旧行为：静默吞）
    run._snapshot_workspace()  # 不得抛出（不影响交付），但必须留下可观测痕迹
    evs = [e.model_dump() for e in store.read_events(run.task.id)]
    hit = [e for e in evs if e["type"] == "knowledge"
           and "快照未生成" in str(e["payload"].get("title", ""))]
    assert hit, "规则异常必须发 knowledge 事件——静默吞掉 = 未修（十五轮 🔴1）"

def test_snapshot_failure_honesty(tmp_path, monkeypatch):
    """十七轮 🔴5-1/5-2 端到端：
    ① 部分失败 → 旧好快照不被挤 + 标题如实带失败数；
    ② 全部失败 → 也有"未生成"事件（此前零事件）+ 空目录被回收。
    回滚锚点：收尾块退回 `if copied:` 独占 → ② 的断言必红。
    """
    from app.bus import EventBus
    from app.executors.local import LocalExecutor
    from app.loop import TaskRun
    from app.schemas import TaskSummary
    from app.store import FsStore
    from types import SimpleNamespace
    import shutil as _shutil

    store = FsStore(tmp_path / "data")
    run = TaskRun(
        TaskSummary(id="task_20261003_fho1", title="fh", created_at="2026-10-03T00:00:00Z",
                    updated_at="2026-10-03T00:00:00Z"),
        "诚实", store=store, bus=EventBus(), provider=None,
        executor=LocalExecutor(SimpleNamespace(
            type="local", workspace_root=str(tmp_path), allowed_dirs=[str(tmp_path)],
            timeout_seconds=30, sandbox="off", shell=None, cfg_shell="", search_url="",
            searxng_url="", browser_channel="msedge", comfyui_url="http://127.0.0.1:9",
            image_checkpoint="x", sandbox_image="python:3.12-slim",
            sandbox_network="none", sandbox_memory="512m",
        )),
        approval=ApprovalManager(), tools=[], max_iterations=1, timeout_seconds=1.0,
        approval_required=[], on_finish=lambda r: None,
    )
    wd = run.workdir
    (wd / "good.txt").write_text("GOOD", encoding="utf-8")
    (wd / "b.txt").write_text("B", encoding="utf-8")
    snap_root = store.snapshots_dir(run.task.id)

    def nonempty_dirs():
        return [d for d in snap_root.iterdir() if d.is_dir() and any(d.iterdir())]

    # 第一份：好快照
    run._snapshot_workspace()
    good1 = nonempty_dirs()[0]

    # ② 全失败（copy2 全抛 OSError）→ 事件"未生成" + 空目录回收 + good1 仍在
    monkeypatch.setattr(_shutil, "copy2", lambda *a, **k: (_ for _ in ()).throw(OSError("disk-full")))
    run._snapshot_workspace()
    monkeypatch.undo()
    kn = [e.model_dump() for e in store.read_events(run.task.id) if e.type == "knowledge"]
    assert any("未入快照" in str(e["payload"].get("title", "")) for e in kn),         f"全失败必须有诚实事件（此前零事件）：{[e['payload'].get('title') for e in kn]}"
    assert good1 in nonempty_dirs(), "全失败后好快照必须仍在"
    assert not [d for d in snap_root.iterdir() if d.is_dir() and not any(d.iterdir())],         "空目录必须被回收（不占滚动窗口）"

    # ① 部分失败：只让 b.txt 失败 → 标题如实带失败数；good1 不被挤
    real_copy2 = _shutil.copy2
    def partial(src, dst, *a, **k):
        if str(src).endswith("b.txt"):
            raise OSError("simulated-partial")
        return real_copy2(src, dst, *a, **k)
    monkeypatch.setattr(_shutil, "copy2", partial)
    run._snapshot_workspace()
    monkeypatch.undo()
    kn = [e.model_dump() for e in store.read_events(run.task.id) if e.type == "knowledge"]
    assert any("1 个失败" in str(e["payload"].get("title", "")) for e in kn),         f"部分失败标题必须如实：{[e['payload'].get('title') for e in kn]}"
    assert good1 in nonempty_dirs(), "部分失败（跳过滚动）不得挤掉好快照"

def test_snapshot_oversized_and_rolling(tmp_path, monkeypatch):
    """十八轮 🔴4-1/4-2：
    ① 全部超大 → 有"未入快照"事件（此前静默零事件）；
    ② partial 常态下滚动仍生效（连续多次 1 失败 → 非空目录数 ≤5，不再无限增长）。
    回滚锚点：4-1 退回 `continue` 静默 → ① 红；4-2 退回 `if not failed:` → ② 红。
    """
    from app.bus import EventBus
    from app.executors.local import LocalExecutor
    from app.loop import TaskRun
    from app.schemas import TaskSummary
    from app.store import FsStore
    from types import SimpleNamespace
    import shutil as _shutil

    store = FsStore(tmp_path / "data")
    run = TaskRun(
        TaskSummary(id="task_20261003_ovr1", title="ov", created_at="2026-10-03T00:00:00Z",
                    updated_at="2026-10-03T00:00:00Z"),
        "超大", store=store, bus=EventBus(), provider=None,
        executor=LocalExecutor(SimpleNamespace(
            type="local", workspace_root=str(tmp_path), allowed_dirs=[str(tmp_path)],
            timeout_seconds=30, sandbox="off", shell=None, cfg_shell="", search_url="",
            searxng_url="", browser_channel="msedge", comfyui_url="http://127.0.0.1:9",
            image_checkpoint="x", sandbox_image="python:3.12-slim",
            sandbox_network="none", sandbox_memory="512m",
        )),
        approval=ApprovalManager(), tools=[], max_iterations=1, timeout_seconds=1.0,
        approval_required=[], on_finish=lambda r: None,
    )
    wd = run.workdir
    snap_root = store.snapshots_dir(run.task.id)

    def kn_titles():
        return [e.model_dump()["payload"].get("title", "")
                for e in store.read_events(run.task.id) if e.type == "knowledge"]

    # ① 全部超大：【稀疏文件】真造 >200MB（NTFS 稀疏不占盘，毫秒级）——
    #    十九轮 🔴4：此前用 copy2-OSError 模拟，走不到超限分支 = 假锚点
    #    （回滚 continue 后仍 passed）。现在真走超限分支：
    #    修复版 → oversized 计入 failed + 事件；回滚版 → 零事件零 failed → 红。
    big = wd / "huge.bin"
    with open(big, "wb") as f:
        f.seek(201 * 1024 * 1024)
        f.write(b"\0")  # 稀疏：逻辑 201MB，物理 ~4KB
    (wd / "a.txt").write_text("x", encoding="utf-8")
    run._snapshot_workspace()
    assert big.stat().st_size > 200 * 1024 * 1024, "稀疏文件必须真超阈值"
    titles = kn_titles()
    # 修复版：超大计入 failed → 标题"1 个失败" + 正文含"超大"说明
    # （回滚静默 continue：超大不计 failed → copied=1、failed=0 → 标题无"失败"、
    #   正文无"超大" → 本断言红在【超大未计入】上）
    assert any("1 个失败" in t for t in titles),         f"超大文件必须计入 failed（回滚时红在此）：{titles}"
    assert any("200MB" in str(e.model_dump()["payload"].get("content", ""))
               for e in store.read_events(run.task.id) if e.type == "knowledge"),         f"事件正文必须说明超 200MB：{titles}"
    big.unlink()  # 清理稀疏文件
    (wd / "a.txt").unlink()

    # ② partial 常态滚动（十八轮 🔴4-2 / 十九轮🔴1 重做——原 8 轮版被
    #    【秒级时间戳碰撞】打穿：快测试 8 轮写同一 dest，断言恒绿=假锚点）。
    #    现场构造：5 份历史 partial + 1 份历史完整；本轮再造 1 份 partial
    #    → 收尾应淘汰最旧 partial（非空目录数回落；完整快照受保护）
    real = _shutil.copy2
    def one_fails(src, dst, *a, **k):
        if str(src).endswith("b.txt"):
            raise OSError("partial")
        return real(src, dst, *a, **k)
    (wd / "b.txt").write_text("y", encoding="utf-8")
    (wd / "a.txt").write_text("x", encoding="utf-8")  # copied>0 的前提（①已 unlink）
    for _i in range(5):
        d = snap_root / f"2026010{_i + 1}T000000"
        d.mkdir(parents=True, exist_ok=True)
        (d / "f.txt").write_text("old-partial", encoding="utf-8")
        (d / ".partial").write_text("1", encoding="utf-8")
    full_old = snap_root / "20260109T000000"
    full_old.mkdir(parents=True, exist_ok=True)
    (full_old / "f.txt").write_text("old-full", encoding="utf-8")
    monkeypatch.setattr(_shutil, "copy2", one_fails)
    run._snapshot_workspace()
    monkeypatch.undo()
    nonempty_after = [d for d in snap_root.iterdir() if d.is_dir() and any(d.iterdir())]
    # 7 份（6 旧 + 1 新）→ 淘汰 2 份最旧 partial → 剩 5（4 partial + 1 full + 新 p = 5）
    assert len(nonempty_after) <= 6,         f"partial 淘汰必须生效（实测：回滚态 8 目录；修复态 ≤6）：实际 {len(nonempty_after)}"
    assert full_old.exists(), "完整快照在 partial 淘汰阶段受保护（不被优先删）"
    # 反事实差异点：修复版收尾把非空压回 ≤6；回滚版（不滚）恒 8——
    # 所以上限取 6 才能区分（回滚=7 红 / 修复=5 绿）
    assert len(nonempty_after) <= 6 and full_old.exists(), "partial 淘汰 + 完整快照保护"


# ═══ 二十六轮第3处：git/pip 间接执行参数面（①c remote-only 面新增） ═══
# 独立复现（验证员）：`git -c core.pager='docker system prune -a' log` 三模式
# PASS——git 在无关命令白名单里整族放行，但 -c/--exec-path/--upload-pack 是
# 【间接执行】入口（core.pager/core.sshCommand/difftool.*.cmd/filter.*.clean
# 都是运行外部命令的配置键）。pip 的 -c/--constraint 按拍板一并 ASK。
_INDIRECT_EXEC_ASKS = [
    "git -c core.pager='docker system prune -a' log",
    "git --exec-path=/tmp log",
    "git -c core.sshCommand=x log",
    "pip -c x",
]
_INDIRECT_EXEC_PASSES = [
    "git log",
    "git log docker",
    "git status",
    "pip list",
    "pip --version",
    # 二十六轮第 7 批第 1 处：全局选项同族扫描后【刻意不拦】的形态——
    # 都不注入配置、不指定外部程序（只改落点/输出方式）→ 必须仍 PASS，
    # 防"整族恐慌式收窄"把 CI/submodule 常见的 -C/--git-dir 一并误伤。
    "git -C . status",
    "git --git-dir=/tmp/x log",
    "git --work-tree=/tmp log",
    "git --namespace=ns1 status",
    "git --paginate log",
    "git --no-pager log",
    "git --version",
    "git --list-cmds=main",
]


# ═══ 二十六轮第 7 批第 1 处：`--config-env` 补口 + git 全局选项同族扫描 ═══
# 真机复现（git 2.53.0.windows.3）：`git --config-env=core.pager=EVIL log` 与
# `git --config-env core.pager=EVIL log` 三模式全 PASS，而同能力面的
# `git -c core.pager=x log` 是 ASK（★真机：EVIL='!echo PWNED'
# git --config-env=alias.pwn=EVIL pwn → 真的执行了 shell）。
# `--attr-source=<tree-ish>` 同批实测：临时仓库里工作区【无】.gitattributes、
# 树里有 → `git --attr-source=HEAD check-attr filter f.txt` = evilfilter，
# 对照组（不加该 flag）= unspecified ⇒ 它能换掉属性来源，而属性正是
# filter.<n>.clean/smudge/process 与 diff.<n>.command（外部程序）的开关。
_GIT_CONFIG_ENV_ASKS = [
    "git --config-env=core.pager=EVIL log",       # = 形态
    "git --config-env core.pager=EVIL log",       # 空格形态
    "git --config-env=alias.pwn=EVIL pwn",        # alias 形态（真机实测执行 shell）
    "git --config-env=core.sshCommand=EVIL log",  # 同族可执行键
    "git --attr-source=HEAD check-attr filter f.txt",
]


@pytest.mark.parametrize("in_sandbox,network_disabled", _LIFE_MODES)
@pytest.mark.parametrize("cmd", _GIT_CONFIG_ENV_ASKS)
def test_git_config_env_indirect_exec_asks(ws, cmd, in_sandbox, network_disabled):
    """二十六轮第 7 批第 1 处：--config-env / --attr-source 必须 ASK（三模式）。"""
    v = _run_cmd(ws, cmd, in_sandbox=in_sandbox, network_disabled=network_disabled)
    assert _is_ask(v), f"配置注入类 git 全局选项必须 ask：{cmd} → {v}"
    assert v.key.startswith("struct:indirect:"), f"键必须是 struct:indirect:*（①c 面）：{v.key}"


@pytest.mark.parametrize("in_sandbox,network_disabled", _LIFE_MODES)
@pytest.mark.parametrize("cmd", _INDIRECT_EXEC_ASKS)
def test_git_pip_indirect_exec_asks(ws, cmd, in_sandbox, network_disabled):
    """二十六轮第3处：git/pip 间接执行参数必须 ASK（struct:indirect:* 键）。"""
    v = _run_cmd(ws, cmd, in_sandbox=in_sandbox, network_disabled=network_disabled)
    assert _is_ask(v), f"间接执行参数必须 ask：{cmd} → {v}"
    assert v.key.startswith("struct:indirect:"), f"键必须是 struct:indirect:*（①c 面）：{v.key}"


@pytest.mark.parametrize("cmd", _INDIRECT_EXEC_PASSES)
def test_git_pip_plain_ops_pass(ws, cmd):
    """二十六轮第3处：git/pip 普通操作不受影响（防过度拦截哨兵）。"""
    v = _run_cmd(ws, cmd)
    assert not _is_ask(v), f"普通 git/pip 操作不应拦：{cmd} → {v}"


# ═══ 二十六轮第2批：三条间接执行旁路 + git -c 位置盲判 ═══
# 独立复现（验证员，三模式全 PASS）：tar --to-command / rsync --rsh /
# git config core.pager（两步攻击第一步：先写恶意 pager，之后任何 git log
# 都执行它）。git -c 位置盲判：git commit -c HEAD（reuse message）此前误拦。
_BYPASS_2B_ASKS = [
    "tar --to-command='docker system prune -a' -xf a.tar",
    "rsync --rsh='docker system prune -a' a b",
    "git config core.pager 'docker system prune -a'",
    "git config core.sshCommand 'x'",
    "git config alias.dump '!docker system prune -a'",
    "git config filter.lfs.clean 'x'",
    "git -c core.pager='x' log",
]
_BYPASS_2B_PASSES = [
    "tar -xf a.tar",
    "rsync a b",
    "git config user.name x",
    "git config core.autocrlf true",
    "git commit -c HEAD",
    "git diff -c",
    "git show -c",
    "git blame -c",
    "git commit -C HEAD",
]


@pytest.mark.parametrize("in_sandbox,network_disabled", _LIFE_MODES)
@pytest.mark.parametrize("cmd", _BYPASS_2B_ASKS)
def test_indirect_bypass_2b_asks(ws, cmd, in_sandbox, network_disabled):
    """二十六轮第2批：tar/rsync/git config 间接执行旁路必须 ASK。"""
    v = _run_cmd(ws, cmd, in_sandbox=in_sandbox, network_disabled=network_disabled)
    assert _is_ask(v), f"间接执行旁路必须 ask：{cmd} → {v}"


@pytest.mark.parametrize("cmd", _BYPASS_2B_PASSES)
def test_indirect_bypass_2b_normal_pass(ws, cmd):
    """二十六轮第2批：正规用法/子命令级 -c 不误拦。"""
    v = _run_cmd(ws, cmd)
    assert not _is_ask(v), f"正规用法不应拦：{cmd} → {v}"


# ═══ 二十六轮第2批第3处：自述类口径统一 ═══
# 真机实测（docker 29.8.0）：裸 -v/-D/--tlsverify/--context x/--log-level <值>
# 全部打印 Usage 或报错退出（无资源操作）→ 统一 PASS（此前裸 -D ASK 而裸 -v
# PASS，同族不同判）；--log-level ps 与 -l ps 两个等价写法实测审批面同结果。
# compose --help <子命令> 三模式统一 ASK（此前 host/sandbox ASK net:docker、
# nonet PASS——容器面豁免被 net 面接走，口径对不上）。
_SELFDESC_PASSES = [
    "docker -D",
    "docker --tlsverify",
    "docker --context x",
    "docker --log-level ps",
    "docker -l ps",
    "docker --log-level debug ps",
    "docker -l debug ps",
    "docker compose --help",
]


@pytest.mark.parametrize("in_sandbox,network_disabled", _LIFE_MODES)
@pytest.mark.parametrize("cmd", _SELFDESC_PASSES)
def test_selfdescription_options_only_pass(ws, cmd, in_sandbox, network_disabled):
    """二十六轮第2批第3处：选项-only（无子命令）= 纯自述/错误退出，三模式 PASS。"""
    v = _run_cmd(ws, cmd, in_sandbox=in_sandbox, network_disabled=network_disabled)
    assert not _is_ask(v), f"选项-only 自述不应拦：{cmd} → {v}"


@pytest.mark.parametrize("in_sandbox,network_disabled", _LIFE_MODES)
@pytest.mark.parametrize("cmd", ["docker compose --help up"])
def test_compose_help_with_sub_unified_asks(ws, cmd, in_sandbox, network_disabled):
    """二十六轮第2批第3处：compose --help + 子命令三模式统一 ASK（struct 键）。"""
    v = _run_cmd(ws, cmd, in_sandbox=in_sandbox, network_disabled=network_disabled)
    assert _is_ask(v), f"compose --help + 子命令必须 ask：{cmd} → {v}"
    assert v.key.startswith("struct:"), f"键必须为 struct:*（容器面统一接管）：{v.key}"


# ═══ 二十六轮第 3 批第 2 处：--debug/-D 无值单跳（真子命令不被吞）═══
# 二十五轮曾以 docker --debug ps 为锚点——选项-only 统一后该形态失去判别力
# （回滚后经"选项-only"路径同样 PASS），组被误删；二十六轮验证员指出
# 「无值flag + 观察子命令 + 位置参数」形态仍可观察：回滚态 --debug 双跳
# 吞掉 inspect/logs、abc 当动词 → ASK struct:docker-abc-unknown（本班复现）。
_DEBUG_ARG_PASS = [
    "docker --debug inspect abc",
    "docker -D logs abc",
    "docker --debug logs abc",
]


@pytest.mark.parametrize("cmd", _DEBUG_ARG_PASS)
def test_debug_valueless_flag_keeps_subcommand(ws, cmd):
    """二十六轮第 3 批第 2 处：无值全局 flag 不得吞掉真实观察子命令。"""
    v = _run_cmd(ws, cmd)
    assert not _is_ask(v), f"--debug/-D 不应吞子命令：{cmd} → {v}"


# ═══ 二十六轮第 3 批第 3 处：git config 漏拦补口 ═══
_GITCFG_3B_ASKS = [
    "git config interactive.diffFilter 'x'",   # 二十五轮常量拼写 diffilter 漏拦（本班修正）
    "git config difftool.x.cmd 'x'",           # 注释声称覆盖、实现只查 .command（本班实现）
    "git config DIFF.EXTERNAL 'x'",            # 大小写归一（真 key 大小写混排）
]
_GITCFG_3B_PASSES = [
    "git config user.name x",
    "git config core.autocrlf true",
]


@pytest.mark.parametrize("cmd", _GITCFG_3B_ASKS)
def test_git_config_exec_keys_3b_asks(ws, cmd):
    """二十六轮第 3 批第 3 处：git config 可执行键漏拦补口（三模式）。"""
    for in_sandbox, network_disabled in _LIFE_MODES:
        v = _run_cmd(ws, cmd, in_sandbox=in_sandbox, network_disabled=network_disabled)
        assert _is_ask(v), f"git config 可执行键必须 ask：{cmd} → {v}"


@pytest.mark.parametrize("cmd", _GITCFG_3B_PASSES)
def test_git_config_benign_3b_pass(ws, cmd):
    """普通配置键不受影响。"""
    v = _run_cmd(ws, cmd)
    assert not _is_ask(v), f"普通配置键不应拦：{cmd} → {v}"


# ═══ 二十六轮第 4 批第 5 处：git config 10+2 键补口 ═══
_GITCFG_4B_ASKS = [
    "git config credential.helper '!sh -c id'",        # git 最经典任意命令执行向量
    "git config credential.https://x.helper '!cmd'",
    "git config core.askPass '!cmd'",
    "git config core.gitProxy '!cmd'",
    "git config core.alternateRefsCommand '!cmd'",
    "git config filter.x.process 'sh -c id'",          # .clean/.smudge 已覆盖，.process 漏
    "git config diff.x.textconv 'sh -c id'",
    "git config core.hooksPath /tmp/h",
    "git config sequence.editor '!cmd'",
    "git config remote.origin.uploadpack 'sh -c id'",
    "git config remote.origin.receivepack 'sh -c id'",
    "git config include.path /tmp/evil",               # 配置注入
    "git config merge.tool '!cmd'",
]
_GITCFG_4B_PASSES = [
    "git config user.name x",
    "git config core.autocrlf true",
    "git config remote.origin.url https://github.com/y/z",
]


@pytest.mark.parametrize("cmd", _GITCFG_4B_ASKS)
def test_git_config_exec_keys_4b_asks(ws, cmd):
    """二十六轮第 4 批第 5 处：git config 可执行/注入键补口（三模式）。"""
    for in_sandbox, network_disabled in _LIFE_MODES:
        v = _run_cmd(ws, cmd, in_sandbox=in_sandbox, network_disabled=network_disabled)
        assert _is_ask(v), f"git config 可执行键必须 ask：{cmd} → {v}"


@pytest.mark.parametrize("cmd", _GITCFG_4B_PASSES)
def test_git_config_benign_4b_pass(ws, cmd):
    """普通配置不受影响。"""
    v = _run_cmd(ws, cmd)
    assert not _is_ask(v), f"普通配置不应拦：{cmd} → {v}"


# ═══ 二十六轮第 5 批第 2 处：git config 选项绕过 + 3 漏键 ═══
_GITCFG_5B_ASKS = [
    "git config --global core.pager 'docker system prune -a'",   # 最常见真实写法曾被一行参数抹掉
    "git config --local core.pager 'x'",
    "git config --system alias.dump '!x'",
    "git config --file /tmp/x core.pager 'x'",
    "git config -f /tmp/x core.pager 'x'",
    "git config --global credential.helper '!cmd'",
    "git config --global core.hooksPath /tmp/evil",
    "git config gpg.openpgp.program 'x'",
    "git config gpg.ssh.program 'x'",
    "git config trailer.sign.command 'x'",
    "git config protocol.ext.allow 'always'",                    # ext:: remote helper 通道
]
_GITCFG_5B_PASSES = [
    "git config user.name x",
    "git config --global user.name x",
    "git config core.autocrlf true",
    "git config trailer.sign.key 'x'",
]


@pytest.mark.parametrize("cmd", _GITCFG_5B_ASKS)
def test_git_config_global_bypass_5b_asks(ws, cmd):
    """二十六轮第 5 批第 2 处：config 选项（--global 等）不得抹掉 key 判定。"""
    for in_sandbox, network_disabled in _LIFE_MODES:
        v = _run_cmd(ws, cmd, in_sandbox=in_sandbox, network_disabled=network_disabled)
        assert _is_ask(v), f"必须 ask：{cmd} → {v}"


@pytest.mark.parametrize("cmd", _GITCFG_5B_PASSES)
def test_git_config_benign_5b_pass(ws, cmd):
    v = _run_cmd(ws, cmd)
    assert not _is_ask(v), f"普通配置不应拦：{cmd} → {v}"


# ═══ 二十六轮第 6 批第 2 处：git config 选项表重写 ═══
_GITCFG_6B_ASKS = [
    "git config --global --type path core.pager '!cmd'",   # --type 带值，值曾被当 key
    "git config --comment m core.pager '!cmd'",
    "git config --type bool core.pager '!cmd'",
    "git config --type=path core.pager '!cmd'",
    "git config set core.pager '!cmd'",                    # 现代 set 子命令
    # 二十六轮第 7 批第 8-b 处补：--value 的【空格式】出现于 key 之前——
    # 真机实测这条 rc=0 且真的把 core.pager 写成 !cmd；本表若把 "--value"
    # 从 _CFG_VALUE_OPTS 删掉，x 会被当成 key ⇒ 这条立刻变 PASS（真绕过）。
    "git config set --value x core.pager '!cmd'",
    "git config set --value=x core.pager '!cmd'",
    "git config --edit",                                   # 拉起 core.editor
    "git config edit",
]
_GITCFG_6B_PASSES = [
    "git config --get core.pager",                         # 只读曾被误拦
    "git config get core.pager",
    "git config --list",
    # 二十六轮第 7 批第 8-b 处补两条（钉住选项表的两处口径）：
    # ① 裸 `git config list` = 现代子命令的【非选项 token】形态（真机 rc=0）——
    #    证明 `_CFG_READ_HEADS` 里原来的裸 "list"/"get" 在此分支永不可达、删掉不改行为；
    # ② `git config get --default core.pager` = 带值选项"空格式"的只读形态——
    #    真机实测：撤掉 `--default` 后【本条的判定不变】（key 取的是 non-opts[0]="get"，
    #    不会轮到 core.pager）。故 --default 属"口径自洽（宁严）"而非"安全承重"项，如实钉住。
    "git config list",
    "git config get --default core.pager",
    "git config core.pager",                               # 传统单 token 读法
    "git config unset core.pager",
    "git config user.name x",
    "git config trailer.sign.key x",
]


@pytest.mark.parametrize("cmd", _GITCFG_6B_ASKS)
def test_git_config_options_6b_asks(ws, cmd):
    for in_sandbox, network_disabled in _LIFE_MODES:
        v = _run_cmd(ws, cmd, in_sandbox=in_sandbox, network_disabled=network_disabled)
        assert _is_ask(v), f"必须 ask：{cmd} → {v}"


@pytest.mark.parametrize("cmd", _GITCFG_6B_PASSES)
def test_git_config_options_6b_pass(ws, cmd):
    v = _run_cmd(ws, cmd)
    assert not _is_ask(v), f"只读/普通配置不应拦：{cmd} → {v}"
