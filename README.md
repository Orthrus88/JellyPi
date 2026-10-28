# JellyPi

JellyPi turns one Raspberry Pi into a self-hosted media server with automated
requests, downloads, organization, compatibility encoding, and monitoring.
Every application starts with an empty configuration. The repository contains
no user accounts, API keys, media history, provider tokens, or VPN credentials.

For the complete data flows, path contract, service checks, maintenance jobs,
and failure runbooks, use the [operations reference](docs/operations.md).

## Included services

| Service | Address | Purpose |
| --- | --- | --- |
| Jellyfin | `http://PI_ADDRESS:8096` | Media library and playback |
| Foreseerr | `http://PI_ADDRESS:5055` | Friendly movie and series requests with optional discovery integrations |
| Radarr | `http://PI_ADDRESS:7878` | Movie management |
| Sonarr | `http://PI_ADDRESS:8989` | Series management |
| Prowlarr | `http://PI_ADDRESS:9696` | Indexer management |
| Bazarr | `http://PI_ADDRESS:6767` | English subtitle search and sidecars |
| qBittorrent | `http://PI_ADDRESS:8080` | Downloads routed through Gluetun |
| SABnzbd (optional) | `http://PI_ADDRESS:8081` | Usenet downloads |
| Download recovery (optional) | Background service | Failed Usenet cleanup and replacement searches |
| Audiobookshelf (optional) | `http://PI_ADDRESS:13378` | Audiobook library and listening |
| AudioBookRequest (optional) | `http://PI_ADDRESS:8000` | Friendly audiobook requests |
| Chaptarr (optional) | `http://PI_ADDRESS:8789` | Audiobook search, import, and organization |
| Encoder status | `http://PI_ADDRESS:8097` | Encoder queue, progress, and disk use |

The encoder converts files to a conservative 1080p H.264/AAC profile suitable
for Roku, Chromecast, smart TVs, and web browsers. It validates the beginning,
middle, and end of every output before atomically replacing the source file.

## Hardware and software

- Raspberry Pi 4 or newer with a 64-bit operating system
- A USB 3 storage device mounted at `/srv/media`, or another path of your choice
- Docker Engine with the Compose plugin
- A custom OpenVPN configuration supported by Gluetun

Ubuntu Server 24.04 LTS 64-bit is the reference operating system.

## Quick start

Clone the repository on the Pi and enter the project directory:

```bash
git clone https://github.com/Orthrus88/JellyPi.git
cd JellyPi
cp .env.example .env
```

Edit `.env`. At minimum, confirm `PUID`, `PGID`, `MEDIA_ROOT`, and
`CONFIG_ROOT`; set the qBittorrent and OpenVPN credentials that will be used on
your installation.

Place the provider's OpenVPN file at:

```text
CONFIG_ROOT/gluetun/custom.conf
```

Then create the directories and start the stack:

```bash
chmod +x scripts/*.sh
./scripts/bootstrap.sh
```

The first run can take several minutes while Docker downloads and builds the
images.

## First-run configuration

Use the same internal paths everywhere so the applications agree about files:

| Application | Setting | Value |
| --- | --- | --- |
| qBittorrent | Completed downloads | `/data/Downloads/complete` |
| qBittorrent | Incomplete downloads | `/data/Downloads/incomplete` |
| Radarr | Movie root folder | `/data/Movies` |
| Sonarr | Series root folder | `/data/TV` |
| Radarr/Sonarr | Download client host | `gluetun` |
| Radarr/Sonarr | Download client port | `8080` |
| Jellyfin | Movie library | `/data/movies` |
| Jellyfin | Shows library | `/data/tv` |

1. Open qBittorrent, replace its temporary password, and copy the same username
   and password into `.env`. Under **Settings > Advanced**, set **Network
   interface** to `tun0`. Leave UPnP/NAT-PMP disabled.
2. Give Radarr downloads the category `movies` and Sonarr downloads the category
   `tv` in both download clients.
