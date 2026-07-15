"""Research and web utility functions for the CREPE MCP server.

Environment variables (all prefixed CREPE_):
  CREPE_TAVILY_API_KEY       — enables web_search; warning returned if absent.
  CREPE_HEADLESS_BROWSER_PATH — path to a Chromium-compatible browser for
                                fetch_webpage (e.g. /usr/bin/chromium).
                                Falls back to urllib if unset.
"""
from __future__ import annotations

import html
import json
import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional


# ---------------------------------------------------------------------------
# Academic search — Semantic Scholar (no API key required)
# ---------------------------------------------------------------------------

def academic_search(query: str, limit: int = 5) -> dict:
    """Search Semantic Scholar for academic papers."""
    encoded = urllib.parse.quote(query)
    url = (
        f"https://api.semanticscholar.org/graph/v1/paper/search"
        f"?query={encoded}&limit={limit}"
        f"&fields=title,abstract,openAccessPdf,url"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            return {"papers": [], "error": "Semantic Scholar rate limit (429). Try again shortly."}
        return {"papers": [], "error": f"HTTP {exc.code}: {exc}"}
    except Exception as exc:
        return {"papers": [], "error": str(exc)}

    papers = []
    for paper in data.get("data", []):
        open_access = paper.get("openAccessPdf") or {}
        pdf_url = open_access.get("url") if isinstance(open_access, dict) else None
        link = pdf_url or paper.get("url") or "No link available"
        abstract = paper.get("abstract") or "No abstract available."
        papers.append({
            "title": paper.get("title", ""),
            "link": link,
            "abstract": abstract[:400] + ("…" if len(abstract) > 400 else ""),
        })
    return {"papers": papers}


# ---------------------------------------------------------------------------
# General web search — Tavily API (CREPE_TAVILY_API_KEY)
# ---------------------------------------------------------------------------

def web_search(query: str, max_results: int = 5) -> dict:
    """Search the web using Tavily. Returns a warning if key is absent."""
    api_key = os.environ.get("CREPE_TAVILY_API_KEY", "").strip()
    if not api_key:
        return {
            "results": [],
            "warning": (
                "CREPE_TAVILY_API_KEY is not set. "
                "Export it in your environment to enable web search."
            ),
        }
    payload = json.dumps({
        "api_key": api_key,
        "query": query,
        "search_depth": "basic",
        "include_answer": False,
        "max_results": max_results,
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.tavily.com/search",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            return {"results": [], "error": "Invalid CREPE_TAVILY_API_KEY (401)."}
        if exc.code == 429:
            return {"results": [], "error": "Tavily rate limit hit (429)."}
        return {"results": [], "error": f"HTTP {exc.code}: {exc}"}
    except Exception as exc:
        return {"results": [], "error": str(exc)}

    results = [
        {"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("content", "")}
        for r in data.get("results", [])
    ]
    return {"results": results}


# ---------------------------------------------------------------------------
# Wikipedia search + read
# ---------------------------------------------------------------------------

def wikipedia_search(query: str, limit: int = 3) -> dict:
    """Search Wikipedia; return titles, URLs, and excerpts."""
    encoded = urllib.parse.quote(query)
    url = (
        f"https://en.wikipedia.org/w/api.php"
        f"?action=query&list=search&srsearch={encoded}&format=json"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except Exception as exc:
        return {"results": [], "error": str(exc)}

    results = []
    for item in data.get("query", {}).get("search", [])[:limit]:
        title = item.get("title", "")
        snippet = html.unescape(re.sub(r"<[^>]+>", "", item.get("snippet", "")))
        page_url = f"https://en.wikipedia.org/wiki/{urllib.parse.quote(title)}"
        results.append({"title": title, "url": page_url, "excerpt": snippet})
    return {"results": results}


def wikipedia_read(title: str, max_chars: int = 15000) -> dict:
    """Fetch full plain-text of a Wikipedia article by exact title."""
    encoded = urllib.parse.quote(title)
    url = (
        f"https://en.wikipedia.org/w/api.php"
        f"?action=query&prop=extracts&explaintext=1&titles={encoded}&format=json"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except Exception as exc:
        return {"title": title, "content": "", "error": str(exc)}

    for page_id, page_info in data.get("query", {}).get("pages", {}).items():
        if page_id == "-1":
            return {"title": title, "content": "", "error": f"Article not found: {title!r}"}
        content = page_info.get("extract") or ""
        return {
            "title": page_info.get("title", title),
            "content": content[:max_chars] + ("…" if len(content) > max_chars else ""),
        }
    return {"title": title, "content": "", "error": "Unexpected empty Wikipedia response"}


# ---------------------------------------------------------------------------
# Webpage fetch — headless browser or urllib fallback
# ---------------------------------------------------------------------------

def _strip_html(raw: str) -> str:
    text = re.sub(
        r"<(style|script|head|meta|noscript|svg).*?>.*?</\1>",
        " ", raw, flags=re.DOTALL | re.IGNORECASE,
    )
    text = re.sub(r"<[^>]+>", " ", text)
    return html.unescape(re.sub(r"\s+", " ", text).strip())


def fetch_webpage(url: str, max_chars: int = 15000) -> dict:
    """Extract readable plain text from a URL.

    Uses the browser at CREPE_HEADLESS_BROWSER_PATH (--headless=new --dump-dom)
    when set; falls back to urllib + HTML stripping with a warning.
    """
    browser_path = os.environ.get("CREPE_HEADLESS_BROWSER_PATH", "").strip()
    warning: Optional[str] = None

    if browser_path and not os.path.isfile(browser_path):
        warning = (
            f"CREPE_HEADLESS_BROWSER_PATH={browser_path!r} does not exist. "
            "Falling back to urllib."
        )
        browser_path = ""

    if browser_path:
        try:
            result = subprocess.run(
                [browser_path, "--headless=new", "--dump-dom", url],
                capture_output=True, text=True, timeout=30,
            )
            content = _strip_html(result.stdout)[:max_chars]
            resp: dict = {"content": content}
            if warning:
                resp["warning"] = warning
            return resp
        except subprocess.TimeoutExpired:
            warning = "Headless browser timed out (30 s). Falling back to urllib."
        except Exception as exc:
            warning = f"Headless browser error ({exc}). Falling back to urllib."

    if not warning:
        warning = (
            "CREPE_HEADLESS_BROWSER_PATH is not set — using urllib fallback. "
            "Output may be incomplete for JavaScript-rendered pages."
        )
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0 (compatible; CREPE/1.0)"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp_obj:
            raw = resp_obj.read().decode("utf-8", errors="replace")
        content = _strip_html(raw)[:max_chars]
    except Exception as exc:
        return {"content": "", "warning": warning, "error": str(exc)}

    return {"content": content, "warning": warning}
