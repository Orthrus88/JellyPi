import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "recovery"))

import download_recovery as recovery


ROOT = Path(__file__).resolve().parents[1]


class RecoveryRoutingTests(unittest.TestCase):
    def test_usenet_profile_packages_recovery_state(self):
        compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
        bootstrap = (ROOT / "scripts" / "bootstrap.sh").read_text(encoding="utf-8")
        self.assertIn("download-recovery:", compose)
        self.assertIn("profiles:\n      - usenet", compose)
        self.assertIn("download-recovery}", bootstrap)

    def test_routes_common_sab_categories(self):
        for category in ("tv", "sonarr", "series"):
            self.assertEqual(recovery.manager_for_category(category), "sonarr")
        for category in ("movie", "movies", "radarr"):
            self.assertEqual(recovery.manager_for_category(category), "radarr")
        self.assertIsNone(recovery.manager_for_category("other"))

    def test_arr_owns_a_tracked_failure(self):
        self.assertTrue(recovery.manager_owns([{"downloadId": "ABC", "statusMessages": []}], "abc"))

    def test_arr_does_not_own_a_manual_sab_retry(self):
        records = [{
            "downloadId": "abc",
            "statusMessages": [{"messages": ["Download wasn't grabbed by Sonarr, skipping"]}],
        }]
        self.assertFalse(recovery.manager_owns(records, "ABC"))


class RecoverySearchTests(unittest.TestCase):
    @patch.object(recovery, "arr_post")
    @patch.object(recovery, "arr_get")
    def test_tv_searches_only_missing_monitored_episodes(self, get, post):
        get.side_effect = [
            {"episodes": [{"id": 10}, {"id": 11}, {"id": 12}]},
            {"id": 10, "monitored": True, "hasFile": False},
            {"id": 11, "monitored": True, "hasFile": True},
            {"id": 12, "monitored": False, "hasFile": False},
        ]
        post.return_value = {"id": 99}
        result = recovery.recover_tv("Show.S01E01", "key")
        self.assertEqual(result["ids"], [10])
        self.assertEqual(post.call_args.args[3], {"name": "EpisodeSearch", "episodeIds": [10]})

    @patch.object(recovery, "arr_post")
    @patch.object(recovery, "arr_get")
    def test_movie_with_existing_file_is_not_searched(self, get, post):
        get.side_effect = [{"movie": {"id": 7}}, {"hasFile": True, "monitored": True}]
        self.assertIsNone(recovery.recover_movie("Movie.2025", "key"))
        post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
