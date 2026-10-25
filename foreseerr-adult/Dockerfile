# syntax=docker/dockerfile:1.7

ARG FORESEERR_VERSION=v0.14.1

FROM --platform=$BUILDPLATFORM alpine/git:2.49.1 AS source
ARG FORESEERR_VERSION
RUN git clone --depth 1 --branch "${FORESEERR_VERSION}" \
      https://github.com/selmant/foreseerr.git /src
COPY enable-adult-search.patch /tmp/enable-adult-search.patch
RUN cd /src \
    && git apply --check /tmp/enable-adult-search.patch \
    && git apply /tmp/enable-adult-search.patch

# The patched route compiles to architecture-neutral JavaScript. Building it on
# BUILDPLATFORM avoids requiring CPU emulation when producing a Pi/ARM64 image.
FROM --platform=$BUILDPLATFORM oven/bun:1.4.1-alpine AS build
WORKDIR /app
COPY --from=source /src .
RUN --mount=type=cache,id=bun,target=/root/.bun/install/cache \
    CYPRESS_INSTALL_BINARY=0 bun install --frozen-lockfile
RUN bun run build:server

# Keep Foreseerr's official target-architecture runtime and replace only the
# compiled search route. All other Foreseerr features and dependencies remain
# byte-for-byte upstream.
FROM ghcr.io/selmant/foreseerr:${FORESEERR_VERSION}
USER root
COPY --chown=bun:bun --from=build \
  /app/dist/routes/search.js /app/dist/routes/search.js
USER bun:bun
