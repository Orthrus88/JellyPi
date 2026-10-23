import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cleanup"))

from qbit_cleanup import (
    imported_video_names,
    stale_reason,
    target_keys,
    torrent_video_names,
    usenet_target_keys,
)


class CleanupSafetyTests(unittest.TestCase):
    def test_only_imports_after_torrent_was_added_count(self):
        added = int(datetime.now(timezone.utc).timestamp())
        old = datetime.fromtimestamp(added, timezone.utc) - timedelta(days=1)
        new = datetime.fromtimestamp(added, timezone.utc) + timedelta(minutes=1)
        records = [
            {
                "eventType": "downloadFolderImported",
                "date": old.isoformat(),
                "data": {"droppedPath": "/downloads/Movie.mkv"},
            },
            {
                "eventType": "downloadFolderImported",
                "date": new.isoformat(),
                "data": {"droppedPath": "/downloads/Episode.mkv"},
            },
        ]
        self.assertEqual(imported_video_names(records, added), {"episode.mkv"})

    def test_main_video_files_are_required_but_samples_are_ignored(self):
        files = [
            {"name": "Show/Show.S01E01.mkv"},
            {"name": "Show/Show.S01E02.MP4"},
            {"name": "Show/Sample/preview.mkv"},
            {"name": "Show/Show.S01E01.sample.mkv"},
            {"name": "Show/readme.txt"},
        ]
        self.assertEqual(
            torrent_video_names(files), {"show.s01e01.mkv", "show.s01e02.mp4"}
        )

    def test_active_usenet_job_identifies_duplicate_torrent_target(self):
        records = [
            {
                "protocol": "usenet",
                "downloadId": "sab-job",
                "downloadClient": "SABnzbd",
                "episodeId": 42,
            },
            {
                "protocol": "usenet",
                "status": "downloadClientUnavailable",
                "episodeId": 99,
            },
        ]
        self.assertEqual(usenet_target_keys("sonarr", records), {("episode", 42)})
        self.assertEqual(target_keys("radarr", {"movieId": 7}), {("movie", 7)})

    def test_slow_partial_torrent_is_recoverable_after_half_hour(self):
        now = 10_000
        torrent = {
            "progress": 0.05,
            "state": "stalledDL",
            "added_on": now - 1900,
            "last_activity": now - 120,
            "dlspeed": 0,
            "downloaded": 1024,
            "num_seeds": 0,
            "num_complete": 3,
        }
        self.assertIn("partial download", stale_reason(torrent, now))

    def test_recent_or_usefully_fast_partial_torrent_is_left_alone(self):
        now = 10_000
        recent = {
            "progress": 0.05,
            "state": "stalledDL",
            "added_on": now - 1200,
            "last_activity": now - 60,
            "dlspeed": 0,
            "downloaded": 1024,
            "num_seeds": 0,
        }
        fast = dict(recent, added_on=now - 3600, dlspeed=1024 * 1024)
        self.assertIsNone(stale_reason(recent, now))
        self.assertIsNone(stale_reason(fast, now))


if __name__ == "__main__":
    unittest.main()
