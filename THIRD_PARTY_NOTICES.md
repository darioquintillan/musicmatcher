# Third-party notices

The MIT license in this repository covers MusicMatcher's own code and documentation. It does not replace any third-party license or grant rights to music, metadata, artwork, trademarks, or service access.

Dependencies are installed as separate Python distributions. Their installed `*.dist-info/licenses` directories and upstream repositories contain the authoritative license texts, copyright notices and any notices for transitive dependencies. Preserve those notices if redistributing their code.

| Component | Upstream license | Use |
|---|---|---|
| [yt-dlp Python source/wheel](https://github.com/yt-dlp/yt-dlp/blob/2026.10.26/LICENSE) | Unlicense | YouTube metadata extraction and authorized audio retrieval. |
| [yt-dlp-ejs](https://github.com/yt-dlp/ejs/blob/main/LICENSE) | Unlicense; its distributed JavaScript bundles include MIT and ISC components | JavaScript support for yt-dlp. |
| [FastAPI](https://github.com/fastapi/fastapi/blob/master/LICENSE) | MIT | Local web API. |
| [Uvicorn](https://github.com/Kludex/uvicorn/blob/main/LICENSE.md) | BSD-3-Clause | Local ASGI server. |
| [Pydantic](https://github.com/pydantic/pydantic/blob/main/LICENSE) and [pydantic-core](https://github.com/pydantic/pydantic-core/blob/main/LICENSE) | MIT | Validation. |
| [Requests](https://github.com/psf/requests/blob/main/LICENSE) | Apache-2.0 | HTTP requests. |
| [Spotipy](https://github.com/spotipy-dev/spotipy/blob/master/LICENSE) | MIT | Spotify PKCE token exchange. |
| [Certifi](https://github.com/certifi/python-certifi/blob/master/LICENSE) | MPL-2.0 | Certificate bundle. |

The EJS bundle incorporates [Meriyah](https://github.com/meriyah/meriyah/blob/main/LICENSE) (ISC) and [Astring](https://github.com/davidbonnet/astring/blob/main/LICENSE) (MIT). Use the notices shipped with the installed EJS release when redistributing its bundled JavaScript. yt-dlp documents these components in its [dependency and licensing notes](https://github.com/yt-dlp/yt-dlp/blob/2026.10.26/README.md#dependencies).

This project deliberately installs the yt-dlp core package and EJS separately, **without `yt-dlp[default]`**. MusicMatcher does not import or depend on Mutagen; metadata is handled using external FFmpeg/ffprobe. Mutagen, included by yt-dlp's broad default extra, is GPLv2+ and must not be silently added to an MIT-only bundled distribution. Official standalone yt-dlp binaries can also contain GPL components; their licensing differs from the Python wheel. See [yt-dlp's licensing explanation](https://github.com/yt-dlp/yt-dlp/blob/2026.10.26/README.md#licensing).

## External programs

FFmpeg, ffprobe and Deno are installed by the user; this repository's wheel, source archive and release workflow do not bundle their executables.

- [FFmpeg/ffprobe](https://ffmpeg.org/doxygen/trunk/md_LICENSE.html): generally LGPL-2.1-or-later; optional code/libraries and build flags can change the resulting build to GPL or other applicable combinations. Check the exact binary's configuration and license before redistributing it. FFmpeg cannot be relicensed as MIT by this project.
- [Deno](https://github.com/denoland/deno/blob/main/LICENSE.md): MIT for the Deno project, with additional third-party notices for its distributed runtime.

Before making a self-contained executable or redistributing external programs, inventory the exact bundled versions, preserve their notices and satisfy any source-code/relinking obligations that apply. The current release workflow distributes only MusicMatcher's wheel and source archive, with dependency declarations. It does not certify the licensing of a future bundled executable.

Spotify and YouTube are third-party services. MusicMatcher is not affiliated with or endorsed by either service. Their terms apply independently of the software licenses.
