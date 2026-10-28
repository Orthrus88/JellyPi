# JellyPi operations reference

This is the canonical reference for a working JellyPi deployment. It describes
the supported single-Pi Compose install and the expanded deployment in which
storage, applications, CPU workers, and a Windows NVIDIA worker are split
across hosts. Keep credentials, API keys, account tokens, VPN files, and public
DNS account details outside this repository.

## Service map

| Component | Default port | Owns | Persistent data |
| --- | ---: | --- | --- |
| Jellyfin | 8096 | Library, users, playback, watch state | `CONFIG_ROOT/jellyfin` |
| Foreseerr | 5055 | Requests and discovery | `CONFIG_ROOT/seerr` |
| Radarr | 7878 | Movie selection, import, and naming | `CONFIG_ROOT/radarr` |
| Sonarr | 8989 | Series selection, import, and naming | `CONFIG_ROOT/sonarr` |
| Prowlarr | 9696 | Indexers and Arr synchronization | `CONFIG_ROOT/prowlarr` |
| Bazarr | 6767 | Subtitle search and external sidecars | `CONFIG_ROOT/bazarr` |
| SABnzbd | 8081 | Usenet transfer, repair, and unpack | `CONFIG_ROOT/sabnzbd` |
| qBittorrent through Gluetun | 8080 | Torrent transfer | `CONFIG_ROOT/qbittorrent` |
| Gluetun | none except qBittorrent UI | VPN tunnel and kill switch | `CONFIG_ROOT/gluetun` |
| Audiobookshelf | 13378 | Audiobook library and playback | `CONFIG_ROOT/audiobookshelf` |
| Chaptarr | 8789 | Audiobook search, import, and naming | `CONFIG_ROOT/chaptarr` |
| AudioBookRequest | 8000 | Audiobook requests | `CONFIG_ROOT/audiobookrequest` |
| Encoder dashboard | 8097 | Queue, worker, and storage visibility | encoder state directory |
| Dispatcharr | 9191 | Optional IPTV channel, guide, and stream management | `/opt/dispatcharr/data` on its host |

In the expanded layout, Jellyfin, the reverse proxy, Samba, and the encoder
dashboard run natively on the main Pi. The application containers run on the
app Pi. Two Pi workers each scan two storage branches. The Windows GPU worker
scans the combined SMB share and has priority while its heartbeat is current.

## Canonical storage layout

All applications must see the same relative paths. Case matters on Linux.

```text
MEDIA_ROOT/
  Movies/
  TV/
  Audiobooks/
  Downloads/
    complete/
    incomplete/
  usenet/
    incomplete/
    complete/
      movies/
      tv/
      audiobooks/
  .jellypi-encoder/
```

The expanded deployment exposes a mergerfs pool at `/srv/media-pool`. Each
underlying SSD is mounted at `/srv/media` on its owner and exported over NFS.
The pool must use `category.create=epmfs`, `minfreespace=20G`, and
`moveonenospc=true`. `epmfs` keeps sidecars, temporary encoder files, artwork,
and renamed media on a branch where the parent directory already exists.

All media directories use the same numeric media group on every Linux host.
The working deployment uses UID 1000 for application processes, a shared media
GID, setgid directories, and a `0002` umask. Verify with:

```bash
findmnt /srv/media /srv/media-pool
namei -l /srv/media-pool/Movies
docker exec -u "$PUID:$PGID" radarr sh -c \
  'f=/data/Movies/.write-test; : > "$f" && rm -f "$f"'
```

Do not use a root shell in a container as the write test for an NFS-backed
pool. NFS root squashing can reject root while the real service UID works.

## Movie and series workflow

1. A user requests a title in Foreseerr.
2. Foreseerr sends movies to Radarr and series or seasons to Sonarr.
3. Radarr or Sonarr searches Prowlarr. Usenet is priority `1`; qBittorrent is
   priority `10`.
4. The delay profile prefers Usenet immediately and delays torrents 60 minutes.
   `Bypass if Highest Quality` stays disabled, preventing simultaneous grabs.
5. SABnzbd downloads to `/data/usenet/incomplete`, then repairs and unpacks to
   `/data/usenet/complete/movies` or `/data/usenet/complete/tv`. Torrent data
   uses `/data/Downloads` in the reference Compose install; a deployment may
   call the same shared path `/data/Incoming` if every client agrees.
