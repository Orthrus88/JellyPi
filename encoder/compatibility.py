import json
import os
import subprocess
from pathlib import Path

PROFILE = "jellypi-remote-h264-v1"
MAX_VIDEO_BITRATE = int(os.getenv("MAX_VIDEO_BITRATE", "6000000"))
EXTENSIONS = {".mkv", ".mp4", ".m4v", ".avi", ".mov", ".ts", ".m2ts", ".webm"}


def probe(path: Path) -> dict:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "ffprobe failed")
    return json.loads(result.stdout)


def duration(info: dict) -> float:
    try:
        return float(info.get("format", {}).get("duration") or 0)
    except (TypeError, ValueError):
        return 0.0


def transcode_plan(info: dict) -> dict | None:
    streams = info.get("streams", [])
    videos = [stream for stream in streams if stream.get("codec_type") == "video"]
    audios = [stream for stream in streams if stream.get("codec_type") == "audio"]
    if not videos:
        return None

    video = videos[0]
    tags = info.get("format", {}).get("tags") or {}
    encoded_profile = str(
        tags.get("MEDIA_ENCODER_PROFILE")
        or tags.get("media_encoder_profile")
        or ""
    )
    try:
        bitrate = int(
            video.get("bit_rate") or info.get("format", {}).get("bit_rate") or 0
        )
    except (TypeError, ValueError):
        bitrate = 0

    video_ok = (
        video.get("codec_name") == "h264"
        and video.get("pix_fmt") in {"yuv420p", "yuvj420p"}
        and int(video.get("width") or 0) <= 1920
        and int(video.get("height") or 0) <= 1080
        and (video.get("color_transfer") or "").lower()
        not in {"smpte2084", "arib-std-b67"}
        and (encoded_profile == PROFILE or not bitrate or bitrate <= MAX_VIDEO_BITRATE)
    )
    stereo_aac = any(
        audio.get("codec_name") == "aac" and int(audio.get("channels") or 0) <= 2
        for audio in audios
    )
    if video_ok and stereo_aac:
        return None
    return {
        "video_encode": not video_ok,
        "add_aac": bool(audios) and not stereo_aac,
        "audio_count": len(audios),
    }


def describe_plan(plan: dict | None) -> str:
    if plan is None:
        return "Compatible"
    actions = ["Convert video" if plan["video_encode"] else "Copy video"]
    if plan["add_aac"]:
        actions.append("Add AAC stereo")
    return " + ".join(actions)
