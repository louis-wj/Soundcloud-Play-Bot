"""Proxy format detection, parsing, and concurrent validation."""
import re
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from curl_cffi import requests as crequests


# ── Format detection ──────────────────────────────────────────────────────────

FORMATS = {
    "user:pass@host:port": re.compile(r'^[^:]+:[^@]+@[^:]+:\d+$'),
    "host:port:user:pass": re.compile(r'^[^:]+:\d+:[^:]+:.+$'),
    "host:port":           re.compile(r'^[^:]+:\d+$'),
    "ip:port":             re.compile(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d+$'),
}


def detect_format(proxy_str: str) -> str:
    proxy_str = proxy_str.strip()
    for name, pattern in FORMATS.items():
        if pattern.match(proxy_str):
            return name
    return "unknown"


def parse_proxy(proxy_str: str) -> dict:
    """Parse any proxy format into {host, port, user, password}."""
    proxy_str = proxy_str.strip()
    if '@' in proxy_str:
        auth, hostport = proxy_str.rsplit('@', 1)
        user, pw = auth.split(':', 1)
        host, port = hostport.rsplit(':', 1)
    else:
        parts = proxy_str.split(':')
        if len(parts) == 4:
            host, port, user, pw = parts
        elif len(parts) == 2:
            host, port = parts
            user, pw = '', ''
        else:
            return {"host": proxy_str, "port": "8080", "user": "", "password": "", "raw": proxy_str}
    return {"host": host, "port": port, "user": user, "password": pw, "raw": proxy_str}


def proxy_to_url(p: dict) -> str:
    if p["user"]:
        return f'http://{p["user"]}:{p["password"]}@{p["host"]}:{p["port"]}'
    return f'http://{p["host"]}:{p["port"]}'


# ── Validation ────────────────────────────────────────────────────────────────

def check_single_proxy(proxy_str: str, timeout: int = 15) -> dict:
    """Test a single proxy. Returns {proxy, working, ip, error}."""
    p = parse_proxy(proxy_str)
    url = proxy_to_url(p)
    try:
        resp = crequests.get(
            'https://httpbin.org/ip',
            proxies={'http': url, 'https': url},
            timeout=timeout,
            impersonate='chrome110',
        )
        if resp.status_code == 200:
            ip = resp.json().get('origin', '?')
            return {"proxy": proxy_str, "working": True, "ip": ip, "error": None}
        return {"proxy": proxy_str, "working": False, "ip": None, "error": f"HTTP {resp.status_code}"}
    except Exception as e:
        return {"proxy": proxy_str, "working": False, "ip": None, "error": str(e)[:80]}


def check_proxies(proxy_list: list[str], max_workers: int = 10,
                   callback=None) -> dict:
    """Check all proxies concurrently. Returns {total, working, dead, results}."""
    results = []
    working = 0
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(check_single_proxy, p): p for p in proxy_list}
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            if result["working"]:
                working += 1
            if callback:
                callback(result, len(results), len(proxy_list))
    return {
        "total": len(proxy_list),
        "working": working,
        "dead": len(proxy_list) - working,
        "results": results,
    }
