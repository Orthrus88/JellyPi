# Optional NVIDIA encoding worker

An NVIDIA desktop can process the compatibility queue much faster than the
Raspberry Pi. JellyPi can give that computer priority while it is online and
automatically return work to the Pi when it goes offline.

The desktop and Pi must see the same media root. It must contain `Movies`, `TV`,
and `.jellypi-encoder`. Share the Pi's media disk over SMB or NFS, mount it on
the desktop, and make sure Docker can read and write that mount.

The native Windows worker used by the expanded deployment is included as
`windows/desktop_encoder.py`. Configure `JELLYPI_MEDIA_SHARE`,
`JELLYPI_FFMPEG`, and optionally `JELLYPI_WORKER_LABEL` in its scheduled-task
environment. Launch it with `pythonw.exe` so it does not flash a console window.

## Requirements

- An NVIDIA GPU with NVENC support
- Current NVIDIA drivers
- Docker with NVIDIA GPU access
- The JellyPi repository cloned on the desktop
- The Pi media root mounted on the desktop with write access

On Windows, Docker Desktop GPU support requires its WSL 2 backend. Follow
[Docker's GPU setup guide](https://docs.docker.com/desktop/features/gpu/) and
verify GPU access before starting the worker:

```powershell
docker run --rm --gpus all nvidia/cuda:12.9.0-base-ubuntu22.04 nvidia-smi
```

Linux hosts require the
[NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html).

## Start the desktop worker

Copy the example environment file:

```powershell
Copy-Item .env.nvenc.example .env.nvenc
```

Edit `GPU_MEDIA_ROOT` in `.env.nvenc`. On Windows this can be a drive mounted as
`Z:/`; on Linux it can be a path such as `/mnt/jellypi-media`. It must point to
the same directory that the Pi uses as `MEDIA_ROOT`.

Start only the encoder service on the desktop:

```powershell
docker compose --env-file .env.nvenc -f compose.yaml -f compose.nvenc.yaml up -d --build encoder
```

Check that FFmpeg can see NVENC:

```powershell
docker exec media-encoder-nvenc ffmpeg -hide_banner -encoders
```

The output should include `h264_nvenc`. The encoder status page on the Pi will
show both `desktop-gpu` and `pi-fallback` after both workers have reported in.

## Priority and fallback behavior

The desktop writes a heartbeat to the shared `.jellypi-encoder` directory. The
Pi waits while that heartbeat is less than five minutes old. If the desktop is
shut down, sleeps, loses the share, or stops its container, the Pi starts taking
jobs after the timeout.

Each worker creates an atomic claim before touching a file. A worker that is
already encoding finishes its current file before another worker takes the next
one. An abandoned claim is recovered after ten minutes. Temporary output stays
hidden and the original file is replaced only after validation succeeds.
HDR sources are tone-mapped to BT.709 SDR. A validation failure deletes the
temporary output so that it cannot permanently block the queue.

Stop or restart the desktop worker with:

```powershell
docker compose --env-file .env.nvenc -f compose.yaml -f compose.nvenc.yaml stop encoder
docker compose --env-file .env.nvenc -f compose.yaml -f compose.nvenc.yaml start encoder
```

`NVENC_CQ` controls quality. Lower values produce higher quality and larger
files. `NVENC_PRESET=p4` is the default balance between speed and quality.
