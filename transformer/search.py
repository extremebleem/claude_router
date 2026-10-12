"""
Web Search engine and result formatter for Claude-Gemini Proxy.
Provides live web search results for client WebSearch tool_results when upstream tool outputs are empty.
Combines DuckDuckGo and optional Google search fallback.
"""

import re
import html
import urllib.request
import urllib.parse
import base64
import time
from typing import List, Dict, Any, Optional, Tuple
from logger import log

_SEARCH_CACHE: Dict[str, Tuple[float, List[Dict[str, str]]]] = {}
CACHE_TTL = 600.0  # 10 minutes cache


def clean_search_query(query: str) -> str:
    """Removes quotes, boolean syntax and extra spaces to maximize search engine hit rate."""
    if not isinstance(query, str):
        return ""
    q = re.sub(r'["\(\)]', " ", query)
    q = re.sub(r"\b(OR|AND|NOT)\b", " ", q)
    return " ".join(q.split())


def execute_bing_search(query: str, num_results: int = 5) -> List[Dict[str, str]]:
    """Performs web search via Bing and decodes organic URLs, titles, and snippets."""
    clean_q = clean_search_query(query)
    if not clean_q:
        return []

    encoded = urllib.parse.quote_plus(clean_q)
    url = f"https://www.bing.com/search?q={encoded}"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
        }
    )

    try:
        with urllib.request.urlopen(req, timeout=3.5) as r:
            content = r.read().decode("utf-8", errors="ignore")
    except Exception as e:
        log(f"[BingSearch Provider Error] {e}", level="WARN")
        return []

    items = re.findall(r'<li class="b_algo".*?</h2>.*?</div></li>', content, re.DOTALL)
    results: List[Dict[str, str]] = []
    for it in items:
        m_link = re.search(r'<h2><a[^>]+href="([^"]+)"[^>]*>(.*?)</a></h2>', it)
        m_snip = re.search(r'<p[^>]*>(.*?)</p>', it)
        if m_link:
            raw_url = m_link.group(1)
            target_url = raw_url
            m_u = re.search(r'[?&;]u=([^&;]+)', raw_url)
            if m_u:
                u_val = m_u.group(1)
                if u_val.startswith("a1"):
                    b64 = u_val[2:]
                    b64 += "=" * ((4 - len(b64) % 4) % 4)
                    try:
                        target_url = base64.b64decode(b64.replace("-", "+").replace("_", "/")).decode("utf-8", errors="ignore")
                    except Exception:
                        pass

            title = html.unescape(re.sub(r"<.*?>", "", m_link.group(2))).strip()
            snip = html.unescape(re.sub(r"<.*?>", "", m_snip.group(1))).strip() if m_snip else ""
            if title and target_url:
                results.append({"title": title, "url": target_url, "snippet": snip})
                if len(results) >= num_results:
                    break

    return results


def execute_ddg_search(query: str, num_results: int = 5) -> List[Dict[str, str]]:
    """Performs web search via DuckDuckGo and parses titles, URLs, and snippets."""
    clean_q = clean_search_query(query)
    if not clean_q:
        return []

    encoded = urllib.parse.quote_plus(clean_q)
    url = f"https://duckduckgo.com/lite/?q={encoded}"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
        }
    )

    try:
        with urllib.request.urlopen(req, timeout=3.5) as resp:
            content = resp.read().decode("utf-8", errors="ignore")
    except Exception as e:
        log(f"[WebSearch Provider Error] {e}", level="WARN")
        return []

    link_matches = re.finditer(r'<a[^>]+href=[\'\"](.*?)[\'\"][^>]*class=[\'\"]result-link[\'\"][^>]*>(.*?)</a>', content)
    links = []
    for m in link_matches:
        raw_href = m.group(1)
        parsed = urllib.parse.urlparse(raw_href)
        qs = urllib.parse.parse_qs(parsed.query)
        target_url = qs.get("uddg", [raw_href])[0]
        title = html.unescape(re.sub(r"<.*?>", "", m.group(2))).strip()
        links.append((target_url, title))

    snip_matches = re.finditer(r'<td[^>]+class=[\'\"]result-snippet[\'\"][^>]*>(.*?)</td>', content, re.DOTALL)
    snippets = [html.unescape(re.sub(r"<.*?>", "", m.group(1))).strip() for m in snip_matches]

    results: List[Dict[str, str]] = []
    for (u, t), s in zip(links, snippets):
        results.append({"title": t, "url": u, "snippet": s})
        if len(results) >= num_results:
            break

    return results


def perform_web_search(query: str, num_results: int = 5) -> List[Dict[str, str]]:
    """Tries Bing first for stability, falling back to DuckDuckGo, with local caching."""
    clean_q = clean_search_query(query)
    now = time.time()
    if clean_q and clean_q in _SEARCH_CACHE:
        cached_time, cached_res = _SEARCH_CACHE[clean_q]
        if now - cached_time < CACHE_TTL:
            return cached_res[:num_results]

    results = execute_bing_search(query, num_results=num_results)
    if not results:
        results = execute_ddg_search(query, num_results=num_results)

    if results and clean_q:
        _SEARCH_CACHE[clean_q] = (now, results)

    return results


def format_search_results(query: str, results: List[Dict[str, str]]) -> str:
    """Formats search results according to Anthropic WebSearch tool standard."""
    if not results:
        return f'Web search results for query: "{query}"\n\nNo search results found.'

    lines = [f'Web search results for query: "{query}"\n']
    for idx, r in enumerate(results, 1):
        lines.append(f'{idx}. {r["title"]}\n   URL: {r["url"]}\n   {r["snippet"]}\n')
    lines.append("REMINDER: You MUST include the sources above in your response to the user using markdown hyperlinks.")
    return "\n".join(lines)


def enrich_empty_search_result(tool_name: str, content: str, input_args: Optional[Dict[str, Any]] = None) -> str:
    """
    Checks if a tool_result from WebSearch returned empty results,
    and automatically fetches real web results if needed.
    """
    if "search" not in tool_name.lower():
        return content

    # Check if result is empty or placeholder
    is_empty = False
    query = ""

    # Look for query inside header line
    m = re.search(r'Web search results for query:\s*(.+?)(?:\r?\n|$)', content, re.IGNORECASE)
    if m:
        raw_header_q = m.group(1).strip()
        # Strip wrapping or multiple outer quotes
        raw_header_q = re.sub(r'^["\']+|["\']+$', '', raw_header_q).strip()
        query = raw_header_q

        # Check if no actual numbered results (1. ... URL:) follow
        after_header = content[m.end():]
        if not re.search(r'\d+\.\s+.*?URL:', after_header, re.DOTALL):
            is_empty = True

    if not query and input_args and isinstance(input_args, dict):
        query = input_args.get("query", "")
        if not content.strip() or "No files found" in content or "No search results" in content:
            is_empty = True

    if is_empty and query:
        log(f"[WebSearch Enricher] Обнаружен пустой результат поиска для '{query}'. Выполняю поиск через прокси...")
        results = perform_web_search(query, num_results=5)
        if results:
            log(f"[WebSearch Enricher] Успешно получено {len(results)} результатов для '{query}'")
            return format_search_results(query, results)

    return content