3. Add Radarr and Sonarr to Prowlarr under **Settings > Apps**.
4. Connect Foreseerr to Jellyfin, Radarr, and Sonarr using their internal service
   names and ports.
5. Restart the cleanup service after changing `.env`:

   ```bash
   docker compose up -d --force-recreate torrent-cleanup
   ```

The cleanup worker waits until Radarr or Sonarr has finished importing a
completed download, then removes the torrent and its download files. The
organized library copy remains in `Movies` or `TV`. It verifies a newer import
record for every main video file first, so clearing a failed item from an Arr
queue cannot cause the only downloaded copy to be deleted.

When Usenet is enabled, the same worker also removes a torrent if SABnzbd is
already downloading that exact movie or episode. Torrents that never resolve
metadata, make no progress, or average less than 16 KiB/s without a connected
seed for 30 minutes are removed and blocklisted; Radarr or Sonarr then searches
again. Episode recovery searches only the affected episodes rather than an
entire season.

qBittorrent shares Gluetun's network namespace and is additionally bound to the
VPN interface. Gluetun's firewall acts as the kill switch if the tunnel drops.
The Compose file deliberately does not publish the torrent listening port on
the Pi's normal network interface. Provider-side port forwarding is a separate,
optional feature and is useful only when the VPN provider supports it.

## Optional Usenet downloads

SABnzbd is included as an optional Compose profile. The standard quick start
does not run it. To enable it, set this in `.env`:

```dotenv
COMPOSE_PROFILES=usenet
```

Then apply the change:

```bash
./scripts/bootstrap.sh
```

Open `http://PI_ADDRESS:8081` and use SABnzbd's setup wizard to add your Usenet
provider. Enable SSL when the provider supports it. Set these folders under
**Settings > Folders**:

| SABnzbd setting | Value |
| --- | --- |
| Temporary Download Folder | `/data/usenet/incomplete` |
| Completed Download Folder | `/data/usenet/complete` |

Create categories named `movies`, `tv`, and `audiobooks`, with matching folders
under `/data/usenet/complete`. In Radarr and Sonarr, add
SABnzbd as a download client using host `sabnzbd`, port `8080`, its API key,
and the `movies` or `tv` category. Give SABnzbd the higher priority if qBittorrent
should serve only as a fallback.

Add NZBGeek or another Newznab-compatible indexer in Prowlarr, then sync it to
Radarr and Sonarr. Provider passwords and indexer/API keys are entered only in
the application interfaces and remain under the untracked `CONFIG_ROOT`.

For a one-hour torrent fallback window, add a Radarr/Sonarr delay profile with
Usenet preferred, Usenet delay `0`, torrent delay `60`, and **Bypass if Highest
Quality** disabled. This prevents the same release from immediately starting
in both download clients.

The `usenet` profile also starts the download-recovery worker. Every five
minutes it checks SABnzbd for failures that are at least ten minutes old.
Failures tracked by Radarr or Sonarr are left to their built-in blocklist and
redownload handling. If a manual SAB retry becomes detached from its Arr queue,
the worker maps it back to the monitored movie or episode, requests a replacement
search, and removes the failed SAB history entry and incomplete files. Its state
is stored under `CONFIG_ROOT/download-recovery`, so a restart cannot submit the
same replacement search twice.

Change the timing in `.env` when needed:

```dotenv
RECOVERY_INTERVAL_SECONDS=300
RECOVERY_MIN_FAILURE_AGE_SECONDS=600
```

Review recent recovery decisions with:

```bash
docker compose logs --tail=50 download-recovery
```

For a conservative retry of monitored, released titles that remain missing
after the original request search, install the host timer:

```bash
./scripts/install-missing-search.sh
```

It searches at most ten episodes and two movies per hourly run, skips the
specific movies or episodes already present in an Arr queue, and waits 24 hours
before retrying the same item. This fills the gap between an initial request
search and RSS feeds without hammering indexers or queueing an entire library
at once.

