"""拉取"这个提供者现在到底有哪些模型"（Phase 2 ⑥：模型名下拉 + 一键刷新）。

为什么要有它：
  设置页的模型名此前只能填空（或从内置预设里挑），用户永远不知道服务商那边
  **此刻**有哪些模型可用（新增/下线/改名都看不见）。这个小模块负责真的去问一次。

★ 与 `app/ssrf.py`（web_fetch 用的守卫）**有意为之的差别**，别当成疏忽：
  SSRF 守卫是 fail-closed，连 127.0.0.1 和内网都拦 —— 那是对的，因为 web_fetch 抓的是
  **外部内容**，可能被诱导去访问内网。而这里的目标地址是**用户自己填进 config.json 的
  服务地址**，且本地 Ollama（127.0.0.1:11434）/ 局域网自建 vLLM 是**正当用法**，
  拦掉等于功能废掉。所以本模块的规矩是：
    · 只允许 http / https
    · **拒绝云元数据地址**（169.254.169.254 —— 被当成 SSRF 跳板时的头号目标）
    · 不跟随跨主机重定向（避免"你验的是 A、实际请求了 B"）
    · 超时 8 秒、失败一律降级为"内置预设 + 说明原因"，不抛 500
    · Key 只往**该提供者的地址**上带；返回值里**绝不出现 Key**
"""
from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlsplit

import httpx

# 云元数据/链路本地：任何"用户填的地址"都不该指向这里
_METADATA_HOSTS = {"169.254.169.254", "metadata.google.internal", "100.100.100.100"}
_TIMEOUT = 8.0

_CACHE: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
_CACHE_TTL = 300.0        # 5 分钟：设置页来回切不该反复打服务商


class ModelsBlocked(ValueError):
    """地址不允许（非 http(s) / 云元数据）。"""


def _check_url(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise ModelsBlocked(f"只支持 http/https 地址（收到 {parts.scheme or '空'}）")
    host = (parts.hostname or "").lower()
    if not host:
        raise ModelsBlocked("地址里没有主机名")
    if host in _METADATA_HOSTS:
        raise ModelsBlocked("拒绝访问云元数据地址")
    return url.rstrip("/")


def _parse_openai(body: Any) -> list[str]:
    """OpenAI 兼容：{"data":[{"id": "..."}]}（也容忍裸 {"models":[...]} 的变体）。"""
    items = body.get("data") if isinstance(body, dict) else None
    if items is None and isinstance(body, dict):
        items = body.get("models")
    out: list[str] = []
    for it in items or []:
        mid = it.get("id") if isinstance(it, dict) else it
        if isinstance(mid, str) and mid.strip():
            out.append(mid.strip())
    return out


def _parse_ollama(body: Any) -> list[str]:
    """Ollama：{"models":[{"name":"qwen3:0.6b"}]}。"""
    out: list[str] = []
    for it in (body.get("models") if isinstance(body, dict) else None) or []:
        name = it.get("name") if isinstance(it, dict) else it
        if isinstance(name, str) and name.strip():
            out.append(name.strip())
    return out


def _parse_anthropic(body: Any) -> list[str]:
    """Anthropic：{"data":[{"id":"claude-..."}]}。"""
    return _parse_openai(body)


def _plan(provider: str, base_url: str, key: str | None) -> tuple[str, dict[str, str], str]:
    """→ (请求 URL, 请求头, 解析器名)。把"每家怎么问"集中在这里。"""
    if provider == "ollama":
        base = base_url or "http://127.0.0.1:11434"
        return _check_url(f"{base}/api/tags"), {}, "ollama"
    if provider in ("anthropic", "claude"):
        base = base_url or "https://api.anthropic.com/v1"
        headers = {"anthropic-version": "2023-06-01"}
        if key:
            headers["x-api-key"] = key
        return _check_url(f"{base}/models"), headers, "anthropic"
    # 其余都是 OpenAI 兼容（mimo / deepseek / qwen / glm / kimi / openai_compatible …）
    base = base_url or ""
    if not base:
        raise ModelsBlocked("没有配置 API 地址（base_url），无法拉取模型列表")
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    return _check_url(f"{base}/models"), headers, "openai"


async def fetch_models(provider: str, base_url: str = "", key: str | None = None,
                       refresh: bool = False,
                       transport: httpx.AsyncBaseTransport | None = None) -> dict[str, Any]:
    """问一次服务商"有哪些模型"。**永不抛异常**，失败降级为 preset + 原因。

    返回：{"provider", "models": [str], "source": "live"|"preset", "note": str, "cached": bool}
    `transport` 只为测试注入（假 HTTP），生产走真网络。
    """
    ck = (provider, base_url or "")
    if not refresh and ck in _CACHE:
        ts, cached = _CACHE[ck]
        if time.time() - ts < _CACHE_TTL:
            return {**cached, "cached": True}
    try:
        url, headers, parser = _plan(provider, base_url, key)
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=False,
                                     transport=transport) as cli:
            r = await cli.get(url, headers=headers)
        if r.status_code != 200:
            raise RuntimeError(f"服务商返回 {r.status_code}")
        models = {"openai": _parse_openai, "ollama": _parse_ollama,
                  "anthropic": _parse_anthropic}[parser](r.json())
        if not models:
            raise RuntimeError("服务商返回的列表是空的")
        models = sorted(dict.fromkeys(models))
        out = {"provider": provider, "models": models, "source": "live",
               "note": f"已从服务商拉到 {len(models)} 个模型", "cached": False}
        _CACHE[ck] = (time.time(), out)
        return out
    except ModelsBlocked as e:
        return {"provider": provider, "models": [], "source": "preset",
                "note": f"地址不允许：{e}", "cached": False}
    except Exception as e:
        # ★ 不抛 500：拉不到是常态（没 Key / 服务商不提供 / 断网），
        #   界面该退回内置预设并把原因说清楚。
        return {"provider": provider, "models": [], "source": "preset",
                "note": f"拉不到在线列表（{type(e).__name__}: {str(e)[:80]}）—— 用内置预设即可",
                "cached": False}


def clear_cache() -> None:
    _CACHE.clear()
