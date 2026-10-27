import importlib.util
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "missing_search", ROOT / "recovery" / "missing_search.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class MissingSearchTests(unittest.TestCase):
    def test_aired_rejects_future_and_accepts_past(self):
        now = datetime.now(timezone.utc)
        self.assertTrue(
            MODULE.aired({"airDateUtc": (now - timedelta(hours=1)).isoformat()}, now.timestamp())
        )
        self.assertFalse(
            MODULE.aired({"airDateUtc": (now + timedelta(hours=1)).isoformat()}, now.timestamp())
        )

    def test_cooldown_prevents_repeat_search(self):
        now = 1_000_000.0
        self.assertFalse(MODULE.due({"sonarr:1": now - 60}, "sonarr:1", now))
        self.assertTrue(
            MODULE.due(
                {"sonarr:1": now - MODULE.COOLDOWN_SECONDS - 1},
                "sonarr:1",
                now,
            )
        )
        self.assertTrue(MODULE.due({}, "sonarr:1", now))


if __name__ == "__main__":
    unittest.main()
