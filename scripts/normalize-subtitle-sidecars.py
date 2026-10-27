#!/usr/bin/env python3
"""Keep English subtitle sidecars named exactly like their media file."""

import argparse
import json
import os
import time
from pathlib import Path


MEDIA_EXTENSIONS = {".mkv", ".mp4"}
ENGLISH_SUFFIXES = (
    ".en.srt",
    ".eng.srt",
    ".en.default.srt",
    ".eng.default.srt",
    ".en.hi.srt",
    ".eng.hi.srt",
)


def english_rank(path: Path) -> int:
    lower = path.name.lower()
    for rank, suffix in enumerate(ENGLISH_SUFFIXES):
        if lower.endswith(suffix):
            return rank
    if lower.endswith(".srt.sidecar-backup"):
        return len(ENGLISH_SUFFIXES)
    return 100


def english_sidecars(directory: Path) -> list[Path]:
    return sorted(
        (
            path
            for path in directory.iterdir()
            if path.is_file() and english_rank(path) < 100
        ),
        key=lambda path: (english_rank(path), path.name.lower()),
    )


def normalize_directory(directory: Path, dry_run: bool = False) -> list[dict]:
    media = sorted(
        (
            path
            for path in directory.iterdir()
            if path.is_file() and path.suffix.lower() in MEDIA_EXTENSIONS
        ),
        key=lambda path: path.name.lower(),
    )
    sidecars = english_sidecars(directory)
    changes = []

    for video in media:
        exact = []
        for sidecar in sidecars:
            lower = sidecar.name.lower()
            for suffix in ENGLISH_SUFFIXES:
                if lower == (video.stem + suffix).lower():
                    exact.append(sidecar)
                    break

        # Every recognized exact-basename variant is already valid. In
        # particular, keep Bazarr's `.en.hi.srt` files and anime sidecars
        # unchanged instead of silently changing their hearing-impaired label.
        if exact:
            continue

        target = video.with_name(video.stem + ".en.srt")
        source = None
        if len(media) == 1:
            # Radarr or the compatibility worker may rename the only movie in
            # its folder after Bazarr has already written a correctly matched
            # sidecar. A single-media directory is safe to repair; season
            # folders with several episodes are deliberately never guessed.
            source = sidecars[0] if sidecars else None

        if source is None:
            continue

        changes.append({"source": str(source), "target": str(target)})
        if not dry_run:
            os.replace(source, target)
            sidecars.remove(source)
            sidecars.append(target)

    return changes


def scan(root: Path, dry_run: bool = False) -> list[dict]:
    changes = []
    for directory, _, files in os.walk(root):
        if not any(Path(name).suffix.lower() in MEDIA_EXTENSIONS for name in files):
            continue
        changes.extend(normalize_directory(Path(directory), dry_run=dry_run))
    return changes


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--media-root",
        type=Path,
        default=Path(os.environ.get("MEDIA_ROOT", "/srv/media")),
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--status", type=Path)
    args = parser.parse_args()

    roots = [args.media_root / "Movies", args.media_root / "TV"]
    changes = []
    failures = []
    for root in roots:
        if not root.exists():
            continue
        try:
            changes.extend(scan(root, dry_run=args.dry_run))
        except Exception as error:
            failures.append({"root": str(root), "error": str(error)})

    result = {
        "updated": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "dry_run": args.dry_run,
        "renamed": len(changes),
        "changes": changes,
        "failures": failures,
    }
    if args.status:
        args.status.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.status.with_suffix(".tmp")
        temporary.write_text(json.dumps(result, indent=2), encoding="utf-8")
        os.replace(temporary, args.status)
    print(json.dumps(result, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
