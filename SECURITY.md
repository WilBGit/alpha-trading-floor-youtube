# Security

## Credential handling

Never commit Google OAuth client files, authorized-user tokens, refresh tokens, API keys, database passwords, provider credentials, `.env` files, or local runtime state. The default Google token path is outside the repository. Use an owner-only secret store or environment injection.

The uploader validates the video and thumbnail before contacting YouTube. Upload privacy defaults to `private`; changing it to `unlisted` or `public` requires an explicit runtime setting and an operator review.

## Fail-closed release boundary

The normal CLI path does not upload. `--upload` is required, and the uploader still requires a valid OAuth token and a thumbnail that is exactly 1280x720, within the configured size bounds, and readable as an image. Preflight auto-fixes are disabled unless `--fix` is explicitly supplied.

A successful upload is not complete until the returned video ID and thumbnail result are recorded and verified. Do not retry blindly after an ambiguous provider response because that can create duplicate videos.

## Data and privacy

Do not publish personal information, private channel analytics, token files, raw downloaded datasets, logs, generated media, or database contents. Treat Yahoo Finance, StockTwits, Finviz, RSS, and public GitHub data as proxy sources unless exchange entitlements and provenance are independently confirmed.

## Incident response

If a credential enters Git, a log, a terminal capture, an issue, a pull request, or chat, stop the workflow, revoke or rotate it at the provider, remove it from the working tree and history, and re-run secret scans before restoring service.
