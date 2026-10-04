import json
import os
import tempfile
import time
import unittest
from email.message import Message
from pathlib import Path
from unittest.mock import Mock, patch

from mdbs_scraper import http as http_module
from mdbs_scraper.base import LoadedSource, ScrapeOptions
from mdbs_scraper.http import HttpClient, HttpResponse, _retry_after_seconds, build_user_agent
from mdbs_scraper.registry import create_scraper


class RetryAfterTests(unittest.TestCase):
    def test_delta_seconds_form_is_read_as_seconds(self):
        self.assertEqual(_retry_after_seconds("7"), 7.0)

    def test_http_date_form_is_read_as_a_wait_until_that_moment(self):
        in_ten = time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime(time.time() + 10))
        wait = _retry_after_seconds(in_ten)
        self.assertIsNotNone(wait)
        self.assertTrue(0 <= wait <= 11)

    def test_a_date_in_the_past_means_no_wait_and_garbage_means_unknown(self):
        self.assertEqual(_retry_after_seconds("Mon, 01 Jan 2001 00:00:00 GMT"), 0.0)
        self.assertIsNone(_retry_after_seconds("soon"))
        self.assertIsNone(_retry_after_seconds(""))


class UserAgentTests(unittest.TestCase):
    def test_the_contact_comes_from_the_environment(self):
        with patch.dict(os.environ, {"MDBS_SCRAPER_CONTACT": "someone@example.org"}):
            self.assertIn("contact: someone@example.org", build_user_agent())
            self.assertIn("someone@example.org", HttpClient().user_agent)

    def test_without_a_contact_the_agent_says_so_instead_of_promising_one(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIn("no contact configured", build_user_agent())


class ThrottleTests(unittest.TestCase):
    def test_two_clients_share_the_spacing_for_one_host(self):
        # Each scraper builds its own client; before 2026-09-16 each client
        # also had its own throttle, so parallel banks hitting the same host
        # were not spaced at all.
        first, second = HttpClient(request_delay=0.2), HttpClient(request_delay=0.2)
        url = f"https://throttle-test-{time.time_ns()}.example/"
        started = time.monotonic()
        first._throttle(url)
        second._throttle(url)
        self.assertGreaterEqual(time.monotonic() - started, 0.18)

    def test_different_hosts_do_not_wait_for_each_other(self):
        client = HttpClient(request_delay=0.5)
        stamp = time.time_ns()
        client._throttle(f"https://a-{stamp}.example/")
        started = time.monotonic()
        client._throttle(f"https://b-{stamp}.example/")
        self.assertLess(time.monotonic() - started, 0.2)


class FetchLogTests(unittest.TestCase):
    def setUp(self):
        http_module.reset_fetch_log()
        self.addCleanup(http_module.configure_raw_archive, None)
        self.addCleanup(http_module.reset_fetch_log)

    def _fake_send(self, body: bytes):
        headers = Message()
        headers["Content-Type"] = "application/json"
        return Mock(return_value=HttpResponse("https://example.org/final", 200, headers, body))

    def test_every_fetch_is_hashed_for_the_manifest(self):
        client = HttpClient(request_delay=0)
        with patch.object(HttpClient, "_send_with_retries", self._fake_send(b'{"a": 1}')):
            client.get("https://example.org/data")
        [entry] = http_module.fetch_log()
        self.assertEqual(entry["url"], "https://example.org/data")
        self.assertEqual(entry["final_url"], "https://example.org/final")
        self.assertEqual(entry["bytes"], 8)
        self.assertEqual(len(entry["sha256"]), 64)

    def test_save_raw_writes_the_body_and_a_sidecar(self):
        with tempfile.TemporaryDirectory() as directory:
            http_module.configure_raw_archive(directory)
            client = HttpClient(request_delay=0)
            with patch.object(HttpClient, "_send_with_retries", self._fake_send(b'{"a": 1}')):
                client.get("https://example.org/data")
            body = [path for path in Path(directory).iterdir() if not path.name.endswith(".meta.json")][0]
            sidecar = json.loads(body.with_suffix(".meta.json").read_text(encoding="utf-8"))
            self.assertEqual(body.read_bytes(), b'{"a": 1}')
            self.assertEqual(sidecar["url"], "https://example.org/data")
            self.assertEqual(sidecar["content_type"], "application/json")
            self.assertFalse(list(Path(directory).glob("*.part")))


class CkanPagingTests(unittest.TestCase):
    def test_packages_beyond_the_first_search_page_are_fetched(self):
        scraper = create_scraper("caf", ScrapeOptions())
        first_page = {"result": {"count": 3, "results": [{"name": "p1"}, {"name": "p2"}]}}
        second_page = {"result": {"count": 3, "results": [{"name": "p3"}]}}
        scraper.client.get = Mock(return_value=Mock(body=json.dumps(second_page).encode()))
        source = LoadedSource(json.dumps(first_page).encode(), "https://registry.example/search?rows=2", "json")
        with patch(
            "mdbs_scraper.adapters.common.rows_from_ckan_resources",
            side_effect=lambda client, resources: [{"project_name": "x"}],
        ) as rows_for:
            rows = scraper.rows_from_source(source)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows_for.call_count, 3)
        self.assertEqual(scraper.client.get.call_args.kwargs["params"], {"start": 2})


if __name__ == "__main__":
    unittest.main()
