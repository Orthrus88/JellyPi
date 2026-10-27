import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class StackOperationsTests(unittest.TestCase):
    def test_bazarr_is_part_of_the_default_stack(self):
        compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
        self.assertIn("  bazarr:", compose)
        self.assertIn("- \"6767:6767\"", compose)
        self.assertIn("/bazarr:/config", compose)

    def test_nightly_updater_validates_before_pulling(self):
        script = (ROOT / "scripts" / "media-stack-stable-update.sh").read_text(
            encoding="utf-8"
        )
        self.assertLess(script.index("docker compose config"), script.index("docker compose pull"))
        self.assertIn("docker compose up -d --remove-orphans", script)

    def test_windows_worker_tone_maps_hdr_and_cleans_failed_output(self):
        path = ROOT / "windows" / "desktop_encoder.py"
        spec = importlib.util.spec_from_file_location("desktop_encoder", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        info = {
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "hevc",
                    "pix_fmt": "yuv420p10le",
                    "width": 1920,
                    "height": 1080,
                    "color_transfer": "smpte2084",
                },
                {"codec_type": "audio", "codec_name": "eac3", "channels": 6},
            ],
            "format": {"tags": {}},
        }
        plan = module.plan(info)
        self.assertTrue(plan["video_encode"])
        self.assertTrue(plan["tone_map"])
        self.assertTrue(plan["add_aac"])
        source = path.read_text(encoding="utf-8")
        self.assertIn("zscale=transfer=linear", source)
        self.assertIn("temp.unlink(missing_ok=True)", source)


if __name__ == "__main__":
    unittest.main()
