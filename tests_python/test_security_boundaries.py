from __future__ import annotations

import copy
import hashlib
import io
import json
import tarfile
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request

from song_pick.archive import regular_member_file
from song_pick.config import Config, DEFAULT_CONFIG_PATH, Paths, Source, load_config
from song_pick.download import download_source
from song_pick.fetch_popularity import request_popularity
from song_pick.network import RestrictedRedirectHandler, SOURCE_HOSTS
from song_pick.pipeline import fingerprint


class ConfigSecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.raw = json.loads(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _load(self, raw: dict[str, Any]) -> None:
        path = self.root / "config.json"
        path.write_text(json.dumps(raw), encoding="utf-8")
        load_config(path)

    def test_rejects_source_filename_traversal(self) -> None:
        raw = copy.deepcopy(self.raw)
        raw["sources"][0]["filename"] = "../outside.tar.zst"
        with self.assertRaisesRegex(ValueError, "plain filename"):
            self._load(raw)

    def test_rejects_unapproved_source_hosts_and_schemes(self) -> None:
        for url in ("file:///etc/passwd", "https://127.0.0.1/source.tar.zst"):
            with self.subTest(url=url):
                raw = copy.deepcopy(self.raw)
                raw["sources"][0]["url"] = url
                with self.assertRaisesRegex(ValueError, "HTTPS|not approved"):
                    self._load(raw)

    def test_rejects_arbitrary_token_environment_and_endpoint(self) -> None:
        raw = copy.deepcopy(self.raw)
        raw["popularity"]["tokenEnvironment"] = "AWS_SECRET_ACCESS_KEY"
        with self.assertRaisesRegex(ValueError, "tokenEnvironment"):
            self._load(raw)

        raw = copy.deepcopy(self.raw)
        raw["popularity"]["url"] = "https://example.com/collect"
        with self.assertRaisesRegex(ValueError, "not approved"):
            self._load(raw)

    def test_source_size_pin_does_not_invalidate_completed_imports(self) -> None:
        original = load_config()
        raw = copy.deepcopy(original.raw)
        raw["sources"][0]["size"] += 1
        changed = Config(raw, DEFAULT_CONFIG_PATH)
        self.assertEqual(
            fingerprint(original, "import-acousticbrainz"),
            fingerprint(changed, "import-acousticbrainz"),
        )


class NetworkSecurityTests(unittest.TestCase):
    def test_redirect_strips_authorization_between_approved_origins(self) -> None:
        handler = RestrictedRedirectHandler(SOURCE_HOSTS, "source URL")
        request = Request(
            "https://data.metabrainz.org/source",
            headers={"Authorization": "Token secret"},
        )
        redirected = handler.redirect_request(
            request,
            io.BytesIO(),
            302,
            "Found",
            Message(),
            "https://ftp.musicbrainz.org/source",
        )
        self.assertIsNotNone(redirected)
        self.assertIsNone(redirected.get_header("Authorization"))

    def test_redirect_rejects_unapproved_origin(self) -> None:
        handler = RestrictedRedirectHandler(SOURCE_HOSTS, "source URL")
        request = Request("https://data.metabrainz.org/source")
        with self.assertRaises(HTTPError) as caught:
            handler.redirect_request(
                request,
                io.BytesIO(),
                302,
                "Found",
                Message(),
                "https://example.com/source",
            )
        caught.exception.close()


class InputLimitTests(unittest.TestCase):
    def test_download_refuses_more_than_the_pinned_size(self) -> None:
        content = b"good"
        source = Source(
            name="test",
            url="https://data.metabrainz.org/test.tar.zst",
            filename="test.tar.zst",
            size=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            snapshot="2026-09-18",
            license="CC0-1.0",
        )
        with tempfile.TemporaryDirectory() as temporary:
            paths = Paths.under(Path(temporary))
            paths.create()
            with patch("song_pick.download.urlopen", return_value=io.BytesIO(content + b"!")):
                with self.assertRaisesRegex(RuntimeError, "exceeded pinned size"):
                    download_source(source, paths, retries=0)
            self.assertFalse((paths.raw / source.filename).exists())

    def test_popularity_response_has_a_hard_byte_limit(self) -> None:
        settings = copy.deepcopy(load_config().popularity)
        with (
            patch("song_pick.fetch_popularity.MAX_RESPONSE_BYTES", 4),
            patch("song_pick.fetch_popularity.urlopen", return_value=io.BytesIO(b"12345")),
        ):
            with self.assertRaisesRegex(RuntimeError, "exceeded 4 bytes"):
                request_popularity([], settings)

    def test_archive_member_has_a_hard_size_limit(self) -> None:
        member = tarfile.TarInfo("oversized")
        member.size = 5
        with patch("song_pick.archive.MAX_ARCHIVE_MEMBER_BYTES", 4):
            with self.assertRaisesRegex(RuntimeError, "unsafe size"):
                regular_member_file(Mock(), member)
