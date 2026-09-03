"""Research and web utility functions for the CREPE MCP server.

Environment variables (all prefixed CREPE_):
  CREPE_TAVILY_API_KEY           — enables web_search; warning returned if absent.
  CREPE_SEMANTIC_SCHOLAR_API_KEY — optional key for higher rate limits.
  CREPE_HEADLESS_BROWSER_PATH    — path to a Chromium-compatible browser used by
                                   Playwright for fetch_webpage (e.g. /usr/bin/chromium).
                                   Falls back to urllib if unset or unavailable.

The `browser_lifespan` context manager is exported here for use as the FastMCP lifespan.
"""
from __future__ import annotations

import asyncio
import atexit
import glob
import html
import json
import os
import re
import signal
import subprocess
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from playwright.async_api import Browser  # noqa: F401

# Lazily initialized when fetch_webpage is invoked.
_playwright_instance: Any = None
_playwright_browser: Any = None
_browser_lock: asyncio.Lock | None = None
_browser_pgid: int | None = None


def _get_browser_lock() -> asyncio.Lock:
    global _browser_lock
    if _browser_lock is None:
        _browser_lock = asyncio.Lock()
    return _browser_lock




# ---------------------------------------------------------------------------
# Academic search — Semantic Scholar
# ---------------------------------------------------------------------------

def academic_search(query: str, limit: int = 5) -> dict:
    """Search Semantic Scholar for academic papers. Reads CREPE_SEMANTIC_SCHOLAR_API_KEY if set."""
    limit = max(1, min(limit, 100))
    encoded = urllib.parse.quote(query)
    url = (
        f"https://api.semanticscholar.org/graph/v1/paper/search"
        f"?query={encoded}&limit={limit}"
        f"&fields=title,abstract,openAccessPdf,url"
    )
    headers = {"User-Agent": "Mozilla/5.0 (compatible; CREPE/1.0)"}
    api_key = os.environ.get("CREPE_SEMANTIC_SCHOLAR_API_KEY", "").strip()
    if api_key:
        headers["x-api-key"] = api_key

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            return {
                "papers": [],
                "error": (
                    "Semantic Scholar rate limit (429). Try again shortly or "
                    "configure CREPE_SEMANTIC_SCHOLAR_API_KEY."
                ),
            }

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


def arxiv_search(query: str, limit: int = 5) -> dict:
    """Search arXiv for preprints via Atom XML API."""
    limit = max(1, min(limit, 100))
    encoded = urllib.parse.quote(query)
    url = f"https://export.arxiv.org/api/query?search_query=all:{encoded}&start=0&max_results={limit}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; CREPE/1.0)"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw_xml = resp.read().decode("utf-8")
        root = ET.fromstring(raw_xml)
    except Exception as exc:
        return {"papers": [], "error": f"arXiv query failed: {exc}"}

    ns = {"atom": "http://www.w3.org/2005/Atom"}
    papers = []
    for entry in root.findall("atom:entry", ns):
        title = (entry.findtext("atom:title", "", ns) or "").strip().replace("\n", " ")
        summary = (entry.findtext("atom:summary", "", ns) or "").strip().replace("\n", " ")
        published = entry.findtext("atom:published", "", ns) or ""
        pdf_link = ""
        for link in entry.findall("atom:link", ns):
            if link.get("title") == "pdf" or link.get("type") == "application/pdf":
                pdf_link = link.get("href", "")
                break
        if not pdf_link:
            id_text = entry.findtext("atom:id", "", ns) or ""
            if id_text:
                pdf_link = id_text.replace("/abs/", "/pdf/") + ".pdf"

        if not pdf_link:
            pdf_link = "No link available"

        papers.append({
            "title": title,
            "published": published[:10],
            "link": pdf_link,
            "abstract": summary[:400] + ("…" if len(summary) > 400 else ""),
        })
    return {"papers": papers}



# ---------------------------------------------------------------------------
# General web search — Tavily API (CREPE_TAVILY_API_KEY)
# ---------------------------------------------------------------------------

def web_search(query: str, max_results: int = 5) -> dict:
    """Search the web using Tavily. Returns a warning if key is absent."""
    max_results = max(1, min(max_results, 50))
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
    limit = max(1, min(limit, 50))
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
# Webpage fetch — Playwright (shared browser) or urllib fallback
# ---------------------------------------------------------------------------

def _strip_html(raw: str) -> str:
    text = re.sub(
        r"<(style|script|head|meta|noscript|svg).*?>.*?</\1>",
        " ", raw, flags=re.DOTALL | re.IGNORECASE,
    )
    text = re.sub(r"<[^>]+>", " ", text)
    return html.unescape(re.sub(r"\s+", " ", text).strip())


