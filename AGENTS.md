# Maintenance instructions

## Project scope

MusicMatcher is a local Python app using yt-dlp, deterministic matching, a CLI and a loopback web interface. The user explicitly removed AI from the project: do not add providers, model keys, prompting, inference, or model dependencies. Keep the historical `youtube_search_agent` module name if useful for compatibility; it does not denote an AI agent.

The original project outside this repository is separate. Do not edit its files or copy its private data, credentials, populated environment files, caches, downloads or Git history into this repository.

## Preserve module boundaries

- `playlist_importer` and `spotify_importer`: input, metadata and provenance.
- `youtube_search_agent`: yt-dlp search/details and persistent search orchestration.
- `match_validator`: pure deterministic matching rules and explanations.
- `mapping_manager`: state, identities, exports, atomic writes and locks.
- `audio_downloader`: authorized retrieval, conversion orchestration and resumable audio work.
- `metadata_manager`: external FFmpeg/ffprobe operations, ID3 and final-file validation.
- `config_manager` and `models`: validated settings and schemas.
- `service`: one-job coordination, progress and cancellation.
- `cli` and `web`: presentation and request handling; call shared logic rather than reimplementing it.

Use Python 3.11+ and public dependencies declared in `pyproject.toml`. Run `python -m pytest` and `python -m ruff check src tests` for meaningful changes. For packaging work, run `python -m build` and inspect the resulting wheel and source archive. Do not run live account operations or actual music downloads as routine tests; use mocks and generated local audio.

## Functional invariants

- Preserve exact source titles/artists for metadata; normalize only copies used for comparison and portable filenames.
- Preserve all positions and duplicate occurrences. Reuse work by compatible identity, never title alone.
- Candidate count is now an adaptive maximum: start with one result and expand up to the configured maximum when evidence is weak, contradictory or lacks an official-upload hint. The maximum defaults to 8 and accepts 1–25 in both interfaces. Never retrieve the first video's full metadata twice during expansion. Keep UI, CLI, model bounds and documentation consistent.
- The user's later instruction makes the app's default matching mode `relaxed`; retain explicit `strict` validation for comparison and regression coverage. Flexible mode may accept missing structured metadata and unknown explicit flags, while known artist/version/number contradictions remain disqualifying. Keep the selected mode in cache identities and match results.
- A bounded trial (`track_limit` / `--first`) must preserve the full catalog and never search or download rows beyond that prefix.
- After the initial search, expand only automatic `review` rows up to `review_candidate_limit` (default 8) when it exceeds the first-pass maximum. Reuse known candidates and inspect only unseen URLs. Preserve accepted/manual/unmatched/error rows and keep each pass's summary. Never interrupt an active first pass to load a new implementation; queue a handover at its natural completion.
- Contradicting artist, version, duration, numbered work or movement cannot be overridden by a high similarity score.
- Prefer review when evidence is missing or ambiguous. Keep reasons and candidates for inspection.
- Respect manual decisions. Invalidate incompatible authorization/download state when a selected URL changes or is no longer accepted.
- Download permission is explicit and bound to the selected URL. Imported files cannot create trusted permissions.
- Keep JSON writes atomic, preserve existing final files on validation failure and retain recoverable download temporaries.
- Make cancellation cooperative and bound network/OAuth waits. Never leave a worker waiting on terminal input.

## Security and provenance

Keep web binding restricted to loopback and preserve host/origin/request-token protections. Escape/render imported text as text, validate URLs, and pass subprocess argument arrays without shell evaluation. Do not log OAuth codes, renewable tokens or credentials. Keep data under the configured local data directory and out of Git.

Spotify is optional and uses each user's own Client ID and PKCE. Its current playlist restrictions and quotas must be documented accurately from official sources. Preserve `source='spotify'` for Spotify-derived input across JSON/CSV imports. The legacy `ai_allowed` field must remain false. Do not download Spotify audio or automatically persist/embed Spotify artwork without applicable authorization.

No DRM bypass, account-cookie extraction, proxy rotation or access-control evasion. A public/official upload does not prove download rights.

## Distribution

The project's code is MIT; third-party components retain their licenses. Do not add `yt-dlp[default]`, Mutagen, vendored runtimes or bundled FFmpeg without revisiting the licensing design. Update `THIRD_PARTY_NOTICES.md` for dependency changes. Update pinned yt-dlp/EJS versions together and verify compatibility.

Keep both READMEs aligned. Distinguish configured CI from observed successful jobs, mocks from live-service tests, and wheels/source archives from standalone installers. Do not claim a GitHub publication or platform validation that has not happened. Commits may be made when authorized by the task; pushes, remote publication and releases need the user's applicable authorization.