6. Radarr or Sonarr imports to `/data/Movies` or `/data/TV`, renames the file,
   records the imported download, and asks the download client to remove it.
7. The torrent cleanup worker removes imported torrents only after it can prove
   a newer Arr import exists for every main video file. It also removes a
   duplicate torrent when SABnzbd already owns the same movie or episode.
8. Jellyfin's filesystem watcher or next library scan registers the imported
   file. Foreseerr reconciles download state every minute and scans Arr every
   15 minutes.
9. Bazarr searches for English subtitles and writes an external sidecar beside
   the video. The normalizer repairs safe one-video-folder naming mismatches.
10. The encoder waits 15 minutes from the last file change, then converts only
    media outside the H.264 8-bit SDR/AAC compatibility profile.

An empty download-client queue is normal after a successful import. A completed
item remaining for more than one cleanup interval, an Arr queue item marked
`warning`, or a monitored released title with no file requires investigation.

## Audiobook workflow

1. A trusted user requests a book in AudioBookRequest.
2. AudioBookRequest sends the selected edition to Chaptarr.
3. Chaptarr searches the audiobook-capable Prowlarr indexers and sends the grab
   to SABnzbd first, with VPN-bound qBittorrent as fallback.
4. SABnzbd uses category `audiobooks` and completes into
   `/data/usenet/complete/audiobooks`.
5. Chaptarr imports and names the result below `/audiobooks/Author/Book`.
6. Audiobookshelf scans `/audiobooks` and exposes it to phone clients.

Adding one book may add the author's catalog to Chaptarr. Those other books
remain unmonitored unless the user explicitly monitors them.

## Optional live TV with Dispatcharr

The expanded deployment runs Dispatcharr on encoder Node 2 at
`http://192.168.1.12:9191`. Its Compose project is
`/opt/dispatcharr/compose.yaml`, and all application state is bind-mounted at
`/opt/dispatcharr/data`. Docker is enabled at boot and the container uses the
`unless-stopped` restart policy.

The current deployment imports IPTV-org's United States playlist from
`https://iptv-org.github.io/iptv/countries/us.m3u`. It refreshes every 24 hours
and auto-syncs 174 channels from the sports, news, outdoor, entertainment/news,
and weather groups. New groups are not enabled automatically. Imported groups
use next-available channel numbering, and channels removed from the source are
removed on a later sync after Dispatcharr's seven-day stale retention period.

Jellyfin consumes Dispatcharr's generated M3U at
`http://192.168.1.12:9191/output/m3u` with a simultaneous-stream limit of `0`.
In Jellyfin, open **Live TV**, change the view from **Programs** to
**Channels**, and select a station. Programs and the grid remain empty until a
reliable XMLTV/EPG source is configured. Do not attach an unverified or stale
guide URL merely to populate those views.

For another source, add a lawful M3U or Xtream Codes account and its EPG,
select the groups to import, and create channels before connecting it to
Jellyfin. On Dispatcharr's Channels page, use the generated HDHR or M3U link as
the Jellyfin tuner and the generated EPG link as the guide data source. Leave
the Jellyfin simultaneous-stream limit at `0` for an M3U tuner so Dispatcharr
can enforce any provider limit.

IPTV-org entries are community-maintained public stream links. Individual
channels may be offline, geo-blocked, or replaced upstream even while the
playlist and Dispatcharr are healthy. Confirm a failure in Dispatcharr's
channel preview before troubleshooting Jellyfin.

Node 2 has no supported video GPU inside Dispatcharr. Prefer redirect, proxy,
or FFmpeg stream-copy profiles for compatible sources. CPU transcoding of
multiple live streams can compete with the node's media-encoder service and is
not part of the supported baseline.

## Downloader and VPN invariants

The following are required:

- qBittorrent has `network_mode: service:gluetun`.
- qBittorrent is bound to interface `tun0` in Advanced settings.
- No torrent listening port is published on the normal host interface.
- Gluetun reports healthy and owns `tun0` before qBittorrent starts.
- Radarr and Sonarr have both clients enabled, with SABnzbd at priority 1.
- Both Arr applications use a 60-minute torrent delay with Usenet preferred.
- SABnzbd's temporary and complete folders exist on every mergerfs branch.
- SAB categories are `movies`, `tv`, and `audiobooks`.