async def fetch_webpage(url: str, max_chars: int = 15000) -> dict:
    """Extract readable plain text from a URL (http/https only).

    Uses the shared Playwright browser (launched at startup from
    CREPE_HEADLESS_BROWSER_PATH) when available, which means the browser
    is already warm and JS is fully rendered. Each call gets its own
    isolated BrowserContext that is closed after the call.

    Falls back to urllib + HTML stripping with a warning when no browser
    is configured or Playwright is unavailable.
    """
    scheme = urllib.parse.urlsplit(url).scheme.lower()
    if scheme not in ("http", "https"):
        return {
            "content": "",
            "error": (
                f"Unsupported URL scheme {scheme!r}; only http:// and https:// "
                "are allowed (e.g. file:// would disclose local files)."
            ),
        }

    browser = await _ensure_browser()
    if browser is not None:
        context = None
        try:
            # Each call gets an isolated context (cookies, cache, storage)
            # so concurrent fetches don't interfere with each other.
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (compatible; CREPE/1.0)"
            )
            page = await context.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            # inner_text gives rendered visible text — much cleaner than
            # stripping raw HTML.
            try:
                content = await page.inner_text("body", timeout=5_000)
            except Exception:
                # Fallback: get full HTML and strip it manually.
                raw_html = await page.content()
                content = _strip_html(raw_html)
            return {"content": content[:max_chars]}
        except Exception as exc:
            return {"content": "", "error": f"Playwright fetch failed: {exc}"}
        finally:
            if context is not None:
                try:
                    await context.close()
                except Exception:
                    pass

    # --- urllib fallback ---
    warning: str | None = (
        "CREPE_HEADLESS_BROWSER_PATH is not set or Playwright browser is not "
        "running — using urllib fallback. Output may be incomplete for "
        "JavaScript-rendered pages."
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

# ---------------------------------------------------------------------------
# Browser lifespan — shared between server_research.py (standalone) and
# server.py (monolith). Import and pass as `lifespan=` to FastMCP.
# ---------------------------------------------------------------------------

def _kill_browser_group() -> None:
    """Kill the entire Chromium process group (idempotent).

    The PGID self-kill guard checks that the browser's process group differs
    from the Python server's own group — killing our own group would terminate
    the server process and every coroutine running inside it.
    """
    global _browser_pgid
    if _browser_pgid is not None:
        if _browser_pgid != os.getpgrp():
            try:
                os.killpg(_browser_pgid, signal.SIGKILL)
            except OSError:
                pass
        _browser_pgid = None


def _emergency_exit(_signum: int, _frame: object) -> None:
    """Module-level signal handler: immediate exit on SIGTERM/SIGINT."""
    _kill_browser_group()
    os._exit(0)


try:
    signal.signal(signal.SIGTERM, _emergency_exit)
    signal.signal(signal.SIGINT, _emergency_exit)
except (ValueError, AttributeError):
    pass


async def _ensure_browser() -> Any:
    """Lazily launch Chromium only when fetch_webpage is invoked."""
    global _playwright_instance, _playwright_browser, _browser_pgid
    if _playwright_browser is not None:
        return _playwright_browser

    async with _get_browser_lock():
        if _playwright_browser is not None:
            return _playwright_browser

        browser_path = os.environ.get("CREPE_HEADLESS_BROWSER_PATH", "").strip()
        if not browser_path or not os.path.isfile(browser_path):
            return None

        from playwright.async_api import async_playwright
        pw = await async_playwright().start()
        _playwright_instance = pw

        profiles_before: set[str] = set(
            glob.glob("/tmp/playwright_chromiumdev_profile-*")
        )
        browser = await pw.chromium.launch(
            headless=True,
            executable_path=browser_path,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ],
        )
        _playwright_browser = browser

        profiles_after: set[str] = set(
            glob.glob("/tmp/playwright_chromiumdev_profile-*")
        )
        new_profiles = profiles_after - profiles_before
        if new_profiles:
            profile_dir = next(iter(new_profiles))
            try:
                result = subprocess.run(
                    ["pgrep", "-f", profile_dir],
                    capture_output=True, text=True, timeout=3,
                )
                pids = [int(p) for p in result.stdout.split() if p.isdigit()]
                if pids:
                    _browser_pgid = os.getpgid(min(pids))
            except (OSError, ValueError, subprocess.SubprocessError):
                pass

        atexit.register(_kill_browser_group)
        return _playwright_browser


@asynccontextmanager
async def browser_lifespan(_server: object):  # type: ignore[type-arg]
    """FastMCP lifespan context manager: manage the Playwright browser lifecycle.

    Startup is instant: Chromium is lazily launched only when fetch_webpage
    is invoked. On server shutdown, any running browser instance is stopped.
    """
    try:
        yield
    finally:
        global _playwright_browser, _playwright_instance
        _playwright_browser = None
        _kill_browser_group()
        if _playwright_instance is not None:
            try:
                await _playwright_instance.stop()
            except Exception:
                pass
            _playwright_instance = None
