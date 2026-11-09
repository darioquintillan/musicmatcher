# Verification record

Verified locally on Windows with Python 3.12.14 on 9 November 2026.

- `python -m pytest -q`: 102 tests and 3 subtests passed. Network/account operations are mocked in the automated suite. The audio tests use generated local signals and external FFmpeg/ffprobe.
- `python -m ruff check src tests`: passed.
- `node --check src/musicmatcher/static/app.js`: passed.
- `python -m pip check`: no broken requirements.
- `python -m build`: wheel and source distribution created; package contents inspected for UI assets, notices and private runtime data.
- Browser verification of the actual local server: CSV file chooser/import, persisted candidate count (8 → 3 → 8), settings dialog, ordered duplicate rows, manual link editing, distinct audio authorization, live search/candidate progress, pause and resume to completion, and an empty browser error log.
- The browser's search used the supplied fictional example catalog and real public metadata requests. No music was downloaded, no download permission was asserted, and no Spotify account was used in that browser test.

The original project is separate and was not modified. The new source contains no copied personal playlist, Client ID, environment file, OAuth token or audio file.

Not verified by this record: GitHub-hosted CI runs/releases, macOS/Linux execution, a standalone executable, or live Spotify OAuth in the new application. The OAuth implementation is covered by local mocked callback, state, timeout and cancellation tests. The configured multi-platform CI matrix is preparation for those additional checks.

The installed Starlette test client currently emits a deprecation warning about its supported HTTP test transport. The tests pass; this is not a reported application failure.

## Follow-up: flexible matching and a user-authorized ten-song trial

- `python -m pytest -q`: 125 tests and 3 subtests passed after adding flexible matching, profile-specific cache invalidation, compatible remix/credit parsing and first-N batch guards.
- Real lookup of the first ten rows with exactly three candidates per row: 72.5 seconds. Re-evaluation of those stored candidates after the remix parser correction required no additional discovery requests.
- The user explicitly confirmed permission to download these ten selected URLs. That declaration was recorded per URL in the private runtime state; no permission was inferred from Spotify Premium, a public upload or a successful match.
- Download/conversion of this authorized prefix: 92.8 seconds; ten completed MP3 files, all reporting 320000 bit/s through ffprobe, totaling 78.42 MB. All files were revalidated for audio, exact original title/artists/album, fingerprint and source URL.
- Generated an ordered M3U8. Compared the other 997 rows against the pre-trial runtime backup: they were preserved exactly.
- Browser check displayed Flexible, 3 candidates, first 10, 100% progress and a completed MP3 marker on each of the ten rows.
- Real audio, authorization declarations, source metadata and the trial report remain in the user's runtime folders outside the repository. They are not included in the package or Git history.

These checks establish a successful bounded trial. They do not prove recording identity acoustically or guarantee availability of every future YouTube URL.
