#!/usr/bin/env python3
import argparse
import fcntl
import json
import os
import signal
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from compatibility import (
    EXTENSIONS,
    MAX_VIDEO_BITRATE,
    PROFILE,
    duration,
    probe,
    transcode_plan,
)

ROOTS = [Path(value) for value in os.getenv("MEDIA_ROOTS", "/media/Movies:/media/TV").split(":")]
STATE_DIR = Path(os.getenv("STATE_DIR", "/media/.jellypi-encoder"))
STATUS = STATE_DIR / "status.json"
MIN_AGE = int(os.getenv("MIN_AGE_SECONDS", "900"))
SLEEP = int(os.getenv("SCAN_INTERVAL_SECONDS", "120"))
CRF = os.getenv("CRF", "21")
PRESET = os.getenv("PRESET", "veryfast")
stop = False


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_status(**changes) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        current = json.loads(STATUS.read_text(encoding="utf-8"))
    except Exception:
        current = {}
    current.update({"worker": os.uname().nodename, "updated": now()}, **changes)
    temporary = STATUS.with_suffix(".tmp")
    temporary.write_text(json.dumps(current, indent=2), encoding="utf-8")
    os.chmod(temporary, 0o664)
    os.replace(temporary, STATUS)


def parse_clock(value: str) -> float:
    try:
        hours, minutes, seconds = value.split(":")
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    except (ValueError, TypeError):
        return 0.0


def validate_samples(path: Path, total: float) -> None:
    sample_length = min(120.0, total) if total else 120.0
    starts = [0.0]
    if total > sample_length:
        starts.extend(
            [
                max(0.0, total / 2 - sample_length / 2),
                max(0.0, total - sample_length),
            ]
        )
    starts = list(dict.fromkeys(round(value, 3) for value in starts))
    labels = (
        ["beginning", "middle", "end"]
        if len(starts) == 3
        else [f"sample {index + 1}" for index in range(len(starts))]
    )
    for index, (label, start_at) in enumerate(zip(labels, starts), 1):
        write_status(
            state="validating",
            progress=100,
            validationSample=label,
            validationStep=f"{index}/{len(starts)}",
        )
        result = subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-xerror",
                "-ss",
                str(start_at),
                "-i",
                str(path),
                "-t",
                str(sample_length),
                "-map",
                "0:v:0",
                "-map",
                "0:a?",
                "-f",
                "null",
                "-",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if result.returncode:
            raise RuntimeError(
                f"{label} sample validation failed: {result.stderr[-500:].strip()}"
            )


