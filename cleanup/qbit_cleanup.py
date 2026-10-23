#!/usr/bin/env python3
import http.cookiejar
import json
import os
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from functools import lru_cache
from pathlib import PurePosixPath

QB = os.getenv("QBIT_BASE_URL", "http://gluetun:8080/api/v2/")
INTERVAL = int(os.getenv("CLEANUP_INTERVAL_SECONDS", "300"))
MIN_COMPLETE_AGE = int(os.getenv("MIN_COMPLETE_AGE_SECONDS", "600"))
VIDEO_EXTENSIONS = {".mkv", ".mp4", ".m4v", ".avi", ".mov", ".ts", ".m2ts", ".webm"}


def api_json(url: str, headers: dict | None = None):
    request = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


@lru_cache
def arr_api_key(config_path: str) -> str:
    api_key = ET.parse(config_path).getroot().findtext("ApiKey")
    if not api_key:
        raise RuntimeError(f"No API key found in {config_path}")
    return api_key


def arr_queue(base_url: str, config_path: str) -> set[str]:
    api_key = arr_api_key(config_path)
    url = (
        f"{base_url}/api/v3/queue?page=1&pageSize=1000"
        "&includeUnknownSeriesItems=true&includeUnknownMovieItems=true"
    )
    data = api_json(url, {"X-Api-Key": api_key})
    return {
        str(item.get("downloadId", "")).lower()
        for item in data.get("records", [])
        if item.get("downloadId")
    }


def timestamp(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def imported_video_names(records: list[dict], added_on: int) -> set[str]:
    imported = set()
    for record in records:
        try:
            if (
                record.get("eventType") != "downloadFolderImported"
                or timestamp(record["date"]) < added_on
            ):
                continue
            dropped_path = str((record.get("data") or {}).get("droppedPath") or "")
            if dropped_path:
                imported.add(dropped_path.replace("\\", "/").rsplit("/", 1)[-1].casefold())
        except (KeyError, TypeError, ValueError):
            continue
    return imported


def arr_imports(
    base_url: str, config_path: str, download_id: str, added_on: int
) -> set[str]:
    api_key = arr_api_key(config_path)
    query = urllib.parse.urlencode(
        {
            "page": 1,
            "pageSize": 1000,
            "sortDirection": "descending",
            "sortKey": "date",
            "downloadId": download_id,
        }
    )
    data = api_json(f"{base_url}/api/v3/history?{query}", {"X-Api-Key": api_key})
    return imported_video_names(data.get("records", []), added_on)


def torrent_video_names(files: list[dict]) -> set[str]:
    videos = set()
    for item in files:
        normalized = str(item.get("name") or "").replace("\\", "/")
        path = PurePosixPath(normalized)
        sample = "sample" in {part.casefold() for part in path.parts[:-1]}
        sample = sample or "sample" in path.stem.casefold().split(".")
        if path.suffix.casefold() in VIDEO_EXTENSIONS and not sample:
            videos.add(path.name.casefold())
    return videos


def torrent_files(opener, torrent_hash: str) -> list[dict]:
    query = urllib.parse.urlencode({"hash": torrent_hash})
    with opener.open(QB + f"torrents/files?{query}", timeout=30) as response:
        return json.load(response)


def post(opener, path: str, fields: dict) -> bytes:
    data = urllib.parse.urlencode(fields).encode()
    request = urllib.request.Request(QB + path, data=data)
    with opener.open(request, timeout=30) as response:
        return response.read()


def cleanup_once() -> None:
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    post(
        opener,
        "auth/login",
        {
            "username": os.environ["QBIT_USER"],
            "password": os.environ["QBIT_PASSWORD"],
        },
    )
    radarr_url = os.getenv("RADARR_URL", "http://radarr:7878")
    radarr_config = os.getenv("RADARR_CONFIG", "/arr-config/radarr/config.xml")
    sonarr_url = os.getenv("SONARR_URL", "http://sonarr:8989")
    sonarr_config = os.getenv("SONARR_CONFIG", "/arr-config/sonarr/config.xml")
    active = arr_queue(radarr_url, radarr_config) | arr_queue(sonarr_url, sonarr_config)
    with opener.open(QB + "torrents/info", timeout=30) as response:
        torrents = json.load(response)

    current_time = int(time.time())
    removed = 0
    for torrent in torrents:
        torrent_hash = torrent.get("hash", "").lower()
        category = torrent.get("category", "").lower()
        completed = int(torrent.get("completion_on") or 0)
        added = int(torrent.get("added_on") or 0)
        if category not in {"radarr", "sonarr"} or float(torrent.get("progress", 0)) < 1:
            continue
        if (
            not completed
            or not added
            or current_time - completed < MIN_COMPLETE_AGE
            or torrent_hash in active
        ):
            continue
        required = torrent_video_names(torrent_files(opener, torrent_hash))
        imported = arr_imports(radarr_url, radarr_config, torrent_hash, added)
        imported |= arr_imports(sonarr_url, sonarr_config, torrent_hash, added)
        if not required or not required.issubset(imported):
            print(
                f"cleanup skipped: {torrent.get('name', torrent_hash)} "
                f"confirmed_imports={len(imported)}/{len(required)}",
                flush=True,
            )
            continue
        post(
            opener,
            "torrents/delete",
            {"hashes": torrent_hash, "deleteFiles": "true"},
        )
        print("removed:", torrent.get("name", torrent_hash), flush=True)
        removed += 1
    print(
        f"cleanup complete: removed={removed} active_queue_items={len(active)}",
        flush=True,
    )


def main() -> None:
    while True:
        try:
            cleanup_once()
        except Exception as error:
            print(f"cleanup error: {error}", flush=True)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
