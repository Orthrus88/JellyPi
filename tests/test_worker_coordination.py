import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "encoder"))

import media_encoder


class WorkerCoordinationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.originals = {
            name: getattr(media_encoder, name)
            for name in (
                "STATE_DIR",
                "STATUS",
                "CLAIM_DIR",
                "WORKER_NAME",
                "PREFERRED_STATUS",
                "PREFERRED_TIMEOUT",
                "CLAIM_TIMEOUT",
            )
        }
        root = Path(self.temporary.name)
        media_encoder.STATE_DIR = root
        media_encoder.STATUS = root / "status-test.json"
        media_encoder.CLAIM_DIR = root / "encoder.claim"
        media_encoder.WORKER_NAME = "test-worker"
        media_encoder.PREFERRED_STATUS = ""
        media_encoder.PREFERRED_TIMEOUT = 300
        media_encoder.CLAIM_TIMEOUT = 600

    def tearDown(self):
        for name, value in self.originals.items():
            setattr(media_encoder, name, value)
        self.temporary.cleanup()

    def test_claim_allows_only_one_worker_and_can_be_released(self):
        self.assertTrue(media_encoder.acquire_claim())
        self.assertFalse(media_encoder.acquire_claim())
        media_encoder.release_claim()
        self.assertTrue(media_encoder.acquire_claim())

    def test_stale_claim_is_recovered(self):
        media_encoder.CLAIM_DIR.mkdir()
        stale = datetime.now(timezone.utc) - timedelta(hours=1)
        (media_encoder.CLAIM_DIR / "owner.json").write_text(
            json.dumps({"worker": "offline-worker", "updated": stale.isoformat()}),
            encoding="utf-8",
        )
        media_encoder.CLAIM_TIMEOUT = 1
        self.assertTrue(media_encoder.acquire_claim())

    def test_fresh_preferred_worker_heartbeat_pauses_fallback(self):
        preferred = media_encoder.STATE_DIR / "status-desktop-gpu.json"
        preferred.write_text(
            json.dumps(
                {
                    "worker": "desktop-gpu",
                    "updated": datetime.now(timezone.utc).isoformat(),
                    "state": "idle",
                }
            ),
            encoding="utf-8",
        )
        media_encoder.PREFERRED_STATUS = str(preferred)
        self.assertTrue(media_encoder.preferred_worker_online())


if __name__ == "__main__":
    unittest.main()
