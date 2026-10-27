# JellyPi pre-transcoding plugin feasibility

## Decision

A Jellyfin plugin can orchestrate JellyPi pre-transcoding on Jellyfin 12.3
without waiting for another Jellyfin feature, provided the compatibility copy
is the default version. The plugin can expose configuration, register scheduled
tasks, inspect the library, submit jobs to the existing Pi and NVIDIA workers,
and refresh the completed item.

True per-client version selection is outside this scope. Jellyfin currently
keeps the queried primary version first even when another version would direct
play or fit the bandwidth limit. Implementing automatic selection such as
"AV1 for this client, H.264 for that client" still requires a Jellyfin core
change equivalent to feature request 508. There is no supported plugin hook in
the 12.3 playback pipeline that replaces this ordering decision.

This gives the plugin a useful, complete first version:

- Preserve the source as an alternate version.
- Create and validate a conservative H.264/AAC compatibility version.
- Make the compatibility version the deterministic default.
- Leave the original selectable through Jellyfin's version menu.
- Keep the existing replace-in-place encoder mode available for installations
  that do not want the additional storage use.

No Jellyfin client changes are required for that behavior.

## Evidence checked

The deployed server reported Jellyfin `12.3.0`. A proof plugin targeting
`net10.0` and the `Jellyfin.Controller` and `Jellyfin.Model` `12.3.0` packages
was compiled with zero warnings and zero errors. It was then loaded into an
isolated official `jellyfin/jellyfin:12.3` container. Jellyfin discovered the
plugin and exposed its `Queue compatibility versions` task in Scheduled Tasks.

Two naming checks were also run against Jellyfin 12.3's own `Emby.Naming`
resolver:

- A movie compatibility file using the exact movie-folder basename was selected
  as primary while `- Original` was grouped as its alternate.
- Episode files labelled `A Compatibility` and `Z Original`, without resolution
  labels, were grouped as one episode and the compatibility file was selected
  as primary.

Both checks passed. Production code should generate names itself instead of
depending on the release name supplied by Radarr or Sonarr.

Relevant upstream behavior:

- [Official plugin template](https://github.com/jellyfin/jellyfin-plugin-template)
- [Plugin scheduled tasks](https://jellyfin.org/docs/general/server/tasks/#plugin-tasks)
- [Multiple-version naming](https://jellyfin.org/docs/general/server/media/movies/#multiple-versions)
- [Jellyfin 12.3 playback-source ordering](https://github.com/jellyfin/jellyfin/blob/v12.3/Jellyfin.Api/Helpers/MediaInfoHelper.cs#L367-L405)
- [Test that the queried version remains the default](https://github.com/jellyfin/jellyfin/blob/v12.3/tests/Jellyfin.Api.Tests/Helpers/MediaInfoHelperTests.cs#L65-L83)
- [Jellyfin 12.3 version resolver](https://github.com/jellyfin/jellyfin/blob/v12.3/Emby.Naming/Video/VideoListResolver.cs#L274-L319)

## Plugin boundary

The plugin should coordinate work rather than run FFmpeg inside the Jellyfin
server process. Long encodes belong in the existing JellyPi workers so a failed
encode, GPU driver problem, or worker restart cannot take Jellyfin down.

The plugin owns:

1. An administrator configuration page for profiles, schedules, storage limits,
   included libraries, and preserve-original policy.
2. A scheduled task and a manual task that inspect movies and episodes through
   `ILibraryManager`.
3. Idempotent job manifests under `.jellypi-encoder/jobs` containing the Jellyfin
   item ID, source fingerprint, target profile, output path, and requested mode.
4. Job status and cancellation endpoints for the Jellyfin dashboard.
5. A targeted library refresh after a worker atomically publishes a version.
6. Reconciliation after Radarr or Sonarr replaces the tracked source.

The workers own:

1. FFprobe compatibility inspection.
2. CPU or NVENC conversion.
3. Duration and beginning/middle/end sample validation.
4. Atomic publication and failure cleanup.
5. Heartbeats, claims, progress, and retry state.

## File strategy

The worker must never expose a partial version to Jellyfin. It writes a hidden
temporary file, validates it, renames the existing source to its generated
original-version name, and atomically publishes the compatibility version.

For movies, the compatibility file uses the exact parent-folder basename:

```text
Movies/Movie (2024)/
  Movie (2024).mkv
  Movie (2024) - Original.mkv
```

For episodes, generated labels avoid resolution tokens because Jellyfin sorts
resolution-labelled versions from highest to lowest. The compatibility label
sorts before the original label:

```text
TV/Series/Season 01/
  Series S01E01 - A Compatibility.mkv
  Series S01E01 - Z Original.mkv
```

The UI labels can be refined during implementation, but the ordering contract
must remain covered by resolver tests. External subtitle sidecars need a copy or
rename for each version basename so clients such as EVO can see them.

## Operational constraints

- Radarr and Sonarr track one main file per movie or episode. The plugin must
  treat Arr upgrades as source replacement events and rebuild or remove the old
  compatibility version. It must not expect Arr to manage both versions.
- The source fingerprint must include size, modification time, duration, and
  stream information. A changed source invalidates the prior output.
- The compatibility output carries the JellyPi encoder-profile metadata so it
  is not submitted again.
- A configurable free-space floor stops new jobs before two full versions could
  exhaust a storage branch.
- Removing the plugin must not remove media. Its files remain valid Jellyfin
  versions and can be cleaned through a separate explicit task.
- Upgrades of Jellyfin require a matching plugin build because Jellyfin plugin
  package versions must match the server API version.

## Delivery sequence

1. Add the plugin project and reproducible package build for Jellyfin 12.3.
2. Add read-only discovery and a dry-run scheduled task.
3. Add the shared job-manifest protocol to the existing workers.
4. Test one copied movie and one copied episode, including subtitles and resume
   state, in an isolated library.
5. Add atomic version publication and targeted Jellyfin refresh.
6. Test Radarr and Sonarr upgrades, deletion, rescans, and worker interruption.
7. Enable preserve-original mode as an opt-in setting. Keep the current
   replace-in-place mode as the default until the version workflow passes the
   full integration suite.

Per-client automatic source selection can be added later if Jellyfin implements
feature request 508 or exposes a supported playback-source ordering extension.
