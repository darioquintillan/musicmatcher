# MusicMatcher Local

[Español](README.es.md) · [Architecture](docs/architecture.md) · [Contributing](CONTRIBUTING.md)

Import a song list, find matching YouTube recordings, review uncertain results, and process audio whose download is authorized. MusicMatcher runs on your computer, with a local web interface and a CLI. Matching uses yt-dlp and deterministic Python rules. **There are no AI models, model accounts, API keys for models, or model inference calls.**

Metadata alone cannot prove that two uploads contain the same recording or master. MusicMatcher prefers an unresolved result to an incorrect automatic selection.

## Install

You need **Python 3.11+**. Install [FFmpeg and ffprobe](https://ffmpeg.org/download.html) for audio processing and [Deno 2.3+](https://docs.deno.com/runtime/getting_started/installation/) for full YouTube support. Make their executables available in `PATH`. FFmpeg is an external program; installing a Python package named `ffmpeg` does not install it.

Download or clone this repository and open a terminal in its root folder.

Windows PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\musicmatcher.exe web
```

Use another installed Python 3.11+ version if necessary. Activating the environment and changing PowerShell execution policy are optional.

Linux/macOS:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/musicmatcher web
```

On Debian/Ubuntu, `sudo apt install python3-venv ffmpeg` supplies the virtual-environment support and audio tools. Install Deno separately using its official instructions. macOS uses the same Python commands; install compatible FFmpeg/Deno builds for your machine.

After the initial installation, the supplied `start_windows.cmd` or `sh start.sh` launcher opens the local application. The terminal must stay open while the application runs. These are source launchers, not self-contained executables.

The web interface opens at **http://127.0.0.1:8765**. To choose another port or avoid opening the browser automatically:

```bash
musicmatcher web --port 8766 --no-browser
```

In commands below, `musicmatcher` means the executable from your virtual environment. You can also use `python -m musicmatcher` with that environment's Python.

## First use

1. Open the web interface and import a CSV/JSON file, or enter an accessible Spotify playlist URL.
2. In Settings, choose the output folder, MP3 bitrate and maximum search candidates. The maximum defaults to **8**, configurable from **1 to 25**. Search starts with **1** and only expands when evidence is uncertain.
3. Run the search. Follow the current phase, completed work, estimated remaining time and individual errors.
4. Review uncertain matches. Check the recording and version before accepting its URL.
5. Record an applicable download permission and its source, then start audio processing.

Spotify is optional. CSV/JSON imports work without a Spotify account or developer registration. Search does not download audio; accepted matches and download permissions are separate decisions. The application records your permission declaration; it does not automatically verify licenses. A public or official video does not itself establish permission to download it.

## Input files

Use UTF-8 encoding. A UTF-8 BOM is supported. Required columns/fields are `title` and `artists` (or `artist`). Optional fields include `album`, `duration_ms`, `duration_seconds`, `duration`, `isrc` and `explicit`.

```csv
title,artists,album,duration_seconds
Example Song,Example Artist,Example Album,210
Another Song,Example Artist|Guest Artist,Another Album,03:45
```

Use `|` or `;` between collaborators. A comma can belong to a band's name and is not split automatically. Quote CSV values containing the file's delimiter. Comma, semicolon and tab delimiters are supported. Spanish header aliases such as `Título`, `Artistas`, `Álbum` and `Duración` are also accepted.

JSON can be an array or an object containing `tracks`:

```json
{
  "tracks": [
    {
      "title": "Example Song",
      "artists": ["Example Artist", "Guest Artist"],
      "album": "Example Album",
      "duration_ms": 210000
    }
  ]
}
```

Numeric `duration` values mean seconds; `mm:ss` and `hh:mm:ss` are accepted. Missing duration is allowed, but prevents a fully verified automatic match. Web/CLI file inputs are limited to 5 MB and 10,000 tracks. Original titles, playlist order and duplicate occurrences are retained. Invalid input is rejected before replacing the saved catalogue.

Importing a different catalogue requires explicit replacement. Exported files do not grant permissions: download authorizations and completion records are never trusted from an imported file. Reimporting the same catalogue can reuse compatible decisions already stored locally.

## Optional Spotify setup

Create your own app in the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard). Enter its **Client ID** in the local Settings screen; a Client Secret is not required. Register exactly:

