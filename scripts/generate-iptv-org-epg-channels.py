#!/usr/bin/env python3
"""Build an IPTV-org EPG channel list for the JellyPi live-TV groups."""

from __future__ import annotations

import argparse
import html
import json
import re
import urllib.request
from collections import Counter
from pathlib import Path


PLAYLIST_URL = "https://iptv-org.github.io/iptv/countries/us.m3u"
GUIDES_URL = "https://iptv-org.github.io/api/guides.json"

ENABLED_GROUPS = {
    "Auto;Outdoor;Series;Sports",
    "Comedy;Movies;Series;Sports",
    "Culture;News",
    "Documentary;News",
    "Entertainment;News",
    "Entertainment;Sports",
    "General;News",
    "News",
    "Outdoor;Sports",
    "Sports",
    "Weather",
}

# Prefer direct FAST-platform data before general listings sites. Entries not
# listed here remain eligible and sort after these providers.
SITE_PREFERENCE = [
    "i.mjh.nz",
    "pluto.tv",
    "plex.tv",
    "xumo.tv",
    "tvpassport.com",
    "tvguide.com",
    "watch.whaletvplus.com",
    "ontvtonight.com",
    "distro.tv",
    # tvtv.us is a fallback because its current US endpoints frequently return
    # HTTP 404 even while the guide definition remains in IPTV-org's API.
    "tvtv.us",
]


def fetch_text(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "JellyPi-EPG/1.0"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read().decode("utf-8")


def selected_channels(playlist: str) -> list[tuple[str, str, str | None]]:
    channels: list[tuple[str, str, str | None]] = []
    for line in playlist.splitlines():
        if not line.startswith("#EXTINF"):
            continue
        group = re.search(r'group-title="([^"]*)"', line)
        tvg_id = re.search(r'tvg-id="([^"]*)"', line)
        if not group or not tvg_id or group.group(1) not in ENABLED_GROUPS:
            continue
        full_id = tvg_id.group(1)
        if not full_id:
            continue
        channel_id, separator, feed = full_id.partition("@")
        channels.append((full_id, channel_id, feed if separator else None))
    return list(dict.fromkeys(channels))


def guide_rank(guide: dict, wanted_feed: str | None) -> tuple[int, int, str]:
    guide_feed = guide.get("feed")
    feed_rank = 0 if guide_feed == wanted_feed else 1 if guide_feed is None else 2
    try:
        site_rank = SITE_PREFERENCE.index(guide["site"])
    except ValueError:
        site_rank = len(SITE_PREFERENCE)
    return feed_rank, site_rank, guide["site"]


def build_xml(channels: list[tuple[str, str, str | None]], guides: list[dict]) -> tuple[str, Counter]:
    guides_by_channel: dict[str, list[dict]] = {}
    for guide in guides:
        if guide.get("channel") and guide.get("lang") == "en":
            guides_by_channel.setdefault(guide["channel"], []).append(guide)

    entries: list[str] = []
    sites: Counter = Counter()
    for full_id, channel_id, feed in channels:
        choices = guides_by_channel.get(channel_id, [])
        if not choices:
            continue
        guide = min(choices, key=lambda item: guide_rank(item, feed))
        entries.append(
            "  <channel"
            f' site="{html.escape(guide["site"], quote=True)}"'
            f' lang="{html.escape(guide["lang"], quote=True)}"'
            f' xmltv_id="{html.escape(full_id, quote=True)}"'
            f' site_id="{html.escape(str(guide["site_id"]), quote=True)}">'
            f'{html.escape(guide["site_name"])}'
            "</channel>"
        )
        sites[guide["site"]] += 1

    xml = '<?xml version="1.0" encoding="UTF-8"?>\n<channels>\n'
    xml += "\n".join(entries)
    xml += "\n</channels>\n"
    return xml, sites


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path, help="Destination channels.xml path")
    args = parser.parse_args()

    channels = selected_channels(fetch_text(PLAYLIST_URL))
    guides = json.loads(fetch_text(GUIDES_URL))
    xml, sites = build_xml(channels, guides)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(xml, encoding="utf-8", newline="\n")

    matched = sum(sites.values())
    print(f"Wrote {matched} guide mappings for {len(channels)} unique channel IDs")
    for site, count in sites.most_common():
        print(f"  {site}: {count}")


if __name__ == "__main__":
    main()
