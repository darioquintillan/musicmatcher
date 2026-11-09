# Security policy

## Scope and supported version

Security fixes target the current maintained release and the development branch. Old releases may need upgrading. MusicMatcher is a local desktop workflow with a loopback web interface; it is not a multi-user or internet-facing hosted service.

Report vulnerabilities through the GitHub repository's **Security → Advisories → Report a vulnerability** feature when the repository owner has enabled private reporting. If private reporting is unavailable, open a minimal issue asking the maintainer for a private reporting channel. Do not put secrets, private playlists or exploitation details in that public issue. No response-time commitment is currently offered.

Include the package/Python/OS versions, affected action, a minimal reproduction using invented data, expected impact and relevant redacted logs. Do not download copyrighted music to demonstrate a bug.

## Local data and accounts

By default, state, settings, logs and renewable Spotify tokens live under `~/.musicmatcher`. A custom `--data-dir` or `MUSICMATCHER_DATA_DIR` changes that location. The Spotify Client ID is public application identification; the renewable token is sensitive. No Client Secret or model API key is required.

Use an account-controlled local folder and your operating system's access permissions. Do not place the data folder in a public web directory, shared repository or automatically shared folder. Logs and state may contain private song lists, paths and URLs even when they contain no authentication tokens. Review them before sharing.

To stop retaining a Spotify account:

1. Stop any running import/job and close the local server.
2. Remove the `.spotify-cache-*` files from that instance's data folder. They are the local renewable tokens.
3. Remove the Spotify-derived catalogue/state, review/mapping exports and any logs containing account data that should no longer be retained. Back up independently owned data first if it shares the folder.
4. Remove the application's access in your Spotify account settings if you also wish to revoke its server-side grant.

Removing the public Client ID setting alone does not revoke an existing OAuth token. Do not cache Spotify data indefinitely; retain only what is necessary for the authorized local workflow.

## Web and input boundaries

Keep the server bound to `127.0.0.1`. Do not expose it with `0.0.0.0`, a reverse proxy or a public tunnel. Host/origin validation and the per-process request token protect local mutation endpoints; they are not a general remote authentication system. Close browser sessions you do not trust while authorizing accounts, and do not share a session token or callback URL.

Uploaded CSV/JSON, imported titles, external metadata and saved state are untrusted. Input size limits and Pydantic validation prevent many malformed requests, but do not establish download or artwork rights. An imported authorization field must not enable a download.

Spotify OAuth uses PKCE, a random state value, an exact callback path and a loopback listener. Its interactive wait is bounded to 180 seconds and checks cancellation. Network operations have bounded timeouts; cancellation may wait for the current network or FFmpeg operation to reach a checkpoint.

## External programs and downloads

Install Python, FFmpeg/ffprobe and Deno from trusted distributions. They execute locally with your user's permissions. This project does not bundle their binaries or implement DRM bypass, account-cookie extraction, proxy rotation or access-control evasion.

The matcher accepting a recording is separate from permission to download it. Only authorize content whose rights holder and service permit the requested use. This application records a declaration and cannot automatically establish legal permission.

See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) before redistributing dependencies or adding bundled executables.
