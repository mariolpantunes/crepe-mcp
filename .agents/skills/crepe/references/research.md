Read when: finding papers, web pages or background knowledge (server `crepe-research`, 6 tools).

# Research

| Need | Tool | Notes |
|------|------|-------|
| Peer-reviewed papers | `academic_search(query, limit=5)` | Semantic Scholar. Works without a key; `CREPE_SEMANTIC_SCHOLAR_API_KEY` only avoids 429s. |
| Preprints (CS, physics, math, AI) | `arxiv_search(query, limit=5)` | No key. |
| Current web data and news | `web_search(query, max_results=5)` | Tavily. Needs `CREPE_TAVILY_API_KEY`. |
| Background on a topic | `wikipedia_search(query, limit=3)`, then `wikipedia_read(title, max_chars=15000)` | Search first, read the exact title. |
| Full text of a known URL | `fetch_webpage(url, max_chars=15000)` | http/https only. Chromium renders JavaScript pages; without a browser it falls back to plain HTML stripping and says so in a warning. |

## Rules

- Cite what you found: title, authors, year and URL from the result, never from memory.
- `web_search` without a key returns an empty result with a `warning` field, not an error. Tell the user the key is missing from `~/.config/crepe-mcp/.env` and continue with the other tools. Never ask for the key in the chat.
- Raise `limit` or rephrase the query before concluding that nothing exists. Search results are summaries: use `fetch_webpage` or `wikipedia_read` for the text you quote.
- `max_chars` truncates long pages; ask for a larger value only when the part you need is cut.
