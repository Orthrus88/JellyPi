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
METADATA_TIMEOUT = int(os.getenv("QBIT_METADATA_TIMEOUT_SECONDS", "1800"))
ZERO_PROGRESS_TIMEOUT = int(os.getenv("QBIT_ZERO_PROGRESS_TIMEOUT_SECONDS", "1200"))
PARTIAL_STALL_TIMEOUT = int(os.getenv("QBIT_PARTIAL_STALL_TIMEOUT_SECONDS", "1800"))
MIN_USEFUL_SPEED = int(os.getenv("QBIT_MIN_USEFUL_SPEED_BPS", str(16 * 1024)))
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


def arr_request(
    base_url: str,
    config_path: str,
    path: str,
    method: str = "GET",
    body: dict | None = None,
):
    headers = {"X-Api-Key": arr_api_key(config_path)}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        f"{base_url}/api/v3/{path}", headers=headers, data=data, method=method
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        raw = response.read()
        return json.loads(raw) if raw else None


def arr_queue(base_url: str, config_path: str) -> list[dict]:
    api_key = arr_api_key(config_path)
    url = (
        f"{base_url}/api/v3/queue?page=1&pageSize=1000"
        "&includeUnknownSeriesItems=true&includeUnknownMovieItems=true"
    )
    data = api_json(url, {"X-Api-Key": api_key})
    return data.get("records", [])


def active_download_ids(records: list[dict]) -> set[str]:
    return {
        str(item.get("downloadId", "")).lower()
        for item in records
        if item.get("downloadId")
    }


def target_keys(app_name: str, record: dict) -> set[tuple[str, int]]:
    if app_name == "radarr":
        movie_id = record.get("movieId")
        return {("movie", movie_id)} if movie_id else set()
    episode_ids = set(record.get("episodeIds") or [])
    if record.get("episodeId"):
        episode_ids.add(record["episodeId"])
    return {("episode", episode_id) for episode_id in episode_ids}


def usenet_target_keys(app_name: str, records: list[dict]) -> set[tuple[str, int]]:
    targets = set()
    for record in records:
        if (
            str(record.get("protocol") or "").lower() == "usenet"
            and record.get("downloadId")
            and record.get("downloadClient")
        ):
            targets.update(target_keys(app_name, record))
    return targets


def stale_reason(torrent: dict, now: int) -> str | None:
    progress = float(torrent.get("progress") or 0)
    if progress >= 1:
        return None
    state = str(torrent.get("state") or "")
    added = int(torrent.get("added_on") or now)
    last_activity = int(torrent.get("last_activity") or added)
    age = now - added
    idle = now - max(added, last_activity)
    speed = int(torrent.get("dlspeed") or 0)
    downloaded = int(torrent.get("downloaded") or 0)
    average_speed = downloaded // max(age, 1)
    connected_seeds = int(torrent.get("num_seeds") or 0)
    known_complete = int(torrent.get("num_complete") or 0)
    active_states = {"downloading", "stalledDL", "forcedDL"}
    if state in {"metaDL", "forcedMetaDL"} and age >= METADATA_TIMEOUT:
        return f"metadata unresolved for {age // 60} minutes"
    if (
        progress == 0
        and speed == 0
        and connected_seeds == 0
        and known_complete == 0
        and idle >= ZERO_PROGRESS_TIMEOUT
        and state in active_states
    ):
        return f"zero progress and no complete copies for {idle // 60} minutes"
    if (
        0 < progress < 1
        and speed < MIN_USEFUL_SPEED
        and average_speed < MIN_USEFUL_SPEED
        and connected_seeds == 0
        and age >= PARTIAL_STALL_TIMEOUT
        and state in active_states
    ):
        return f"partial download averaged under {MIN_USEFUL_SPEED} B/s"
    return None


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


def delete_queue_item(app: dict, records: list[dict], blocklist: bool) -> None:
    query = urllib.parse.urlencode(
        {
            "removeFromClient": "true",
            "blocklist": str(blocklist).lower(),
            "skipRedownload": "true",
            "changeCategory": "false",
        }
    )
    arr_request(
        app["url"],
        app["config"],
        f"queue/{records[0]['id']}?{query}",
        method="DELETE",
    )


def recover_download(app: dict, records: list[dict]) -> None:
    delete_queue_item(app, records, blocklist=True)
    if app["name"] == "sonarr":
        episode_ids = sorted(
            {record.get("episodeId") for record in records if record.get("episodeId")}
        )
        if episode_ids:
            arr_request(
                app["url"],
                app["config"],
                "command",
                method="POST",
                body={"name": "EpisodeSearch", "episodeIds": episode_ids},
            )
    else:
        movie_ids = sorted(
            {record.get("movieId") for record in records if record.get("movieId")}
        )
        if movie_ids:
            arr_request(
                app["url"],
                app["config"],
                "command",
                method="POST",
                body={"name": "MoviesSearch", "movieIds": movie_ids},
            )


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
    apps = [
        {"name": "radarr", "url": radarr_url, "config": radarr_config},
        {"name": "sonarr", "url": sonarr_url, "config": sonarr_config},
    ]
    owner = {}
    active = set()
    usenet_targets = {}
    for app in apps:
        records = arr_queue(app["url"], app["config"])
        active |= active_download_ids(records)
        for record in records:
            download_id = str(record.get("downloadId") or "").lower()
            if download_id:
                if download_id not in owner:
                    owner[download_id] = (app, [])
                owner[download_id][1].append(record)
        usenet_targets[app["name"]] = usenet_target_keys(app["name"], records)
    with opener.open(QB + "torrents/info", timeout=30) as response:
        torrents = json.load(response)

    current_time = int(time.time())
    removed = 0
    recovered = 0
    for torrent in torrents:
        torrent_hash = torrent.get("hash", "").lower()
        category = torrent.get("category", "").lower()
        completed = int(torrent.get("completion_on") or 0)
        added = int(torrent.get("added_on") or 0)
        progress = float(torrent.get("progress", 0))
        if category not in {"radarr", "sonarr"}:
            continue

        assigned = owner.get(torrent_hash)
        if assigned:
            app, records = assigned
            torrent_targets = set()
            for record in records:
                torrent_targets.update(target_keys(app["name"], record))
            if torrent_targets & usenet_targets.get(app["name"], set()):
                delete_queue_item(app, records, blocklist=False)
                print(
                    "removed duplicate torrent while Usenet is active:",
                    torrent.get("name", torrent_hash),
                    flush=True,
                )
                removed += 1
                continue

        if progress < 1:
            reason = stale_reason(torrent, current_time)
            if reason and assigned:
                app, records = assigned
                recover_download(app, records)
                print(
                    f"recovered: {torrent.get('name', torrent_hash)} ({reason})",
                    flush=True,
                )
                recovered += 1
            continue
        if (
            not completed
            or not added
            or current_time - completed < MIN_COMPLETE_AGE
            or torrent_hash in active
        ):
            continue
        required = torrent_video_names(torrent_files(opener, torrent_hash))
        imported = set()
        for app in apps:
            imported |= arr_imports(
                app["url"], app["config"], torrent_hash, added
            )
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
        f"cleanup complete: removed={removed} recovered={recovered} "
        f"active_queue_items={len(active)}",
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
