# Changelog

## 1.0.0 — 2026-10-09

First release.

- Falls back to qBittorrent when Decypharr refuses `torrents/add`: blocked for legal reasons (451), not cached, or Decypharr unreachable.
- Restarts torrents on qBittorrent when Decypharr reports them as failed after accepting them. Only torrents seen working first are moved.
- Merges `torrents/info` from both backends, and routes hash-based requests to whichever backend holds the torrent.
- Uses `<category>-fallback` categories in qBittorrent, created automatically with the original category's save path.
- Passes 401/403 from Decypharr through to the *arr instead of falling back.
- Adds a `/health` endpoint and a Docker `HEALTHCHECK`.
- Publishes multi-arch images (amd64, arm64) to `ghcr.io/zetawars/decypharr-fallback`.
