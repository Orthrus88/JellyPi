#!/usr/bin/env python3
import http.cookiejar
import json
import os
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

QB = os.getenv("QBIT_BASE_URL", "http://gluetun:8080/api/v2/")
INTERVAL = int(os.getenv("CLEANUP_INTERVAL_SECONDS", "300"))
MIN_COMPLETE_AGE = int(os.getenv("MIN_COMPLETE_AGE_SECONDS", "600"))


def api_json(url: str, headers: dict | None = None) -> dict:
    request = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def arr_queue(base_url: str, config_path: str) -> set[str]:
    api_key = ET.parse(config_path).getroot().findtext("ApiKey")
    if not api_key:
        raise RuntimeError(f"No API key found in {config_path}")
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
    active = arr_queue(
        os.getenv("RADARR_URL", "http://radarr:7878"),
        os.getenv("RADARR_CONFIG", "/arr-config/radarr/config.xml"),
    ) | arr_queue(
        os.getenv("SONARR_URL", "http://sonarr:8989"),
        os.getenv("SONARR_CONFIG", "/arr-config/sonarr/config.xml"),
    )
    with opener.open(QB + "torrents/info", timeout=30) as response:
        torrents = json.load(response)

    current_time = int(time.time())
    removed = 0
    for torrent in torrents:
        torrent_hash = torrent.get("hash", "").lower()
        category = torrent.get("category", "").lower()
        completed = int(torrent.get("completion_on") or 0)
        if category not in {"radarr", "sonarr"} or float(torrent.get("progress", 0)) < 1:
            continue
        if not completed or current_time - completed < MIN_COMPLETE_AGE or torrent_hash in active:
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
