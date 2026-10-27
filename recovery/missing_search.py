#!/usr/bin/env python3
"""Retry a small, rate-limited batch of monitored released media."""

import json
import os
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path


SONARR_URL = os.getenv("SONARR_URL", "http://127.0.0.1:8989").rstrip("/")
RADARR_URL = os.getenv("RADARR_URL", "http://127.0.0.1:7878").rstrip("/")
SONARR_CONFIG = Path(os.getenv("SONARR_CONFIG", "/var/lib/sonarr/config/config.xml"))
RADARR_CONFIG = Path(os.getenv("RADARR_CONFIG", "/var/lib/radarr/config/config.xml"))
STATE_FILE = Path(os.getenv("MISSING_SEARCH_STATE", "/var/lib/jellypi/missing-search.json"))
COOLDOWN_SECONDS = int(os.getenv("MISSING_SEARCH_COOLDOWN_SECONDS", "86400"))
SONARR_BATCH = int(os.getenv("MISSING_SEARCH_EPISODE_BATCH", "10"))
RADARR_BATCH = int(os.getenv("MISSING_SEARCH_MOVIE_BATCH", "2"))
DRY_RUN = os.getenv("MISSING_SEARCH_DRY_RUN", "false").lower() in {"1", "true", "yes"}


def api_key(path: Path) -> str:
    value = ET.parse(path).getroot().findtext("ApiKey")
    if not value:
        raise RuntimeError(f"ApiKey is missing from {path}")
    return value


def request(url: str, key: str, path: str, payload: dict | None = None):
    body = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        f"{url}/api/v3/{path}",
        data=body,
        headers={"X-Api-Key": key, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.load(response)


def load_state() -> dict:
    try:
        value = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp = STATE_FILE.with_name(f"{STATE_FILE.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temp, STATE_FILE)


def due(state: dict, item_key: str, timestamp: float) -> bool:
    try:
        return timestamp - float(state.get(item_key, 0)) >= COOLDOWN_SECONDS
    except (TypeError, ValueError):
        return True


def aired(record: dict, timestamp: float) -> bool:
    value = record.get("airDateUtc")
    if not value:
        return False
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() <= timestamp
    except ValueError:
        return False


def queued_ids(queue: dict, field: str) -> set[int]:
    return {
        item[field]
        for item in queue.get("records", [])
        if isinstance(item.get(field), int)
    }


def main() -> None:
    timestamp = datetime.now(timezone.utc).timestamp()
    state = load_state()
    searched: list[str] = []

    sonarr_key = api_key(SONARR_CONFIG)
    sonarr_queue = request(SONARR_URL, sonarr_key, "queue?page=1&pageSize=1000")
    queued_episodes = queued_ids(sonarr_queue, "episodeId")
    wanted = request(
        SONARR_URL,
        sonarr_key,
        "wanted/missing?page=1&pageSize=1000&sortKey=airDateUtc&sortDirection=ascending&includeSeries=true",
    ).get("records", [])
    episodes = [
        item
        for item in wanted
        if item.get("monitored", True)
        and item["id"] not in queued_episodes
        and aired(item, timestamp)
        and due(state, f"sonarr:{item['id']}", timestamp)
    ][:SONARR_BATCH]
    if episodes:
        ids = [item["id"] for item in episodes]
        if not DRY_RUN:
            request(SONARR_URL, sonarr_key, "command", {"name": "EpisodeSearch", "episodeIds": ids})
        for item in episodes:
            if not DRY_RUN:
                state[f"sonarr:{item['id']}"] = timestamp
            searched.append(
                f"episode {item.get('series', {}).get('title')} "
                f"S{item.get('seasonNumber'):02d}E{item.get('episodeNumber'):02d}"
            )

    radarr_key = api_key(RADARR_CONFIG)
    radarr_queue = request(RADARR_URL, radarr_key, "queue?page=1&pageSize=1000")
    queued_movies = queued_ids(radarr_queue, "movieId")
    movies = [
        item
        for item in request(RADARR_URL, radarr_key, "movie")
        if item.get("monitored")
        and item["id"] not in queued_movies
        and not item.get("hasFile")
        and item.get("status") in {"released", "inCinemas"}
        and due(state, f"radarr:{item['id']}", timestamp)
    ][:RADARR_BATCH]
    if movies:
        ids = [item["id"] for item in movies]
        if not DRY_RUN:
            request(RADARR_URL, radarr_key, "command", {"name": "MoviesSearch", "movieIds": ids})
        for item in movies:
            if not DRY_RUN:
                state[f"radarr:{item['id']}"] = timestamp
            searched.append(f"movie {item.get('title')} ({item.get('year')})")

    if not DRY_RUN:
        save_state(state)
    prefix = "would search" if DRY_RUN else "searched"
    if searched:
        print(f"{prefix}: " + "; ".join(searched))
    else:
        print("No due monitored missing media required a search")


if __name__ == "__main__":
    main()
