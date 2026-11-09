# Contributing

MusicMatcher is a small, local-first Python application. Changes should keep it understandable and usable without programming knowledge. Read [the architecture](docs/architecture.md) and [AGENTS.md](AGENTS.md) before changing a module boundary.

## Set up a development checkout

Use Python 3.11+ and a virtual environment. From the repository root:

```bash
python -m pip install -e ".[dev]"
python -m pytest
python -m ruff check src tests
```

FFmpeg and ffprobe enable the local generated-audio tests. Deno enables live YouTube extraction but is not required for mocked unit tests. Do not add network calls, real account access or music downloads to the default test suite.

## Make a change

1. Describe the concrete behavior and a small reproduction in an issue or pull request.
2. Keep changes focused. Reuse the importer, matcher, persistence and audio components instead of duplicating their logic in the web interface.
3. Add regression tests for meaningful failure modes: an incorrect recording, data loss, duplicate processing, interruptions or an unsafe request.
4. Update the English and Spanish READMEs when commands, input formats or user-visible behavior change.
5. Run the relevant tests and lint checks. For packaging changes, also run `python -m build` and inspect the wheel/source contents.

State what you actually checked, including skipped checks or unavailable external tools. Do not claim live service compatibility from mocked tests. CI defines an OS/Python matrix; its results must be observed before declaring that matrix verified.

## Matching rules

Matching is deterministic; the application does not use AI models or providers. Keep strong identity checks for title, performers, duration, numbered parts and version qualifiers. A high similarity score must not override a contradiction. Send uncertain cases to manual review and retain the reasons and candidates.

Preserve original metadata separately from comparison copies. Duplicates retain their playlist positions and share work only when musical identity and metadata are compatible. Never identify a recording by title alone. Manual choices remain authoritative until the user changes them or the original metadata becomes incompatible.

## Data and security

Never commit populated `.env` files, Spotify tokens, real playlists, logs containing private data, audio downloads or user settings. Use invented metadata and mock URLs in tests. Treat uploaded files, scraped titles, provider messages, local settings and saved JSON as untrusted input.

Keep the web server on loopback, validate request origin/host, preserve the request-token protections, and render imported text as text. Use argument arrays for subprocesses. Keep URL validation, bounded timeouts/retries, atomic JSON writes, cancellation checks and catalogue locks in place.

Permissions to download audio or use artwork are separate from recording matches. Do not infer permission from a video being public, verified, official or Creative Commons-labelled without an applicable authorization decision. Do not add DRM bypass or account-cookie extraction.

## Dependency and release changes

Keep dependencies small and public. Review yt-dlp/EJS compatibility together. Do not add `yt-dlp[default]`, Mutagen, vendored binaries or a new licensing obligation without updating the dependency/licensing design and third-party notices.

Release tags must match the package version in `pyproject.toml`, for example `v0.2.0`. The release workflow builds a wheel and source archive and creates a GitHub release after its checks. Creating/pushing a tag is a publishing action for repository maintainers. A source archive or wheel is not a native installer; describe it accurately.

For security reports, follow [SECURITY.md](SECURITY.md) rather than opening a public issue containing credentials or exploit details.
