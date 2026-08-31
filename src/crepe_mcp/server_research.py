"""CREPE — Research sub-server (Group B, 6 tools).

Research and web utilities: Semantic Scholar, arXiv, Tavily web search,
Wikipedia, and Playwright-powered full-page fetching.

Can be run as a standalone MCP server:
    uv run crepe-research

Or imported and mounted in the full CREPE monolith (crepe-mcp). The Playwright
browser is managed by `research.browser_lifespan` — the same lifespan function
is used by both this standalone server and the monolith.

Tools
-----
Group B (6):
  academic_search, arxiv_search, web_search, wikipedia_search, wikipedia_read,
  fetch_webpage

Environment variables (all CREPE_ prefixed):
  CREPE_TAVILY_API_KEY            — Tavily API key for web_search
  CREPE_SEMANTIC_SCHOLAR_API_KEY  — optional; avoids 429 rate limits
  CREPE_HEADLESS_BROWSER_PATH     — Chromium path for fetch_webpage
"""
from __future__ import annotations

from fastmcp import FastMCP

from crepe_mcp import research
from crepe_mcp.runner import run_server

RESEARCH_INSTRUCTIONS = """\
CREPE Research Engine Guidelines:
1. Use academic_search (Semantic Scholar) or arxiv_search for peer-reviewed papers and preprints.
2. Use web_search for current web data and news (requires CREPE_TAVILY_API_KEY).
3. Use wikipedia_search followed by wikipedia_read to extract background knowledge.
4. Use fetch_webpage to extract full article text from specific URLs.
"""

mcp = FastMCP("crepe-research", instructions=RESEARCH_INSTRUCTIONS, lifespan=research.browser_lifespan)

@mcp.tool
def academic_search(query: str, limit: int = 5) -> dict:
    """Search Semantic Scholar for academic papers. No API key required.

    Returns up to `limit` results with title, link (open-access PDF preferred),
    and truncated abstract (400 chars).
    """
    return research.academic_search(query, limit=limit)


@mcp.tool
def arxiv_search(query: str, limit: int = 5) -> dict:
    """Search arXiv for preprints (AI, CS, Physics, Math). No API key required.

    Returns title, publication date, PDF link, and summary.
    """
    return research.arxiv_search(query, limit=limit)



@mcp.tool
def web_search(query: str, max_results: int = 5) -> dict:
    """Search the web via the Tavily API.

    Requires CREPE_TAVILY_API_KEY in the environment. Returns an empty result
    with a warning field (not an error) if the key is absent, so the agent
    can surface the message gracefully.
    """
    return research.web_search(query, max_results=max_results)


@mcp.tool
def wikipedia_search(query: str, limit: int = 3) -> dict:
    """Search Wikipedia and return matching article titles, URLs, and excerpts.

    Pass the returned title to wikipedia_read to fetch the full article.
    """
    return research.wikipedia_search(query, limit=limit)


@mcp.tool
def wikipedia_read(title: str, max_chars: int = 15000) -> dict:
    """Fetch the full plain-text body of a Wikipedia article.

    `title` should be the exact article title from wikipedia_search.
    Content is truncated to `max_chars` characters (default 15 000).
    """
    return research.wikipedia_read(title, max_chars=max_chars)


@mcp.tool
async def fetch_webpage(url: str, max_chars: int = 15000) -> dict:
    """Extract readable plain text from a URL (http/https only). Uses the
    shared Playwright browser (system Chromium at CREPE_HEADLESS_BROWSER_PATH)
    when available, so JS-rendered pages are fully loaded. Falls back to
    urllib + HTML stripping with a warning when no browser is configured."""
    return await research.fetch_webpage(url, max_chars=max_chars)

def main() -> None:
    """Console-script entrypoint for the standalone crepe-research server."""
    run_server(mcp)


if __name__ == "__main__":
    main()
