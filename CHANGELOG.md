# Changelog

Changes are recorded by release. Dates identify planned/local source versions, not a claim that a release has been published remotely.

## 0.2.0 — 2026-11-09

- Prepared a separate installable MusicMatcher package, reusing the original project's extraction and matching foundations.
- Added a local web workflow for file/Spotify input, settings, deterministic searches, review and authorized audio processing.
- Added UTF-8 CSV/JSON import, stable occurrence IDs, provenance and explicit catalogue replacement.
- Kept matching in Python and yt-dlp; no AI providers, model credentials or inference calls.
- Made the candidate limit configurable, with a default of 8 and a range of 1–25.
- Added progress events, resumable state, cooperative cancellation and a bounded PKCE browser callback.
- Separated FFmpeg metadata handling from audio retrieval without a Mutagen dependency.
- Prepared English/Spanish documentation, an MIT license, third-party notices, test CI and source/wheel release workflows.

Native installers, bundled FFmpeg/Deno, automatic remote repository creation and live-service verification across all supported platforms are not part of this source release.
