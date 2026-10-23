# JellyPi

JellyPi turns one Raspberry Pi into a self-hosted media server with automated
requests, downloads, organization, compatibility encoding, and monitoring.
Every application starts with an empty configuration. The repository contains
no user accounts, API keys, media history, provider tokens, or VPN credentials.

## Included services

| Service | Address | Purpose |
| --- | --- | --- |
| Jellyfin | `http://PI_ADDRESS:8096` | Media library and playback |
| Seerr | `http://PI_ADDRESS:5055` | Friendly movie and series requests |
| Radarr | `http://PI_ADDRESS:7878` | Movie management |
| Sonarr | `http://PI_ADDRESS:8989` | Series management |
| Prowlarr | `http://PI_ADDRESS:9696` | Indexer management |
| qBittorrent | `http://PI_ADDRESS:8080` | Downloads routed through Gluetun |
| SABnzbd (optional) | `http://PI_ADDRESS:8081` | Usenet downloads |
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
   and password into `.env`.
2. Give Radarr downloads the category `radarr` and Sonarr downloads the category
   `sonarr`.
3. Add Radarr and Sonarr to Prowlarr under **Settings > Apps**.
4. Connect Seerr to Jellyfin, Radarr, and Sonarr using their internal service
   names and ports.
5. Restart the cleanup service after changing `.env`:

   ```bash
   docker compose up -d --force-recreate torrent-cleanup
   ```

The cleanup worker waits until Radarr or Sonarr has finished importing a
completed download, then removes the torrent and its download files. The
organized library copy remains in `Movies` or `TV`.

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
| Temporary Download Folder | `/data/Usenet/incomplete` |
| Completed Download Folder | `/data/Usenet/complete` |

Create categories named `radarr` and `sonarr`. In Radarr and Sonarr, add
SABnzbd as a download client using host `sabnzbd`, port `8080`, its API key,
and the matching category. Give SABnzbd the higher priority if qBittorrent
should serve only as a fallback.

Add NZBGeek or another Newznab-compatible indexer in Prowlarr, then sync it to
Radarr and Sonarr. Provider passwords and indexer/API keys are entered only in
the application interfaces and remain under the untracked `CONFIG_ROOT`.

For a one-hour torrent fallback window, add a Radarr/Sonarr delay profile with
Usenet preferred, Usenet delay `0`, torrent delay `60`, and **Bypass if Highest
Quality** disabled. This prevents the same release from immediately starting
in both download clients.

To disable SABnzbd later, remove `usenet` from `COMPOSE_PROFILES` and run:

```bash
docker compose stop sabnzbd
docker compose rm -f sabnzbd
```

## Compatibility encoder

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
and sample playback validation pass.

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

## VPN recovery check

Gluetun normally reconnects by itself. An optional hourly host timer can also
test the complete qBittorrent path and recreate both containers if the internet
is available but the VPN path is not:

```bash
./scripts/install-vpn-watchdog.sh
```

The timer runs at ten minutes past every hour, which avoids common router reboot
times at the top of the hour.

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

## Backup

Back up `.env` privately, plus the `CONFIG_ROOT` directory. Media can be backed
up independently. Do not commit either location to Git.
