"""Deterministic unit tests for HTTP caching and request pacing."""

import os
import tempfile
import unittest
from unittest.mock import Mock, call, patch

from xbrl.cache import HttpCache
from xbrl.helper.connection_manager import ConnectionManager


class CacheHelperTest(unittest.TestCase):
    def test_cache_file_downloads_then_serves_cached_content(self):
        with tempfile.TemporaryDirectory(prefix="py-xbrl-cache-test-") as tmp_cache_dir:
            cache_dir: str = os.path.join(tmp_cache_dir, "")
            cache = HttpCache(cache_dir, delay=0)
            test_url = "https://example.test/xml/note.xml"
            expected_path = os.path.join(cache_dir, "example.test", "xml", "note.xml")
            response_content = b"<note><body>cached response</body></note>"
            response = Mock(status_code=200, content=response_content)

            with patch.object(cache.connection_manager, "download", return_value=response) as download:
                self.assertEqual(cache.cache_file(test_url), expected_path)
                self.assertEqual(download.call_count, 1)
                self.assertEqual(cache.cache_file(test_url), expected_path)
                self.assertEqual(download.call_count, 1)

                self.assertTrue(cache.purge_file(test_url))
                self.assertEqual(cache.cache_file(test_url), expected_path)
                self.assertEqual(download.call_count, 2)

            self.assertEqual(download.call_args_list, [call(test_url, headers={}), call(test_url, headers={})])
            self.assertTrue(os.path.isfile(expected_path))
            with open(expected_path, "rb") as cached_file:
                self.assertEqual(cached_file.read(), response_content)
            self.assertTrue(cache.purge_file(test_url))
            self.assertFalse(os.path.isfile(expected_path))


class ConnectionManagerTest(unittest.TestCase):
    def test_rate_limit_delay_is_applied_before_follow_up_download(self):
        manager = ConnectionManager(delay=5000, logs=False)
        manager.next_try_systime_ms = 1000
        test_url = "https://example.test/xml/note.xml"
        response = Mock(status_code=200)

        with (
            patch.object(manager, "_get_systime_ms", side_effect=[1000, 1000, 1000, 1000]),
            patch.object(manager._session, "get", return_value=response) as request,
            patch("xbrl.helper.connection_manager.time.sleep") as sleep,
        ):
            self.assertIs(manager.download(test_url, headers={}), response)
            self.assertIs(manager.download(test_url, headers={}), response)

        self.assertEqual(sleep.call_args_list, [call(0.0), call(5.0)])
        self.assertEqual(
            request.call_args_list,
            [
                call(test_url, headers={}, allow_redirects=True, verify=True),
                call(test_url, headers={}, allow_redirects=True, verify=True),
            ],
        )


if __name__ == "__main__":
    unittest.main()