The host VPN watchdog checks the tunnel and qBittorrent namespace hourly. It
recreates Gluetun and qBittorrent only when the host has internet access but the
VPN path is unhealthy.

## Import and recovery

Radarr and Sonarr must mount the same `/data` tree as both download clients.
With identical paths, remote path mappings are unnecessary. Their root folders
must be `/data/Movies` and `/data/TV` and must report accessible.

The download recovery timer checks failed SABnzbd history every five minutes.
Tracked failures remain with the Arr blocklist/retry workflow. Detached manual
failures are mapped back to a monitored movie or episode, searched once, and
then removed from SAB history. Its state file prevents duplicate searches.

If SABnzbd shows `Completed` for hours, inspect the matching Arr queue before
clearing history. `Import pending` with `Destination already exists` can mean a
valid library file is present but the Arr database cannot parse its filename.
Confirm the library copy, give the video and all of its sidecars a canonical
`SxxEyy` or movie-year name, refresh the series or movie, and verify `hasFile`
before removing the completed downloader record. Never clear the record first;
the missing-media search may otherwise download the same title again.

The missing-media timer searches a small hourly batch and applies a 24-hour
cooldown per item. It excludes only movies and episodes already represented in
their Arr queue; an unrelated download does not pause every missing search.

After restoring files outside an Arr import, run a refresh and scan before any
missing-title search:

```text
Sonarr: System > Tasks > Refresh Series
Radarr: System > Tasks > Refresh Movie
Jellyfin: Dashboard > Scheduled Tasks > Scan Media Library
```

This prevents existing recovered files from being downloaded again.

## Encoder workflow

The encoder preserves all audio and subtitle streams, adds an AAC stereo track
when absent, and converts incompatible video to H.264 8-bit SDR. HDR input is
tone-mapped through `zscale` and `tonemap`. It validates duration plus samples
from the beginning, middle, and end before atomically replacing the source.

Workers coordinate with a shared claim and heartbeat. The preferred Windows
GPU worker pauses the Pi workers while its heartbeat is less than five minutes
old. If it goes offline, the Pi workers resume. Each Pi worker should scan its
local SSD plus one remote SSD so the four branches are covered once. The main
Pi worker remains disabled when it is the playback server.

The live Windows worker uses `windows/desktop_encoder.py`, a hidden scheduled
task, and an NVIDIA FFmpeg build. Its local failure cache suppresses repeated
processing of an unchanged bad input. Fix or replace the file, then remove only
that matching cache entry. Temporary `.encoder-part` output must be removed
after a failed validation; the worker now does this automatically.

## Subtitle workflow

Bazarr is connected to Radarr and Sonarr and requests English SRT sidecars.
Embedded-subtitle extraction is disabled. External subtitles avoid ASS/PGS
burn-in and the buffering it causes on low-power clients.

The normalizer runs every ten minutes. It keeps exact names such as
`Movie Name.en.srt` and `Episode Name.en.hi.srt`. It does not guess inside a
folder containing multiple videos. The old subtitle-embedding timer is retired
and should be disabled.

The sidecar timer changes names only; it never transcodes media. Jellyfin may
need one library scan after a new sidecar arrives before a client lists it.

## Startup ordering

NFS servers must be enabled on every storage owner and require `/srv/media`.
On the app Pi, Docker must have `RequiresMountsFor=/srv/media-pool` and start
after `remote-fs.target`. On the main Pi, Jellyfin, Samba, and the dashboard must
require `/srv/media-pool`. Encoder workers must require their local disk and
their assigned remote mount.

These dependencies prevent containers from starting against empty directories
when a storage server is late. They also prevent downloads from being written
to a Pi's root filesystem by mistake.

## Scheduled maintenance

| Job | Normal schedule | Purpose |
| --- | --- | --- |
| Foreseerr stable update | 03:00 daily | Advance the pinned stable Foreseerr tag |
| Container stack update | 03:15 daily | Update containers sequentially with a per-service backup and rollback |
| Jellyfin package update | 03:45 daily | Install stable Jellyfin Ubuntu packages |
| qBittorrent cleanup | every 5 minutes | Remove proven imports and recover stalls |
| SAB recovery | every 5 minutes | Retry detached failed downloads |
| Missing-media retry | hourly, 24-hour item cooldown | Search a small batch of monitored released movies and episodes |
| Subtitle sidecar normalization | every 10 minutes | Repair client-visible SRT names |
| VPN watchdog | hourly | Verify tunnel and namespace binding |
| Audiobook missing search | every 2 hours | Retry monitored missing books without flooding indexers |
| Dynu DDNS | every 5 minutes | Keep public host records current |

