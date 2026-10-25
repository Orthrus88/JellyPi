# Foreseerr adult-search overlay

This optional image keeps Foreseerr's Jellyfin, Trakt, AniList, and intervention
features while enabling TMDb adult results in native text search for an explicit
allowlist of Foreseerr user IDs.

No account is allowed by default. Set `FORESEERR_ADULT_USER_IDS` to a
comma-separated list such as `1` or `1,7`. Other users continue sending
`include_adult=false` to TMDb.

Build and run it with the base JellyPi stack:

```sh
FORESEERR_ADULT_USER_IDS=1 docker compose \
  -f compose.yaml \
  -f compose.foreseerr-adult.yaml \
  up -d --build seerr
```

The image is pinned to `FORESEERR_VERSION` (default `v0.14.1`). The Docker build
first runs `git apply --check`, so an upstream source change that no longer
matches the patch fails the build instead of silently deploying an unfiltered or
broken image. Update the version deliberately, build it, and smoke-test search
before replacing a running container.

`foreseerr-stable-update` is an optional replacement for a nightly Foreseerr
updater. It checks GitHub for the latest stable release and builds the patched
image before stopping the current container. If the patch no longer applies,
the update fails with the working release still online. After a successful
build it backs up the configuration, deploys the image, verifies its reported
version and health, and rolls back both configuration and Compose on failure.

This controls Foreseerr search visibility. Apply separate library access rules
in Jellyfin if downloaded media must also be hidden from specific playback
accounts.