def encode(source: Path, info: dict, job: dict) -> None:
    total = duration(info)
    temporary = source.with_name(f".{source.stem}.encoder-part{source.suffix.lower()}")
    temporary.unlink(missing_ok=True)
    command = [
        "ffmpeg",
        "-hide_banner",
        "-y",
        "-i",
        str(source),
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
    ]
    if job["add_aac"]:
        command.extend(["-map", "0:a:0"])
    command.extend(["-map", "0:s?", "-map_metadata", "0", "-map_chapters", "0"])

    if job["video_encode"]:
        command.extend(
            [
                "-c:v",
                "libx264",
                "-preset",
                PRESET,
                "-crf",
                CRF,
                "-maxrate",
                str(MAX_VIDEO_BITRATE),
                "-bufsize",
                str(MAX_VIDEO_BITRATE * 2),
                "-pix_fmt",
                "yuv420p",
                "-vf",
                "scale=w='min(1920,iw)':h='min(1080,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2",
                "-metadata",
                f"MEDIA_ENCODER_PROFILE={PROFILE}",
            ]
        )
    else:
        command.extend(["-c:v", "copy"])

    command.extend(["-c:a", "copy"])
    if job["add_aac"]:
        audio_index = job["audio_count"]
        command.extend(
            [
                f"-c:a:{audio_index}",
                "aac",
                f"-ac:a:{audio_index}",
                "2",
                f"-b:a:{audio_index}",
                "192k",
                f"-metadata:s:a:{audio_index}",
                "title=AAC Stereo Compatibility",
            ]
        )
    command.extend(
        [
            "-c:s",
            "copy",
            "-max_muxing_queue_size",
            "4096",
            "-progress",
            "pipe:1",
            "-nostats",
            str(temporary),
        ]
    )

    started = now()
    write_status(state="encoding", file=str(source), progress=0, started=started, error=None)
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    last_update = 0.0
    assert process.stdout is not None
    for line in process.stdout:
        if stop:
            process.terminate()
            process.wait(timeout=30)
            temporary.unlink(missing_ok=True)
            raise RuntimeError("stopped")
        key, _, value = line.strip().partition("=")
        if key == "out_time" and total and time.time() - last_update > 2:
            write_status(
                state="encoding",
                file=str(source),
                progress=min(99, round(parse_clock(value) * 100 / total, 1)),
                started=started,
            )
            last_update = time.time()
    if process.wait() != 0:
        temporary.unlink(missing_ok=True)
        raise RuntimeError("ffmpeg encoding failed")

    write_status(state="validating", file=str(source), progress=100, started=started)
    output_info = probe(temporary)
    output_plan = transcode_plan(output_info)
    output_duration = duration(output_info)
    if output_plan and output_plan["video_encode"]:
        raise RuntimeError("output video is not compatible")
    if job["add_aac"] and output_plan and output_plan["add_aac"]:
        raise RuntimeError("AAC stereo fallback is missing")
    if total and abs(output_duration - total) > max(2.0, total * 0.01):
        raise RuntimeError(f"duration mismatch ({total:.1f}s vs {output_duration:.1f}s)")
    validate_samples(temporary, output_duration)
    os.chmod(temporary, 0o664)
    os.replace(temporary, source)
    write_status(
        state="completed",
        file=None,
        progress=100,
        started=None,
        lastCompleted=str(source),
        lastCompletedAt=now(),
        error=None,
        validationSample=None,
        validationStep=None,
    )


def candidates() -> list[Path]:
    files: list[Path] = []
    cutoff = time.time() - MIN_AGE
    for root in ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            try:
                if (
                    path.is_file()
                    and path.suffix.lower() in EXTENSIONS
                    and not path.name.startswith(".")
                    and path.stat().st_mtime < cutoff
                ):
                    files.append(path)
            except OSError:
                pass
    return sorted(files, key=lambda path: path.stat().st_mtime)


def run_once() -> bool:
    files = candidates()
    write_status(state="scanning", file=None, progress=0, queue=len(files), error=None)
    for index, source in enumerate(files):
        try:
            info = probe(source)
            job = transcode_plan(info)
            if job is None:
                continue
            decision = "encode video" if job["video_encode"] else "copy video"
            if job["add_aac"]:
                decision += " + AAC stereo"
            write_status(queue=len(files) - index, decision=decision)
            encode(source, info, job)
            return True
        except Exception as error:
            write_status(
                state="error",
                file=str(source),
                progress=0,
                error=str(error),
                failedAt=now(),
            )
            time.sleep(5)
    write_status(state="idle", file=None, progress=0, queue=0, error=None)
    return False


def main() -> None:
    global stop
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--inspect")
    args = parser.parse_args()
    if args.inspect:
        info = probe(Path(args.inspect))
        print(json.dumps({"duration": duration(info), "plan": transcode_plan(info)}, indent=2))
        return

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = STATE_DIR / "encoder.lock"
    lock = lock_path.open("w", encoding="utf-8")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("media encoder is already running")

    def halt(*_args) -> None:
        global stop
        stop = True

    signal.signal(signal.SIGTERM, halt)
    signal.signal(signal.SIGINT, halt)
    write_status(state="starting", file=None, progress=0, error=None)
    while not stop:
        run_once()
        if args.once:
            break
        for _ in range(SLEEP):
            if stop:
                break
            time.sleep(1)
    write_status(state="stopped", file=None, progress=0)


if __name__ == "__main__":
    main()
