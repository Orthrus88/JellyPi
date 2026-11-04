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

    def test_windows_watchdog_is_windowless_and_heartbeat_aware(self):
        project = (
            ROOT / "windows" / "EncoderWatchdog" / "EncoderWatchdog.csproj"
        ).read_text(encoding="utf-8")
        program = (
            ROOT / "windows" / "EncoderWatchdog" / "Program.cs"
        ).read_text(encoding="utf-8")
        installer = (
            ROOT / "windows" / "install_encoder_watchdog.ps1"
        ).read_text(encoding="utf-8")
        worker = (ROOT / "windows" / "desktop_encoder.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("<OutputType>WinExe</OutputType>", project)
        self.assertIn("MaxHeartbeatAgeSeconds", program)
        self.assertIn("worker.Kill(entireProcessTree: true)", program)
        self.assertIn("New-TimeSpan -Minutes 1", installer)
        self.assertIn("$programRoot 'desktop-encoder.pid.json'", installer)
        self.assertIn("desktop-encoder.pid.json", worker)


if __name__ == "__main__":
    unittest.main()