The expanded deployment's `media-containers-update.timer` updates one service
at a time, backs up its configuration, verifies its HTTP endpoint, and rolls
back that service if validation fails. The generic single-Pi installer in this
repository uses `jellypi-media-stack-update.timer` instead; do not enable both
timers on the same host.

## Public access

Only HTTPS on the main reverse proxy is forwarded from the router. Jellyfin,
Foreseerr, Audiobookshelf, and AudioBookRequest receive separate hostnames.
Radarr, Sonarr, Prowlarr, Bazarr, SABnzbd, qBittorrent, Chaptarr, and the encoder
dashboard remain LAN-only.

The DDNS timer updates all public records to the current WAN address. Test the
public path from a device with Wi-Fi disabled; a LAN test can be affected by
router hairpin-NAT behavior.

## Routine verification

Run the repository check on the container host:

```bash
sudo JELLYPI_STACK_DIR=/opt/media-stack ./scripts/verify-stack.sh
```

Then verify each host's services:

```bash
systemctl --failed
# Main Pi
systemctl is-active nfs-server jellyfin caddy smbd media-encoder-dashboard
# App Pi
systemctl is-active nfs-server docker
# Encoder Pi
systemctl is-active nfs-server media-encoder
systemctl list-timers --all | grep -E 'jellypi|foreseerr|recovery|cleanup|dynu'
findmnt /srv/media /srv/media-pool
```

Review application truth rather than relying only on container state:

- Radarr and Sonarr **System > Status** have no health messages.
- Their queues contain no intervention or import-warning rows.
- Root folders are accessible and report pool free space.
- Prowlarr can test NZBGeek and at least one torrent indexer.
- Prowlarr's top-level **Indexers** page lists providers. **Settings >
  Indexers** lists only optional indexer proxies, so an empty page there is
  normal when no proxy is configured.
- SABnzbd can write incomplete and all three category directories.
- qBittorrent's public IP is the VPN IP and its interface is `tun0`.
- Bazarr can write a sidecar into both movie and TV roots.
- Jellyfin can read the pool and finishes a library scan.
- The encoder dashboard reports fresh heartbeats and no unexplained failures.
- Dispatcharr returns HTTP 200 on port 9191, accepts the administrator login,
  and has no unexpected restart loop. An empty channel list is normal before a
  source is configured.

## Incident runbook

**Every container is up but applications hang:** check NFS and mergerfs first.
Restart the failed NFS server, confirm all remote mounts, then restart the app
stack. Do not repeatedly restart individual containers while `/data` is hung.

**Radarr or Sonarr says a SAB path does not exist:** verify the lowercase
`/data/usenet` paths inside both the Arr container and SABnzbd. Create category
directories on every mergerfs branch and test as the service UID/GID.

**An existing series appears missing:** run Refresh Series and wait for the
rescan to finish before searching. Compare the series file count with the disk.

**A torrent is stalled:** let the cleanup timer apply its age and speed rules.
Confirm there is no active SAB job for the same target. Manual deletion is the
last step because it bypasses blocklisting and recovery context.

**SAB loses connection briefly:** check Gluetun only if torrents are also
affected; SAB does not use the VPN namespace. A transient error during a
container restart is expected. A current Arr health warning is not.

**The GPU worker is offline:** confirm the scheduled task, Python process, SMB
write access, FFmpeg NVENC encoder, and fresh heartbeat. Pi workers should show
`paused` while the GPU is active and resume after five minutes when it is not.

**Subtitles exist on disk but a client does not list them:** confirm the sidecar
basename exactly matches the video, run the normalizer, then scan the Jellyfin
library once and restart the client.

## Backups and secrets

Back up `.env`, application configuration roots, the native Jellyfin data
directory, reverse-proxy configuration, and systemd overrides to encrypted
storage outside the media pool. Arr applications create their own database
backups, but those do not cover every service. Media and configuration are
separate recovery domains.

Never commit provider credentials, API keys, tokens, user databases, VPN files,
or account-sync data. A clean clone must start with empty application state.
