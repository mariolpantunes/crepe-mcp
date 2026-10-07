"""Tests for Semantic Scholar pacing and exponential backoff in academic_search (no network)."""

import io
import json
import unittest
import urllib.error
from email.message import Message
from unittest import mock

from crepe_mcp import research

OK_BODY = json.dumps({"data": [{"title": "Paper", "url": "https://example.org/p", "abstract": "A."}]}).encode()


def http_error(code: int, retry_after: str | None = None) -> urllib.error.HTTPError:
    headers = Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return urllib.error.HTTPError("https://api.semanticscholar.org", code, "err", headers, io.BytesIO(b""))


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestS2Backoff(unittest.TestCase):
    def setUp(self):
        research._s2_last_request = 0.0
        sleep = mock.patch.object(research.time, "sleep")
        self.sleep = sleep.start()
        self.addCleanup(sleep.stop)

    def run_search(self, side_effect):
        with mock.patch.object(research.urllib.request, "urlopen", side_effect=side_effect) as urlopen:
            res = research.academic_search("agents", limit=1)
        return res, urlopen.call_count

    def backoff_sleeps(self):
        return [c.args[0] for c in self.sleep.call_args_list if c.args[0] >= research.S2_BACKOFF_BASE]

    def test_retries_429_then_succeeds(self):
        res, calls = self.run_search([http_error(429), http_error(503), FakeResponse(OK_BODY)])
        self.assertEqual(calls, 3)
        self.assertEqual(res["papers"][0]["title"], "Paper")
        first, second = self.backoff_sleeps()
        self.assertTrue(1.0 <= first < 2.0 and 2.0 <= second < 3.0, (first, second))

    def test_gives_up_after_retries(self):
        res, calls = self.run_search([http_error(429)] * (research.S2_RETRIES + 1))
        self.assertEqual(calls, research.S2_RETRIES + 1)
        self.assertIn("rate limit (429)", res["error"])

    def test_honours_retry_after_with_cap(self):
        self.run_search([http_error(429, "7"), http_error(429, "999"), FakeResponse(OK_BODY)])
        self.assertEqual(self.backoff_sleeps(), [7.0, research.S2_MAX_DELAY])

    def test_client_error_is_not_retried(self):
        res, calls = self.run_search([http_error(400)])
        self.assertEqual(calls, 1)
        self.assertIn("HTTP 400", res["error"])

    def test_paces_back_to_back_requests(self):
        with mock.patch.object(research.time, "monotonic", side_effect=[100.0, 100.0, 100.2, 101.0]):
            self.run_search([FakeResponse(OK_BODY)])
            self.run_search([FakeResponse(OK_BODY)])
        self.assertAlmostEqual(self.sleep.call_args_list[-1].args[0], 0.8)


if __name__ == "__main__":
    unittest.main()