To disable SABnzbd later, remove `usenet` from `COMPOSE_PROFILES` and run:

```bash
docker compose stop sabnzbd
docker compose rm -f sabnzbd
```

## Optional audiobook library

The `audiobooks` profile adds Audiobookshelf for playback, AudioBookRequest for
search and requests, and Chaptarr for downloading, naming, and importing books.
It uses the same Prowlarr, SABnzbd, and VPN-bound qBittorrent services as the
movie and TV stack. Audiobook files are stored under `MEDIA_ROOT/Audiobooks`.

Enable it with Usenet support in `.env`:

```dotenv
COMPOSE_PROFILES=usenet,audiobooks
```

Run `./scripts/bootstrap.sh`, then complete the one-time setup:

1. Open Audiobookshelf at `http://PI_ADDRESS:13378`, create its administrator,
   and add a book library using `/audiobooks`.
2. Open Chaptarr at `http://PI_ADDRESS:8789`. Add `/audiobooks` as an audiobook
   root folder. Add SABnzbd at `sabnzbd:8080` with priority `1`; add
   qBittorrent at `gluetun:8080` with a lower priority such as `10`.
3. Add Chaptarr to Prowlarr under **Settings > Apps** using
   `http://chaptarr:8789`, so audiobook indexers are synchronized.
4. Open AudioBookRequest at `http://PI_ADDRESS:8000` and create its root user.
   Under **Settings > Prowlarr / Chaptarr**, select Chaptarr and use
   `http://chaptarr:8789`. Under **Settings > Download**, enable auto-download.
5. Create an Audiobookshelf API token, then connect it in AudioBookRequest with
   `http://audiobookshelf:80` and select the audiobook library.

AudioBookRequest uses the stable major-version `:1` image by default. The image
name remains configurable through `AUDIOBOOKREQUEST_IMAGE`, allowing a specific
release to be pinned without changing the Compose file.

For listening on iPhone, use an Audiobookshelf-compatible client and set its
server URL to the HTTPS hostname of your reverse proxy. Downloaded books remain
available offline. Keep Chaptarr private to the home network; only the playback
and request services need public hostnames.

## Optional live TV and guide

The `iptv` profile adds Dispatcharr plus IPTV-org's EPG generator. Dispatcharr
normalizes M3U channels for Jellyfin, while the generator builds a local XMLTV
guide from IPTV-org's current channel and guide metadata.

Generate the channel map before starting the profile:

```bash
mkdir -p /srv/jellypi/config/iptv-epg
python3 scripts/generate-iptv-org-epg-channels.py \
  /srv/jellypi/config/iptv-epg/channels.xml
```

Set `COMPOSE_PROFILES=iptv` (or add `iptv` to the existing comma-separated
value), then run `docker compose up -d`. Dispatcharr is available at
`http://PI_ADDRESS:9191`, and the generated XMLTV guide is served at
`http://PI_ADDRESS:9200/guide.xml`. Add a lawful M3U source in Dispatcharr, add
the generator URL as an XMLTV source, and use Dispatcharr's `/output/m3u` and
`/output/epg` endpoints in Jellyfin. For IPTV-org playlists, append
`?tvg_id_source=tvg_id` to both output URLs so Jellyfin and the guide share the
same channel identifiers. After the XMLTV import succeeds, run **Channels >
Auto-Match EPG** in Dispatcharr so each supported channel receives its listing.

The guide refreshes at 3:00 AM and 7:00 PM local time and covers the current
UTC date by default. Both values are configurable in `.env`. A channel can
still play when IPTV-org has no compatible English guide definition; only its
schedule will be blank.

## Compatibility encoder

The feasibility and implementation boundary for preserving originals as
Jellyfin alternate versions is documented in the
[pre-transcoding plugin design](docs/pretranscoding-plugin.md).

