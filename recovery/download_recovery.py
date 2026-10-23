#!/usr/bin/env python3
import json
import os
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

SAB_URL = os.getenv("SAB_URL", "http://sabnzbd:8080").rstrip("/")
SONARR_URL = os.getenv("SONARR_URL", "http://sonarr:8989").rstrip("/")
RADARR_URL = os.getenv("RADARR_URL", "http://radarr:7878").rstrip("/")
SAB_CONFIG = os.getenv("SAB_CONFIG", "/arr-config/sabnzbd/sabnzbd.ini")
SONARR_CONFIG = os.getenv("SONARR_CONFIG", "/arr-config/sonarr/config.xml")
RADARR_CONFIG = os.getenv("RADARR_CONFIG", "/arr-config/radarr/config.xml")
STATE_FILE = Path(os.getenv("RECOVERY_STATE_FILE", "/state/state.json"))
INTERVAL = int(os.getenv("RECOVERY_INTERVAL_SECONDS", "300"))
MIN_FAILURE_AGE = int(os.getenv("MIN_FAILURE_AGE_SECONDS", "600"))


def api_key_from_xml(path: str) -> str:
    value = ET.parse(path).getroot().findtext("ApiKey")
    if not value:
        raise RuntimeError(f"No API key found in {path}")
    return value.strip()


def sab_api_key(path: str) -> str:
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    match = re.search(r"^api_key\s*=\s*(\S+)\s*$", text, re.MULTILINE)
    if not match:
        raise RuntimeError(f"No SABnzbd API key found in {path}")
    return match.group(1)


def request_json(
    url: str,
    params: dict | None = None,
    api_key: str | None = None,
    method: str = "GET",
    body: dict | None = None,
):
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    data = None if body is None else json.dumps(body).encode()
    headers = {"Accept": "application/json"}
    if api_key:
        headers["X-Api-Key"] = api_key
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=30) as response:
        raw = response.read()
        return json.loads(raw) if raw else None


def arr_get(base_url: str, api_key: str, path: str, params: dict | None = None):
    return request_json(base_url + path, params=params, api_key=api_key)


def arr_post(base_url: str, api_key: str, path: str, body: dict):
    return request_json(base_url + path, api_key=api_key, method="POST", body=body)


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {"handled": {}}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - 30 * 86400
    state["handled"] = {
        job_id: item
        for job_id, item in state.get("handled", {}).items()
        if item.get("time", 0) >= cutoff
    }
    temporary = STATE_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2, sort_keys=True))
    os.replace(temporary, STATE_FILE)


def sab_history(api_key: str) -> list[dict]:
    result = request_json(
        SAB_URL + "/api",
        {"mode": "history", "output": "json", "limit": 200, "apikey": api_key},
    )
    return result.get("history", {}).get("slots", [])


def sab_delete(api_key: str, job_id: str) -> None:
    try:
        result = request_json(
            SAB_URL + "/api",
            {
                "mode": "history",
                "name": "delete",
                "value": job_id,
                "del_files": 1,
                "output": "json",
                "apikey": api_key,
            },
        )
    except (TimeoutError, socket.timeout, urllib.error.URLError):
        if all(str(item.get("nzo_id")) != job_id for item in sab_history(api_key)):
            return
        raise
    if isinstance(result, dict) and result.get("status") is False:
        raise RuntimeError(f"SABnzbd refused to remove {job_id}")


def arr_queue(base_url: str, api_key: str) -> list[dict]:
    result = arr_get(
        base_url,
        api_key,
        "/api/v3/queue",
        {
            "page": 1,
            "pageSize": 500,
            "includeUnknownSeriesItems": "true",
            "includeUnknownMovieItems": "true",
        },
    )
    return result.get("records", [])


def manager_owns(records: list[dict], job_id: str) -> bool:
    for record in records:
        if str(record.get("downloadId", "")).casefold() != job_id.casefold():
            continue
        messages = " ".join(
            message
            for group in record.get("statusMessages", [])
            for message in group.get("messages", [])
        ).casefold()
        return "wasn't grabbed by" not in messages and "not grabbed by" not in messages
    return False


