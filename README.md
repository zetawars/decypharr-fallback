# decypharr-fallback

[![CI](https://github.com/zetawars/decypharr-fallback/actions/workflows/ci.yml/badge.svg)](https://github.com/zetawars/decypharr-fallback/actions/workflows/ci.yml)

Automatic fallback from [Decypharr](https://github.com/sirrobot01/decypharr) to a real qBittorrent for Radarr, Sonarr and other *arrs.

## The problem

When you use Decypharr (Real-Debrid, AllDebrid, TorBox…) as your *arr download client, some torrents never download:

- **Refused at add time.** `torrent blocked for legal reasons` (HTTP 451), `torrent not cached` (with uncached downloads off), or Decypharr is down.
- **Failed later.** An uncached torrent is accepted, then fails on the debrid side. The *arr only shows "qBittorrent is reporting an error" and waits forever.

Radarr and Sonarr don't retry a refused release with your next download client. They try the next *release* on the same client. Your lower-priority qBittorrent only gets used after Decypharr has been failing for several minutes. In practice, releases your debrid blocks, often regional content like Indian films, never download.

## What this does

A small qBittorrent-API proxy that sits between your *arrs and Decypharr:

```
Radarr / Sonarr ──► decypharr-fallback ──► Decypharr      (always tried first)
                                       └─► qBittorrent    (when Decypharr refuses or fails a torrent)
```

- **Everything passes through to Decypharr.** Logins, categories and adds go to Decypharr unchanged, so debrid stays your primary source.
- **Refused at add time:** the same torrent (magnet or .torrent file) is sent to qBittorrent instead.
- **Failed later:** when Decypharr reports a torrent the *arr was tracking as `error`, it's restarted on qBittorrent from its hash, and the failed Decypharr entry is removed. The *arr keeps tracking it under the same download ID.
- **The *arr sees one list.** `torrents/info` merges both backends, so the *arr tracks and imports qBittorrent items like any other download. Requests naming a hash that lives in qBittorrent (delete, properties, files…) are routed there.
- **Fallback items are kept separate.** They use the qBittorrent category `<category>-fallback` (for example `radarr-fallback`), created automatically with the same save path as `radarr`. If qBittorrent is also configured as its own download client in the *arr, it won't see them twice.
- **Wrong credentials are not a fallback.** A 401/403 from Decypharr goes straight back to the *arr, so a misconfiguration shows up as a failed test instead of silently using qBittorrent.

It's a single Python file using only the standard library, with no dependencies.

## Setup

1. Add the service to your compose file. It must be able to reach both Decypharr and qBittorrent:

   ```yaml
   services:
     decypharr-fallback:
       image: ghcr.io/zetawars/decypharr-fallback:latest
       container_name: decypharr-fallback
       environment:
         DECYPHARR_URL: http://decypharr:8282
         QBIT_URL: http://qbittorrent:8080      # or http://gluetun:8080 if qBittorrent runs inside gluetun
         QBIT_USERNAME: ${QBIT_USERNAME}
         QBIT_PASSWORD: ${QBIT_PASSWORD}
       restart: unless-stopped
   ```

   Images are built for `linux/amd64` and `linux/arm64`. Pin a version with `:1`, `:1.0` or `:1.0.0` if you prefer.

2. In each *arr, open **Settings → Download Clients**, edit your Decypharr client and change:
   - **Host:** `decypharr-fallback`
   - **Port:** `8283`

   Keep the username, password and category exactly as Decypharr needs them. Click **Test**, then **Save**.

3. qBittorrent must save into a path the *arr can see, the same as for any qBittorrent client.

### Environment variables

| Variable | Default | Description |
|---|---|---|
| `DECYPHARR_URL` | `http://decypharr:8282` | Decypharr base URL |
| `QBIT_URL` | `http://qbittorrent:8080` | qBittorrent WebUI URL (`http://gluetun:8080` if it runs inside gluetun) |
| `QBIT_USERNAME` | — (required) | qBittorrent WebUI username |
| `QBIT_PASSWORD` | — (required) | qBittorrent WebUI password |
| `PORT` | `8283` | Port this service listens on |

## Health

`GET /health` returns `200` while the service runs, and reports whether each backend answers:

```json
{"decypharr": true, "qbittorrent": true}
```

The image has a Docker `HEALTHCHECK` built on it.

## Logs

```
WARNING Decypharr refused add (451): {"error": "... torrent blocked for legal reasons", ...} -> falling back to qBittorrent
INFO created qBittorrent category {'category': 'radarr-fallback', 'savePath': '/data/torrents/movies'}
INFO added to qBittorrent
WARNING Decypharr reports Some.Movie.2010.1080p... as failed -> restarted on qBittorrent
```

## Limitations

- A failed Decypharr torrent is only moved if this service saw it working first, so leftover broken entries are never resurrected. That state is kept in memory: a torrent that fails while the service is down stays in Decypharr's error state.
- Torrents restarted after a debrid failure are added by hash (magnet), so qBittorrent fetches the metadata from DHT/peers first.
- Fallback torrents seed according to your qBittorrent settings.
- Tested with Radarr v6, Sonarr v4, Whisparr v3 and Decypharr 2.7.

## Development

```
python -m unittest discover -s tests -v
```

The tests run the real service between a fake Decypharr and a fake qBittorrent.

## License

[MIT](LICENSE)