The encoder waits 15 minutes after a file changes so it does not touch an active
download or import. A file is left alone when it is already:

- H.264, 8-bit 4:2:0 video
- 1080p or lower
- SDR
- At or below the configured target bitrate
- Equipped with an AAC stereo audio track

Otherwise, the encoder preserves the original audio and subtitle tracks, adds
an AAC stereo compatibility track when needed, and encodes video to H.264 with
the configured bitrate ceiling. The original is replaced only after duration
and sample playback validation pass. HDR sources are tone mapped to SDR. Files
that require conversion are written as Matroska (`.mkv`) so copied audio and
subtitle tracks remain valid; Radarr and Sonarr pick up the same-name file on
their next library scan.

The Pi worker uses at most two CPU cores by default, leaving capacity for
direct playback and the other services. Change `ENCODER_CPU_LIMIT` in `.env`
if the Pi has more or less headroom.

Pause or resume encoding with:

```bash
docker compose stop encoder
docker compose start encoder
```

Change the bitrate, quality, preset, or safety delay in `.env`, then recreate
the encoder and dashboard containers.

### Optional NVIDIA desktop acceleration

An NVIDIA desktop can run the encoder with NVENC while the Pi remains an
automatic fallback. The desktop gets priority whenever its heartbeat is fresh;
if it goes offline, the Pi resumes the queue after five minutes. Both workers
appear on the encoder status page and coordinate through the shared media disk.

See [Optional NVIDIA encoding worker](docs/nvenc-worker.md) for the Windows and
Linux setup, GPU check, shared-storage requirement, and start/stop commands.

### EVO-compatible subtitles

Some direct-play clients, including EVO Player on PS5, require Bazarr's
external English subtitle to have exactly the same basename as the video.
Run the sidecar normalizer after Bazarr or a media-renaming step:

```bash
./scripts/install-subtitle-sidecars.sh
```

The worker leaves every correctly matched English sidecar unchanged, including
`.en.hi.srt` and anime subtitles. It recovers an orphaned English sidecar after
a movie is renamed when the folder contains exactly one video, giving the
recovered file the canonical `Video Name.en.srt` name. It never guesses in
multi-episode folders, and it does not modify or transcode the media file.
The timer checks every ten minutes and records its latest result in
`/var/lib/jellypi/subtitle-sidecars.json`. The installer also disables the
older subtitle-embedding timer when upgrading an existing JellyPi host.

## VPN recovery check

Gluetun normally reconnects by itself. An optional hourly host timer can also
test the complete qBittorrent path and recreate both containers if the internet
is available but the VPN path is not:

```bash
./scripts/install-vpn-watchdog.sh
```

The timer runs at ten minutes past every hour, which avoids common router reboot
times at the top of the hour. The installer records the current repository
location, so the watchdog always uses the same Compose and `.env` files as
manual updates.

## Trakt and AniList

Account synchronization is intentionally absent from this repository. Install
the desired Jellyfin plugins from the Jellyfin catalog and authorize each
account from the Jellyfin dashboard. Those tokens remain inside the untracked
`CONFIG_ROOT/jellyfin` directory.

## Updating

```bash
git pull
docker compose pull
docker compose up -d --build
```

Application state is stored below `CONFIG_ROOT`, and media is stored below
`MEDIA_ROOT`, so updating or recreating containers does not remove either.

To install the optional nightly stable-image updater on a systemd host:

```bash
./scripts/install-media-stack-updater.sh
```

It runs at about 03:20 local time, after the dedicated Foreseerr stable-release
check, validates the Compose file, pulls configured tags, recreates only changed
containers, and fails the unit when a container does not return to `running`.

Run a non-destructive check of the enabled stack at any time:

```bash
./scripts/verify-stack.sh
```

## Backup

Back up `.env` privately, plus the `CONFIG_ROOT` directory. Media can be backed
up independently. Do not commit either location to Git.