def manager_for_category(category: str) -> str | None:
    normalized = category.strip().casefold()
    if normalized in {"tv", "sonarr", "series"}:
        return "sonarr"
    if normalized in {"movie", "movies", "radarr"}:
        return "radarr"
    return None


def recover_tv(title: str, api_key: str) -> dict | None:
    parsed = arr_get(SONARR_URL, api_key, "/api/v3/parse", {"title": title})
    episode_ids = []
    for episode in parsed.get("episodes") or []:
        episode_id = episode.get("id")
        if not episode_id:
            continue
        detail = arr_get(SONARR_URL, api_key, f"/api/v3/episode/{episode_id}")
        if detail.get("monitored", True) and not detail.get("hasFile", False):
            episode_ids.append(episode_id)
    if not episode_ids:
        return None
    command = arr_post(
        SONARR_URL,
        api_key,
        "/api/v3/command",
        {"name": "EpisodeSearch", "episodeIds": episode_ids},
    )
    return {"manager": "Sonarr", "ids": episode_ids, "commandId": command.get("id")}


def recover_movie(title: str, api_key: str) -> dict | None:
    parsed = arr_get(RADARR_URL, api_key, "/api/v3/parse", {"title": title})
    movie_id = (parsed.get("movie") or {}).get("id")
    if not movie_id:
        return None
    detail = arr_get(RADARR_URL, api_key, f"/api/v3/movie/{movie_id}")
    if detail.get("hasFile", False) or not detail.get("monitored", True):
        return None
    command = arr_post(
        RADARR_URL,
        api_key,
        "/api/v3/command",
        {"name": "MoviesSearch", "movieIds": [movie_id]},
    )
    return {"manager": "Radarr", "ids": [movie_id], "commandId": command.get("id")}


def finish_cleanup(state: dict, sab_key: str, job_id: str) -> None:
    sab_delete(sab_key, job_id)
    state["handled"][job_id]["cleanupPending"] = False
    save_state(state)


def recovery_once() -> None:
    current_time = time.time()
    sab_key = sab_api_key(SAB_CONFIG)
    sonarr_key = api_key_from_xml(SONARR_CONFIG)
    radarr_key = api_key_from_xml(RADARR_CONFIG)
    sonarr_queue = arr_queue(SONARR_URL, sonarr_key)
    radarr_queue = arr_queue(RADARR_URL, radarr_key)
    state = load_state()
    failures = [item for item in sab_history(sab_key) if item.get("status") == "Failed"]

    for item in failures:
        job_id = str(item.get("nzo_id") or "")
        title = item.get("name") or item.get("nzb_name") or "unknown"
        category = manager_for_category(str(item.get("category") or ""))
        completed = float(item.get("completed") or 0)
        if not job_id or (completed and current_time - completed < MIN_FAILURE_AGE):
            continue

        previous = state.get("handled", {}).get(job_id)
        if previous:
            if previous.get("cleanupPending"):
                finish_cleanup(state, sab_key, job_id)
                print(f"recovery cleanup completed: {title}", flush=True)
            continue

        if category == "sonarr":
            if manager_owns(sonarr_queue, job_id):
                print(f"recovery deferred to Sonarr: {title}", flush=True)
                continue
            result = recover_tv(title, sonarr_key)
        elif category == "radarr":
            if manager_owns(radarr_queue, job_id):
                print(f"recovery deferred to Radarr: {title}", flush=True)
                continue
            result = recover_movie(title, radarr_key)
        else:
            print(f"recovery skipped unknown category: {title}", flush=True)
            continue

        if not result:
            print(f"recovery could not map missing media: {title}", flush=True)
            continue

        state.setdefault("handled", {})[job_id] = {
            "time": current_time,
            "title": title,
            "category": category,
            "result": result,
            "cleanupPending": True,
        }
        save_state(state)
        finish_cleanup(state, sab_key, job_id)
        print(f"recovery requested replacement: {title} -> {result}", flush=True)

    print(f"recovery check complete: failed_jobs={len(failures)}", flush=True)


def main() -> None:
    while True:
        try:
            recovery_once()
        except Exception as error:
            print(f"recovery error: {error}", flush=True)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
