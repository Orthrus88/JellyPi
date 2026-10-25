#!/usr/bin/env python3
import argparse
import json
import os
import signal
import shutil
import socket
import subprocess
import time
from collections import deque
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
STATUS = STATE_DIR / os.getenv("STATUS_FILE", "status.json")
MIN_AGE = int(os.getenv("MIN_AGE_SECONDS", "900"))
SLEEP = int(os.getenv("SCAN_INTERVAL_SECONDS", "120"))
CRF = os.getenv("CRF", "21")
PRESET = os.getenv("PRESET", "veryfast")
VIDEO_ENCODER = os.getenv("VIDEO_ENCODER", "libx264")
NVENC_PRESET = os.getenv("NVENC_PRESET", "p4")
NVENC_CQ = os.getenv("NVENC_CQ", "23")
WORKER_NAME = os.getenv("WORKER_NAME", socket.gethostname())
PREFERRED_STATUS = os.getenv("PREFERRED_WORKER_STATUS", "")
PREFERRED_TIMEOUT = int(os.getenv("PREFERRED_WORKER_TIMEOUT_SECONDS", "300"))
CLAIM_DIR = STATE_DIR / "encoder.claim"
CLAIM_TIMEOUT = int(os.getenv("ENCODER_CLAIM_TIMEOUT_SECONDS", "600"))
stop = False


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_status(**changes) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        current = json.loads(STATUS.read_text(encoding="utf-8"))
    except Exception:
        current = {}
    current.update({"worker": WORKER_NAME, "updated": now()}, **changes)
    temporary = STATUS.with_suffix(".tmp")
    temporary.write_text(json.dumps(current, indent=2), encoding="utf-8")
    os.chmod(temporary, 0o664)
    os.replace(temporary, STATUS)
    refresh_claim()


def parse_clock(value: str) -> float:
    try:
        hours, minutes, seconds = value.split(":")
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    except (ValueError, TypeError):
        return 0.0


def preferred_worker_online() -> bool:
    if not PREFERRED_STATUS:
        return False
    try:
        status = json.loads(Path(PREFERRED_STATUS).read_text(encoding="utf-8"))
        updated = datetime.fromisoformat(status["updated"].replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - updated).total_seconds()
        return age <= PREFERRED_TIMEOUT and status.get("state") not in {"error", "stopped"}
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return False


def refresh_claim() -> None:
    owner_path = CLAIM_DIR / "owner.json"
    try:
        owner = json.loads(owner_path.read_text(encoding="utf-8"))
        if owner.get("worker") != WORKER_NAME:
            return
        owner.update({"updated": now(), "statusFile": STATUS.name})
        temporary = owner_path.with_name(f"owner-{os.getpid()}.tmp")
        temporary.write_text(json.dumps(owner, indent=2), encoding="utf-8")
        os.replace(temporary, owner_path)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        pass


def acquire_claim() -> bool:
    try:
        CLAIM_DIR.mkdir()
    except FileExistsError:
        try:
            owner = json.loads((CLAIM_DIR / "owner.json").read_text(encoding="utf-8"))
            updated = datetime.fromisoformat(owner["updated"].replace("Z", "+00:00"))
            age = (datetime.now(timezone.utc) - updated).total_seconds()
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            try:
                age = time.time() - CLAIM_DIR.stat().st_mtime
            except OSError:
                return False
        if age <= CLAIM_TIMEOUT:
            return False
        stale = STATE_DIR / f"encoder.claim.stale-{time.time_ns()}"
        try:
            CLAIM_DIR.rename(stale)
            shutil.rmtree(stale, ignore_errors=True)
        except OSError:
            return False
        return acquire_claim()
    except OSError:
        return False
    try:
        (CLAIM_DIR / "owner.json").write_text(
            json.dumps(
                {"worker": WORKER_NAME, "updated": now(), "statusFile": STATUS.name},
                indent=2,
            ),
            encoding="utf-8",
        )
    except OSError:
        shutil.rmtree(CLAIM_DIR, ignore_errors=True)
        return False
    return True


