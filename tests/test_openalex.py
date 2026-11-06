"""tests/test_openalex.py — openalex_search with mocked HTTP: parsing, key handling, retries, budget errors."""

import io
import json
import os
import unittest
import urllib.error
from unittest import mock

from crepe_mcp import research

WORK = {
    "display_name": "Routine mining",
    "publication_year": 2021,
    "doi": "https://doi.org/10.1/x",
    "cited_by_count": 7,
    "primary_location": {"source": {"display_name": "IEEE Access"}, "landing_page_url": "https://l"},
    "open_access": {"oa_url": "https://oa/x.pdf"},
    "abstract_inverted_index": {"Smart": [0], "homes": [1], "learn": [2], "routines": [3, 5], "from": [4], "logs": [6]},
}


def reply(payload: dict):
    ctx = mock.MagicMock()
    ctx.__enter__.return_value.read.return_value = json.dumps(payload).encode()
    return ctx


def http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://api.openalex.org", code, "err", {}, io.BytesIO(b""))  # type: ignore[arg-type]


class TestOpenAlex(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(research.time, "sleep")
        patcher.start()
        self.addCleanup(patcher.stop)
        env = mock.patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("CREPE_OPENALEX_API_KEY", None)

    def search(self, side_effect, **kwargs):
        urls = []

        def fake(req, timeout=None):
            urls.append(req.get_full_url())
            item = side_effect.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        with mock.patch.object(research.urllib.request, "urlopen", side_effect=fake):
            return research.openalex_search("smart home", **kwargs), urls

    def test_fields_and_abstract_are_rebuilt(self):
        out, urls = self.search([reply({"results": [WORK]})], limit=3)
        paper = out["papers"][0]
        self.assertEqual(paper["title"], "Routine mining")
        self.assertEqual((paper["year"], paper["venue"], paper["cited_by"]), (2021, "IEEE Access", 7))
        self.assertEqual(paper["link"], "https://oa/x.pdf")
        self.assertEqual(paper["abstract"], "Smart homes learn routines from routines logs")
        self.assertIn("per-page=3", urls[0])
        self.assertNotIn("api_key", urls[0])

    def test_link_falls_back_to_doi_and_missing_fields_are_safe(self):
        bare = {"display_name": "T", "doi": "https://doi.org/10.1/y", "primary_location": None, "open_access": None}
        out, _ = self.search([reply({"results": [bare]})])
        paper = out["papers"][0]
        self.assertEqual(paper["link"], "https://doi.org/10.1/y")
        self.assertEqual(paper["abstract"], "No abstract available.")
        self.assertEqual(paper["venue"], "")

    def test_long_abstract_is_truncated(self):
        work = dict(WORK, abstract_inverted_index={f"w{i}": [i] for i in range(300)})
        out, _ = self.search([reply({"results": [work]})])
        self.assertTrue(out["papers"][0]["abstract"].endswith("…"))

    def test_key_is_sent_when_set(self):
        os.environ["CREPE_OPENALEX_API_KEY"] = "k-123"
        _, urls = self.search([reply({"results": []})])
        self.assertIn("api_key=k-123", urls[0])

    def test_retries_on_429_then_succeeds(self):
        out, urls = self.search([http_error(429), reply({"results": [WORK]})])
        self.assertEqual(len(urls), 2)
        self.assertEqual(len(out["papers"]), 1)

    def test_budget_error_names_the_key_after_retries(self):
        out, urls = self.search([http_error(429)] * 3)
        self.assertEqual(len(urls), research.OPENALEX_RETRIES + 1)
        self.assertEqual(out["papers"], [])
        self.assertIn("CREPE_OPENALEX_API_KEY", out["error"])

    def test_payment_required_is_not_retried(self):
        out, urls = self.search([http_error(402)])
        self.assertEqual(len(urls), 1)
        self.assertIn("CREPE_OPENALEX_API_KEY", out["error"])

    def test_other_http_error_is_reported(self):
        out, urls = self.search([http_error(400)])
        self.assertEqual(len(urls), 1)
        self.assertIn("HTTP 400", out["error"])

    def test_network_error_is_reported(self):
        out, _ = self.search([urllib.error.URLError("down")] * 3)
        self.assertEqual(out["papers"], [])
        self.assertIn("down", out["error"])

    def test_limit_is_clamped(self):
        _, urls = self.search([reply({"results": []})], limit=1000)
        self.assertIn("per-page=100", urls[0])


class TestOpenAlexTool(unittest.TestCase):
    def test_tool_is_registered(self):
        import asyncio

        from crepe_mcp.server_research import mcp

        names = {t.name for t in asyncio.run(mcp.list_tools())}
        self.assertIn("openalex_search", names)
        self.assertEqual(len(names), 7)


if __name__ == "__main__":
    unittest.main()
