# Changelog

Changes are recorded by release. Dates identify planned/local source versions, not a claim that a release has been published remotely.

## Unreleased

- Added adaptive discovery: inspect one video first, expanding only uncertain cases to the configured maximum without repeating video details; show actual evaluated counts in the web interface.
- Added a flexible matching profile as the app default while retaining the previous strict rules as an option.
- Accept missing structured music metadata, collaborator/album/producer credits and unknown explicit flags as warnings when other evidence matches; retain rejection of known incompatible recordings.
- Recognize Spanish live/acoustic labels and generic remix labels before an album suffix.
- Added bounded first-N searches/downloads in the web interface, API and CLI without truncating the catalog.
- Added profile-aware caches and MP3 duration validation, and clearer reporting of missing download permission.
- Verified a real three-candidate search over the first ten playlist rows in 72.5 seconds; download authorization remains a separate explicit user declaration.

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
