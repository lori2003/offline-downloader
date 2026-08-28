import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import download


PLAYER_HTML = """
<html><body><script>
window.canPlayFHD = true;
const video = {
  url: 'https:\\/\\/vixcloud.co\\/playlist\\/42.m3u8?b=1',
  'token': 'abc_123',
  'expires': '2000000000'
};
</script></body></html>
"""


class URLValidationTests(unittest.TestCase):
    def test_accepts_vixcloud_and_subdomains(self):
        self.assertEqual(
            download.validate_video_url("https://vixcloud.co/embed/42"),
            "https://vixcloud.co/embed/42",
        )
        self.assertEqual(
            download.validate_video_url("https://player.vixcloud.co/embed/42"),
            "https://player.vixcloud.co/embed/42",
        )

    def test_rejects_lookalike_and_non_web_urls(self):
        rejected = (
            "https://vixcloud.co.example.com/embed/42",
            "file:///etc/passwd",
            "https://user:pass@vixcloud.co/embed/42",
            "https://vixcloud.co:8443/embed/42",
        )
        for url in rejected:
            with self.subTest(url=url), self.assertRaises(download.URLValidationError):
                download.validate_video_url(url)


class ResolverTests(unittest.TestCase):
    def test_resolves_signed_manifest_and_preserves_existing_query(self):
        resolved = download.resolve_video_url(
            "https://vixcloud.co/embed/42", fetch_html=lambda _: PLAYER_HTML
        )

        self.assertTrue(resolved.extracted_from_player)
        self.assertEqual(resolved.referer, "https://vixcloud.co/embed/42")
        self.assertEqual(
            resolved.url,
            "https://vixcloud.co/playlist/42.m3u8?b=1&token=abc_123"
            "&expires=2000000000&h=1",
        )

    def test_passes_direct_playlist_through_without_fetching(self):
        resolved = download.resolve_video_url(
            "https://vixcloud.co/playlist/42.m3u8?token=x",
            fetch_html=lambda _: self.fail("fetch_html must not be called"),
        )
        self.assertFalse(resolved.extracted_from_player)
        self.assertEqual(
            resolved.url, "https://vixcloud.co/playlist/42.m3u8?token=x"
        )

    def test_unknown_player_shape_falls_back_to_yt_dlp(self):
        url = "https://vixcloud.co/embed/42"
        resolved = download.resolve_video_url(url, fetch_html=lambda _: "<html></html>")
        self.assertEqual(resolved.url, url)
        self.assertFalse(resolved.extracted_from_player)


class DownloadTests(unittest.TestCase):
    def test_download_returns_new_media_file(self):
        class FakeYoutubeDL:
            def __init__(self, options):
                self.options = options

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def extract_info(self, _url, download=True):
                if download is not True:
                    raise AssertionError("download=True expected")
                template = self.options["outtmpl"]
                output = Path(
                    template.replace("%(title).150B", "Film")
                    .replace("%(id)s", "42")
                    .replace("%(ext)s", "mp4")
                )
                output.write_bytes(b"fake media")

        direct_url = "https://vixcloud.co/playlist/42.m3u8?token=x"
        with tempfile.TemporaryDirectory() as temporary_directory:
            with patch.object(download.yt_dlp, "YoutubeDL", FakeYoutubeDL):
                files = download.download_video(direct_url, temporary_directory)

        self.assertEqual([path.name for path in files], ["Film [42].mp4"])


if __name__ == "__main__":
    unittest.main()
