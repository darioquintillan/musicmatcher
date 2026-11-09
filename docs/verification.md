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
