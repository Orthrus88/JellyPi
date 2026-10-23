#!/usr/bin/env bash
set -euo pipefail

VOLUME="jellypi-encoder-smoke-${RANDOM}-$$"
docker volume create "$VOLUME" >/dev/null
cleanup() {
  docker volume rm "$VOLUME" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker run --rm \
  -v "$VOLUME:/media" \
  --entrypoint /bin/sh \
  jellypi-encoder \
  -c 'mkdir -p /media/Movies /media/TV /media/.jellypi-encoder &&
      ffmpeg -hide_banner -loglevel error -f lavfi \
        -i testsrc2=size=320x180:rate=24 -t 1 \
        -vf format=yuv420p10le -c:v libx265 -preset ultrafast \
        -x265-params hdr-opt=1:repeat-headers=1:colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc:log-level=error:pools=1 \
        -color_primaries bt2020 -color_trc smpte2084 -colorspace bt2020nc \
        /media/Movies/smoke-hdr.mp4 &&
      chown -R 1000:1000 /media'

docker run --rm \
  --user 1000:1000 \
  -e MIN_AGE_SECONDS=0 \
  -e WORKER_NAME=smoke-worker \
  -v "$VOLUME:/media" \
  jellypi-encoder python3 /app/media_encoder.py --once

docker run --rm \
  -v "$VOLUME:/media" \
  --entrypoint /bin/sh \
  jellypi-encoder \
  -c 'test ! -e /media/Movies/smoke-hdr.mp4 &&
      test -e /media/Movies/smoke-hdr.mkv &&
      test "$(ffprobe -v error -select_streams v:0 -show_entries stream=codec_name -of csv=p=0 /media/Movies/smoke-hdr.mkv)" = h264 &&
      test "$(ffprobe -v error -select_streams v:0 -show_entries stream=color_transfer -of csv=p=0 /media/Movies/smoke-hdr.mkv)" = bt709'