def release_claim() -> None:
    try:
        owner = json.loads((CLAIM_DIR / "owner.json").read_text(encoding="utf-8"))
        if owner.get("worker") == WORKER_NAME:
            shutil.rmtree(CLAIM_DIR)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        pass


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
    target = source if source.suffix.lower() == ".mkv" else source.with_suffix(".mkv")
    if target != source and target.exists():
        raise RuntimeError(f"output path already exists: {target}")
    temporary = source.with_name(f".{source.stem}.encoder-part.mkv")
    temporary.unlink(missing_ok=True)
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
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
        if VIDEO_ENCODER == "h264_nvenc":
            command.extend(
                [
                    "-c:v",
                    "h264_nvenc",
                    "-preset",
                    NVENC_PRESET,
                    "-rc",
                    "vbr",
                    "-cq",
                    NVENC_CQ,
                    "-b:v",
                    "0",
                ]
            )
        else:
            command.extend(["-c:v", "libx264", "-preset", PRESET, "-crf", CRF])
        scale = "scale=w='min(1920,iw)':h='min(1080,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2"
        filters = [scale]
        if job.get("tone_map"):
            filters = [
                "zscale=transfer=linear:npl=100",
                "format=gbrpf32le",
                "tonemap=tonemap=hable:desat=0",
                "zscale=primaries=bt709:transfer=bt709:matrix=bt709:range=tv",
                "format=yuv420p",
                scale,
            ]
        command.extend(
            [
                "-maxrate",
                str(MAX_VIDEO_BITRATE),
                "-bufsize",
                str(MAX_VIDEO_BITRATE * 2),
                "-pix_fmt",
                "yuv420p",
                "-vf",
                ",".join(filters),
                "-metadata",
                f"MEDIA_ENCODER_PROFILE={PROFILE}",
            ]
        )
        if job.get("tone_map"):
            command.extend(
                [
                    "-color_primaries",
                    "bt709",
                    "-color_trc",
                    "bt709",
                    "-colorspace",
                    "bt709",
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
        stderr=subprocess.STDOUT,
        text=True,
    )
    last_update = 0.0
    diagnostic_lines = deque(maxlen=50)
    assert process.stdout is not None
    for line in process.stdout:
        diagnostic_lines.append(line.rstrip())
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
        detail = "\n".join(diagnostic_lines)[-1500:].strip()
        raise RuntimeError(f"ffmpeg encoding failed: {detail or 'no details reported'}")

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
    os.replace(temporary, target)
    if target != source:
        source.unlink()
    write_status(
        state="completed",
        file=None,
        progress=100,
        started=None,
        lastCompleted=str(target),
        lastCompletedAt=now(),
        error=None,
        validationSample=None,
        validationStep=None,
    )


def candidates() -> list[Path]:
    files: list[tuple[float, Path]] = []
    cutoff = time.time() - MIN_AGE
    for root in ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            try:
                stat = path.stat()
                if (
                    path.is_file()
                    and path.suffix.lower() in EXTENSIONS
                    and not path.name.startswith(".")
                    and stat.st_mtime < cutoff
                ):
                    files.append((stat.st_mtime, path))
            except OSError:
                pass
    # Do not stat files again while sorting. Another worker may replace a file
    # after discovery, and that race must not terminate this worker.
    return [path for _, path in sorted(files, key=lambda item: item[0])]


def run_once() -> str:
    files = candidates()
    last_error = None
    failed_source = None
    write_status(state="scanning", file=None, progress=0, queue=len(files), error=None)
    for index, source in enumerate(files):
        try:
            refresh_claim()
            info = probe(source)
            job = transcode_plan(info)
            if job is None:
                continue
            decision = "encode video" if job["video_encode"] else "copy video"
            if job["add_aac"]:
                decision += " + AAC stereo"
            write_status(queue=len(files) - index, decision=decision)
            encode(source, info, job)
            return "completed"
        except Exception as error:
            last_error = str(error)
            failed_source = source
            write_status(
                state="error",
                file=str(source),
                progress=0,
                error=str(error),
                failedAt=now(),
            )
            time.sleep(5)
    if last_error:
        write_status(
            state="error",
            file=str(failed_source),
            progress=0,
            queue=0,
            error=last_error,
        )
        return "error"
    write_status(
        state="idle",
        file=None,
        progress=0,
        queue=0,
        error=None,
        decision=None,
    )
    return "idle"


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

    if VIDEO_ENCODER not in {"libx264", "h264_nvenc"}:
        raise SystemExit(f"unsupported VIDEO_ENCODER: {VIDEO_ENCODER}")

    STATE_DIR.mkdir(parents=True, exist_ok=True)

    def halt(*_args) -> None:
        global stop
        stop = True

    signal.signal(signal.SIGTERM, halt)
    signal.signal(signal.SIGINT, halt)
    write_status(state="starting", file=None, progress=0, error=None)
    result = "idle"
    while not stop:
        if preferred_worker_online():
            write_status(
                state="standby",
                file=None,
                progress=0,
                error=None,
                decision="Preferred GPU worker is online",
            )
        else:
            if not acquire_claim():
                write_status(
                    state="standby",
                    file=None,
                    progress=0,
                    error=None,
                    decision="Another worker is encoding",
                )
            else:
                try:
                    result = run_once()
                finally:
                    release_claim()
        if args.once:
            break
        for _ in range(SLEEP):
            if stop:
                break
            time.sleep(1)
    if args.once:
        raise SystemExit(1 if result == "error" else 0)
    write_status(state="stopped", file=None, progress=0)


if __name__ == "__main__":
    main()
