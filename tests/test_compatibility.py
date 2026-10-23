import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "encoder"))

from compatibility import PROFILE, transcode_plan


def media_info(video_overrides=None, audio=None, format_overrides=None):
    video = {
        "codec_type": "video",
        "codec_name": "h264",
        "pix_fmt": "yuv420p",
        "width": 1920,
        "height": 1080,
        "bit_rate": "5000000",
        "color_transfer": "bt709",
    }
    video.update(video_overrides or {})
    return {
        "streams": [video]
        + (
            audio
            or [{"codec_type": "audio", "codec_name": "aac", "channels": 2}]
        ),
        "format": {"tags": {}, **(format_overrides or {})},
    }


class CompatibilityTests(unittest.TestCase):
    def test_compatible_file_is_skipped(self):
        self.assertIsNone(transcode_plan(media_info()))

    def test_hevc_video_is_converted(self):
        plan = transcode_plan(media_info({"codec_name": "hevc"}))
        self.assertTrue(plan["video_encode"])

    def test_aac_stereo_is_added_without_reencoding_compatible_video(self):
        plan = transcode_plan(
            media_info(audio=[{"codec_type": "audio", "codec_name": "dts", "channels": 6}])
        )
        self.assertFalse(plan["video_encode"])
        self.assertTrue(plan["add_aac"])

    def test_high_bitrate_video_is_converted(self):
        plan = transcode_plan(media_info({"bit_rate": "12000000"}))
        self.assertTrue(plan["video_encode"])

    def test_profile_tag_prevents_reencoding_for_container_bitrate_variance(self):
        info = media_info(
            {"bit_rate": None},
            format_overrides={
                "bit_rate": "12000000",
                "tags": {"MEDIA_ENCODER_PROFILE": PROFILE},
            },
        )
        self.assertIsNone(transcode_plan(info))


if __name__ == "__main__":
    unittest.main()
