import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class AudiobookProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
        cls.bootstrap = (ROOT / "scripts" / "bootstrap.sh").read_text(
            encoding="utf-8"
        )

    def test_optional_services_share_the_audiobooks_profile(self):
        for service in ("audiobookshelf", "chaptarr", "audiobookrequest"):
            start = self.compose.index(f"  {service}:")
            block = self.compose[start : start + 450]
            self.assertIn("- audiobooks", block)

    def test_audiobook_media_and_config_directories_are_bootstrapped(self):
        self.assertIn('"$MEDIA_ROOT/Audiobooks"', self.bootstrap)
        self.assertIn('"$CONFIG_ROOT/audiobookshelf/config"', self.bootstrap)
        self.assertIn('"$CONFIG_ROOT/chaptarr"', self.bootstrap)
        self.assertIn('"$CONFIG_ROOT/audiobookrequest"', self.bootstrap)

    def test_request_service_contains_no_baked_in_credentials(self):
        start = self.compose.index("  audiobookrequest:")
        block = self.compose[start : start + 600]
        self.assertNotIn("INIT_ROOT_USERNAME", block)
        self.assertNotIn("INIT_ROOT_PASSWORD", block)


if __name__ == "__main__":
    unittest.main()