```text
http://127.0.0.1:8888/callback
```

The first import opens Spotify authorization in your browser. It uses PKCE, a temporary loopback callback and a renewable token stored in the local data folder. Authorization can be cancelled and times out after 180 seconds. The callback port must be available. Spotify does not accept `localhost` instead of the explicit loopback address for this configuration.

For current Development Mode apps, the app owner needs Premium and additional users must be allowlisted, up to the documented development limit. The current `/items` endpoint reads playlists that the authenticated user owns or collaborates on; an arbitrary public playlist URL is insufficient. Development quotas can also stop extraction. The application retains the previous catalogue when extraction fails or the playlist changes while pages are being read. See [Spotify access limits](https://developer.spotify.com/documentation/web-api/concepts/quota-modes), [playlist access](https://developer.spotify.com/documentation/web-api/reference/get-playlists-items), and the [2026 migration update](https://developer.spotify.com/blog/2026-02-06-update-on-developer-access-and-platform-security).

The repository does not supply a shared Spotify application or credentials. MusicMatcher does not download Spotify audio or automatically embed Spotify artwork. Cover images must be separately authorized. Spotify metadata retains its source attribution and links. When disconnecting Spotify, remove its cached tokens and retained account data through the application's disconnect/clear controls or the documented local data files. See [Spotify's developer terms](https://developer.spotify.com/terms).

## CLI

After the first pass, the app automatically expands **only automatic review decisions up to 8 candidates** by default, when this exceeds the initial maximum. It reuses known videos: going from 3 to 8 retrieves at most 5 new videos. Accepted/manual/unmatched/error rows stay unchanged. Change the second-pass maximum in Settings; setting it at or below the initial maximum disables this pass. Fewer available results are recorded honestly, and unresolved ambiguity still requires manual review. Run this phase independently with `musicmatcher search --review-only --candidates 8`.

### Flexible matching and bounded trial runs

The app now defaults to **Flexible** matching. It accepts uploader titles containing credits, album names and promotional labels without requiring structured musical metadata. Duration tolerance is `max(12 seconds, 5%)`. Missing explicit/clean evidence is a warning; known contradictions, incompatible versions, unavailable links and different numbered works remain rejected.

Flexible selection requires a score of at least 78 and a 2-point margin, except for uploads with equivalent metadata. Scores are not probabilities. The previous **Strict** mode remains selectable in the web interface.

Search is **adaptive**: request one YouTube result and its full metadata first. It can stop there if title/artist evidence is clear, duration differs by at most 3 seconds, there is an official-upload hint and no uncertain version/collaborator warnings. Early acceptance requires at least 90 points as well as the selected profile's rules. Missing evidence, contradictions or an unavailable first video trigger discovery up to the configured maximum; the first video's details are reused. Thus `--candidates 3` inspects **1 or up to 3 videos total**, never 1 + 3. The web interface shows the actual evaluated count. Remaining ambiguity requires manual review. A one-video decision cannot compare alternatives that were never retrieved.

Use “Procesar primeras canciones” to limit a trial to the first N rows while preserving the entire catalog. Leave it empty for all rows. The CLI equivalent is:

```bash
musicmatcher search --candidates 3 --matching-mode relaxed --first 10 --refresh
musicmatcher download --first 10
```

Download permissions are still separate and bound to each selected URL. Audio validation follows the selected duration tolerance and retains corruption/truncation checks.

```bash
musicmatcher import songs.csv
musicmatcher spotify "https://open.spotify.com/playlist/YOUR_PLAYLIST_ID"
musicmatcher search --candidates 8
musicmatcher review
musicmatcher accept "Exact song key shown in review" "https://www.youtube.com/watch?v=VIDEO_ID"
musicmatcher authorize "Exact song key shown in review" --note "Permission and its source"
musicmatcher download
musicmatcher export
musicmatcher doctor
```

Choose either a file import or Spotify for the active catalogue. `VIDEO_ID` above is a placeholder; use a real video link you have checked. Commands accept the exact keys shown in the review output, including suffixes for repeated titles.

```bash
musicmatcher import another-list.json --replace
musicmatcher search --candidates 12 --refresh
musicmatcher run songs.csv --candidates 8
musicmatcher --data-dir ./workspace-two web
```

`run` executes the phases and reuses saved work. Tracks awaiting review or permission remain pending. Search refresh preserves manual decisions. Use `musicmatcher COMMAND --help` for the full options of each command. `doctor` checks local dependencies without authenticating to accounts.

## Files, reusing results and audio

The default data folder is `~/.musicmatcher` (`C:\Users\YOUR_NAME\.musicmatcher` on Windows). Override it with `MUSICMATCHER_DATA_DIR` or the global `--data-dir` argument before the command. Settings and credentials can also be configured using an optional `.env` based on `.env.example`; keep a populated `.env` private.

| Data | Purpose |
|---|---|
| `estado.json` | Original metadata, ordered occurrences, candidates, decisions, permissions and processing status. |
| `mapping.json` | Ordered rich export, including original metadata, status, reasons and selected URLs. |
| `canciones.json` | Simple dictionary from unique song keys to accepted YouTube URLs. |
| `revision.json` | Unresolved matches and their reasons. |
| `settings.json` | Local output, search and Spotify settings. |
| `.spotify-cache-*` | Renewable Spotify tokens; do not share. |
| `job.json` / `events.log` | Job progress and individual events; review before sharing. |

Keep the full state file as well as the simple URL mapping: URLs alone cannot preserve album, separate artists, duration or permissions. Keep independent catalogues in different data folders. Do not run two instances against the same folder.

Audio processing selects the best available source audio and converts it with FFmpeg. MP3 bitrate defaults to 320 kbps; a higher output bitrate cannot restore information absent from the source. Portable filenames contain the original title/artists plus an identity suffix. ID3 tags retain original metadata; only filenames are sanitized. Exact duplicates reuse their output file. A generated M3U8 playlist preserves the order and repeated occurrences of completed files.

Results are saved incrementally. Repeating a phase reuses compatible work. Interrupted downloads retain recoverable temporary files. Existing final files are checked before reuse; damaged files are preserved with an error rather than silently overwritten. Source/URL, output settings or artwork changes may require a different output file.

YouTube can change its responses, block requests or remove a video. Errors are recorded per song; a provider-wide block pauses search. MusicMatcher does not implement cookies from accounts, DRM bypass, proxy rotation or access-control evasion. Only process content that both its rights holder and the service authorize you to download. [YouTube terms](https://www.youtube.com/static?template=terms), [yt-dlp YouTube notes](https://github.com/yt-dlp/yt-dlp/wiki/Extractors#youtube).

## Development and distribution

```bash
python -m pip install -e ".[dev]"
python -m pytest
python -m ruff check src tests
python -m build
```

Tests use simulated network services and locally generated audio fixtures; they do not need Spotify credentials or download music. FFmpeg-dependent tests require those executables and can be skipped when unavailable. CI is configured for Windows, Linux and macOS with Python 3.11/3.12. A configured matrix is not a claim that every platform or live service has already been verified.

This repository is prepared for publication; creating a remote repository and uploading it are separate actions. The release workflow produces a Python wheel and source distribution from a version tag. **It does not produce a standalone executable or bundle FFmpeg/Deno.** Cross-platform native installers remain future work.

MusicMatcher's own code is [MIT licensed](LICENSE). Dependencies keep their own licenses; see [third-party notices](THIRD_PARTY_NOTICES.md). YouTube support pins yt-dlp/EJS versions together. Update that pair deliberately and rerun tests when upstream changes, rather than adding yt-dlp's broad optional dependency extra.

For contributing and issue reports, see [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md).
