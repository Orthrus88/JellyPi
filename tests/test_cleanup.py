import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cleanup"))

from qbit_cleanup import imported_video_names, torrent_video_names


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


if __name__ == "__main__":
    unittest.main()
