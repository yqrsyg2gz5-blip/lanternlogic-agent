"""SSRF 防护（K2 整改）——联网工具的目标地址校验。

威胁模型：LLM 可被网页/邮件里的间接提示词注入操纵，让 web_fetch/browser_navigate
访问回环/内网/云元数据地址（169.254.169.254）窃取本机或内网数据，再把内容编码进
外发 URL 渗漏。此前这些目标零校验。

防线（fail-closed，任何一步失败都拦截）：
1. assert_public_url：解析目标主机，**所有**解析结果都必须是公网地址；
   覆盖回环/私网/链路本地（含云元数据）/CGNAT/多播/保留段/未指定段，v4+v6。
2. recheck_still_public：请求完成后**二次解析取交集**——首次解析通过、
   连接时 DNS 答案被掉包（重绑定）时交集为空或出现被拦段 → 丢弃响应。
3. 重定向由调用方**逐跳**重新走 1+2（httpx 不再自动 follow）。

注意：web_search 的 search_url/searxng_url 是运营者配置项（非 LLM 可控），
不在本模块校验范围；image_gen 的 comfyui_url 同理（配置驱动，本机回环属正常）。
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit

_BLOCKED_NETWORKS = tuple(ipaddress.ip_network(c) for c in (
    # IPv4
    "0.0.0.0/8",        # 未指定/本机
    "10.0.0.0/8",       # 私网 A
    "100.64.0.0/10",    # CGNAT 运营商级 NAT
    "127.0.0.0/8",      # 回环
    "169.254.0.0/16",   # 链路本地（★ 含 169.254.169.254 云元数据）
    "172.16.0.0/12",    # 私网 B
    "192.0.0.0/24",     # IETF 协议分配
    "192.0.2.0/24",     # TEST-NET-1
    "192.168.0.0/16",   # 私网 C
    "198.18.0.0/15",    # 基准测试段
    "198.51.100.0/24",  # TEST-NET-2
    "203.0.113.0/24",   # TEST-NET-3
    "224.0.0.0/4",      # 多播
    "240.0.0.0/4",      # 保留
    # IPv6
    "::/128",           # 未指定
    "::1/128",          # 回环
    "::ffff:0:0/96",    # v4 映射（绕过面：::ffff:127.0.0.1）
    "64:ff9b::/96",     # v4/v6 转换（绕过面：64:ff9b::127.0.0.1）
    "fc00::/7",         # ULA 私网
    "fe80::/10",        # 链路本地
    "ff00::/8",         # 多播
))


class SsrfBlocked(ValueError):
    """目标地址命中内网/回环/保留段，或 DNS 无法核实（fail-closed）。"""


def _ip_blocked_reason(ip_s: str) -> str | None:
    try:
        ip = ipaddress.ip_address(ip_s)
    except ValueError:
        return "非法 IP"
    # v4 映射/转换地址取出内嵌 v4 再判一次（双保险）
    candidates = [ip]
    v4 = getattr(ip, "ipv4_mapped", None) or getattr(ip, "sixtofour", None)
    if v4 is not None:
        candidates.append(v4)
    for c in candidates:
        for net in _BLOCKED_NETWORKS:
            if c in net:
                return f"{c} 属于被拦截段 {net}"
    return None


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


async def resolve_ips(host: str) -> set[str]:
    """异步解析主机名 → 全部 IP（供校验；连接仍由 HTTP 客户端负责）。"""
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return {str(ai[4][0]) for ai in infos}


async def _assert_ips_public(host: str, ips: set[str]) -> None:
    if not ips:
        raise SsrfBlocked(f"目标 {host} DNS 无解析结果，无法核实（fail-closed）")
    for s in ips:
        reason = _ip_blocked_reason(s)
        if reason:
            raise SsrfBlocked(f"目标 {host} 解析到 {reason}，已拦截")


async def assert_public_url(url: str) -> set[str]:
    """校验 URL 目标为公网：字面 IP 直接判，域名解析全部 IP 逐条判。

    返回核实过的 IP 集合（供 recheck_still_public 取交集）。任何失败抛 SsrfBlocked。
    """
    host = (urlsplit(url).hostname or "").strip("[]")
    if not host:
        raise SsrfBlocked("URL 缺少主机名")
    if _is_ip_literal(host):
        ips = {host}
    else:
        try:
            ips = await resolve_ips(host)
        except Exception as e:
            raise SsrfBlocked(f"目标 {host} DNS 解析失败（{type(e).__name__}），无法核实（fail-closed）") from e
    await _assert_ips_public(host, ips)
    return ips


async def recheck_still_public(url: str, ips_before: set[str]) -> None:
    """防 DNS 重绑定：请求完成后二次解析。

    · 二次解析失败 / 与首次交集为空 / 出现被拦段 → 判定重绑定嫌疑，抛 SsrfBlocked
      （调用方必须丢弃已拿到的响应，不得使用其内容）。
    """
    host = (urlsplit(url).hostname or "").strip("[]")
    if _is_ip_literal(host):
        return  # 字面 IP 无 DNS 可重绑定，首次校验已覆盖
    try:
        ips_after = await resolve_ips(host)
    except Exception as e:
        raise SsrfBlocked(f"目标 {host} 二次解析失败（{type(e).__name__}），无法排除重绑定（fail-closed）") from e
    if not ips_after or not (ips_after & ips_before):
        raise SsrfBlocked(f"目标 {host} 二次解析与首次无交集（{sorted(ips_after)}），疑似 DNS 重绑定，已丢弃响应")
    await _assert_ips_public(host, ips_after)


async def browser_request_allowed(url: str) -> bool:
    """浏览器路由拦截用：单个请求 URL 是否放行（供 page.route 处理器调用）。"""
    if not url.startswith(("http://", "https://")):
        return True  # data:/blob:/about: 等非网络 scheme 不拦
    try:
        await assert_public_url(url)
        return True
    except SsrfBlocked:
        return False
    except Exception:
        return False  # 未知异常一律 fail-closed
