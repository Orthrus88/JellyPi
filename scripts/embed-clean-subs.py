#!/usr/bin/env python3
"""Embed validated English SRT sidecars for direct-play clients such as EVO."""

import fcntl
import json
import os
import sqlite3
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


MEDIA_ROOT = Path(os.environ.get("MEDIA_ROOT", "/srv/media"))
ROOTS = [MEDIA_ROOT / "Movies", MEDIA_ROOT / "TV"]
STATE = Path(os.environ.get("SUBTITLE_EMBED_STATE", "/var/lib/jellypi/subtitle-embed-state.json"))
LOCK = Path("/run/jellypi-subtitle-embed.lock")
JELLYFIN_DB = Path(
    os.environ.get("JELLYFIN_DB", "/srv/jellypi/config/jellyfin/data/jellyfin.db")
)
JELLYFIN_URL = os.environ.get("JELLYFIN_URL", "http://127.0.0.1:8096").rstrip("/")
SUFFIXES = (".en.default.srt", ".eng.default.srt", ".en.srt", ".eng.srt")


def save_state(**values):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    current = {}
    try:
        current = json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    current.update(values)
    current["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    temporary = STATE.with_suffix(".tmp")
    temporary.write_text(json.dumps(current, indent=2), encoding="utf-8")
    os.replace(temporary, STATE)


def jellyfin_token():
    try:
        database = sqlite3.connect(JELLYFIN_DB)
        row = database.execute(
            "select AccessToken from ApiKeys order by Id desc limit 1"
        ).fetchone()
        return row[0] if row else None
    except (OSError, sqlite3.Error):
        return None


TOKEN = jellyfin_token()


def api(path, method="GET"):
    if not TOKEN:
        return None
    request = urllib.request.Request(
        JELLYFIN_URL + path,
        method=method,
        headers={"Authorization": f'MediaBrowser Token="{TOKEN}"'},
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        if response.status == 204:
            return {}
        return json.load(response)


def playback_active():
    try:
        sessions = api("/Sessions") or []
        return any(
            session.get("NowPlayingItem")
            and not session.get("PlayState", {}).get("IsPaused", False)
            for session in sessions
        )
    except Exception:
        return False


def probe(path):
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=index,codec_type,codec_name:stream_tags=language,title",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def candidates():
    selected = {}
    priority = {suffix: rank for rank, suffix in enumerate(SUFFIXES)}
    for root in ROOTS:
        if not root.exists():
            continue
        for sidecar in root.rglob("*.srt"):
            lower = sidecar.name.lower()
            suffix = next((value for value in SUFFIXES if lower.endswith(value)), None)
            if not suffix:
                continue
            base = sidecar.name[: -len(suffix)]
            media = next(
                (
                    sidecar.with_name(base + extension)
                    for extension in (".mkv", ".mp4")
                    if sidecar.with_name(base + extension).exists()
                ),
                None,
            )
            if not media:
                continue
            old = selected.get(media)
            if old is None or priority[suffix] < priority[old[1]]:
                selected[media] = (sidecar, suffix)
    return sorted((media, value[0]) for media, value in selected.items())


def already_clean(info, extension):
    subtitles = [
        stream
        for stream in info.get("streams", [])
        if stream.get("codec_type") == "subtitle"
    ]
    expected = "subrip" if extension == ".mkv" else "mov_text"
    if len(subtitles) != 1 or subtitles[0].get("codec_name") != expected:
        return False
    tags = subtitles[0].get("tags") or {}
    return tags.get("language") == "eng" and tags.get("title") == "English Dialogue"


def playback_stream_signature(info):
    """Describe video and audio streams a subtitle remux must preserve."""
    return sorted(
        (stream.get("codec_type"), stream.get("codec_name"))
        for stream in info.get("streams", [])
        if stream.get("codec_type") in {"video", "audio"}
    )


def remux(media, sidecar):
    before = probe(media)
    if already_clean(before, media.suffix.lower()):
        return "skipped"

    original = media.stat()
    temporary = media.with_name(
        "." + media.name + ".subtitle-embed.tmp" + media.suffix
    )
    temporary.unlink(missing_ok=True)
    subtitle_codec = "subrip" if media.suffix.lower() == ".mkv" else "mov_text"
    command = [
        "ffmpeg",
        "-nostdin",
        "-y",
        "-v",
        "error",
        "-i",
        str(media),
        "-i",
        str(sidecar),
        "-map",
        "0",
        "-map",
        "-0:s",
        "-map",
        "1:0",
        "-map_metadata",
        "0",
        "-map_chapters",
        "0",
        "-c",
        "copy",
        "-c:s",
        subtitle_codec,
        "-metadata:s:s:0",
        "language=eng",
        "-metadata:s:s:0",
        "title=English Dialogue",
        "-disposition:s:0",
        "default",
        str(temporary),
    ]

    try:
        subprocess.run(command, check=True)
        after = probe(temporary)
        subtitles = [
            stream
            for stream in after.get("streams", [])
            if stream.get("codec_type") == "subtitle"
        ]
        if len(subtitles) != 1 or subtitles[0].get("codec_name") != subtitle_codec:
            raise RuntimeError("output subtitle validation failed")

        if playback_stream_signature(before) != playback_stream_signature(after):
            raise RuntimeError("video/audio stream validation failed")

        before_duration = float(before.get("format", {}).get("duration") or 0)
        after_duration = float(after.get("format", {}).get("duration") or 0)
        if (
            before_duration
            and after_duration
            # Removing embedded subtitles can legitimately change the Matroska
            # container duration by a few seconds when the old subtitle track
            # extends beyond the final video frame. Stream preservation above
            # catches an actual dropped video or audio track.
            and abs(before_duration - after_duration) > 5.0
        ):
            raise RuntimeError(
                f"duration changed from {before_duration} to {after_duration}"
            )

        os.chown(temporary, original.st_uid, original.st_gid)
        os.chmod(temporary, original.st_mode)
        os.utime(temporary, ns=(original.st_atime_ns, original.st_mtime_ns))
        os.replace(temporary, media)
        return "embedded"
    finally:
        temporary.unlink(missing_ok=True)


def main():
    descriptor = os.open(LOCK, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(descriptor)
        print("subtitle embed job is already running", file=sys.stderr)
        return 0
    os.ftruncate(descriptor, 0)
    os.write(descriptor, str(os.getpid()).encode())
    try:
        work = candidates()
        done = skipped = failed = 0
        save_state(
            status="starting",
            total=len(work),
            done=0,
            skipped=0,
            failed=0,
            current=None,
        )
        for number, (media, sidecar) in enumerate(work, 1):
            while playback_active():
                save_state(
                    status="paused-for-playback",
                    total=len(work),
                    done=done,
                    skipped=skipped,
                    failed=failed,
                    current=str(media),
                    position=number,
                )
                time.sleep(30)
            save_state(
                status="remuxing",
                total=len(work),
                done=done,
                skipped=skipped,
                failed=failed,
                current=str(media),
                position=number,
            )
            try:
                result = remux(media, sidecar)
                if result == "skipped":
                    skipped += 1
                else:
                    done += 1
                print(f"{number}/{len(work)} {result}: {media}", flush=True)
            except Exception as error:
                failed += 1
                print(
                    f"{number}/{len(work)} FAILED: {media}: {error}",
                    file=sys.stderr,
                    flush=True,
                )

        try:
            api("/Library/Refresh", method="POST")
        except Exception as error:
            print(f"Jellyfin refresh failed: {error}", file=sys.stderr)
        save_state(
            status="complete",
            total=len(work),
            done=done,
            skipped=skipped,
            failed=failed,
            current=None,
            position=len(work),
        )
        return 1 if failed else 0
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)
        LOCK.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
