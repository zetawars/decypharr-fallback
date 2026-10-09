# decypharr-fallback

Automatic fallback from [Decypharr](https://github.com/sirrobot01/decypharr) to a real qBittorrent for Radarr, Sonarr and other *arrs.

## The problem

When you use Decypharr (Real-Debrid, AllDebrid, TorBox…) as your *arr download client, some torrents get refused:

- `torrent blocked for legal reasons` (HTTP 451). The debrid service won't touch that hash.
- `torrent not cached`, if uncached downloads are off.
- Decypharr is down or restarting.

Radarr and Sonarr don't retry a refused release with your next download client. They treat it as a failed grab and try the next *release* on the same client. Your lower-priority qBittorrent only gets used after Decypharr has been failing for several minutes. In practice, releases the debrid blocks (often regional content, like Indian films) never download.

## What this does

A small qBittorrent-API proxy that sits between your *arrs and Decypharr:

```
Radarr / Sonarr ──► decypharr-fallback ──► Decypharr      (always tried first)
                                       └─► qBittorrent    (only when Decypharr refuses an add)
```

- **Everything passes through to Decypharr.** Logins, categories and adds go to Decypharr unchanged, so debrid stays your primary source.
- **When Decypharr refuses `torrents/add`**, the same torrent (magnet or .torrent file) is sent to qBittorrent with the category `<category>-fallback`, for example `radarr-fallback`. The category is created automatically, with the same save path as `radarr`.
- **`torrents/info` merges both.** qBittorrent fallback items are reported under the original category, so the *arr tracks and imports them like any other download.
- **Requests naming a hash** that lives in qBittorrent (delete, properties, files…) are routed to qBittorrent.
- **The `-fallback` category keeps them separate.** If you also have qBittorrent configured as its own download client in the *arr, it won't see the fallback items twice.

It's a single Python file using only the standard library, with no dependencies.

## Setup

1. Add the service to your compose file (see [`docker-compose.example.yml`](docker-compose.example.yml)). It must be able to reach both Decypharr and qBittorrent.
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

## Logs

```
WARNING Decypharr refused add (451): {"error": "... torrent blocked for legal reasons", ...} -> falling back to qBittorrent
INFO created qBittorrent category {'category': 'radarr-fallback', 'savePath': '/data/torrents/movies'}
INFO added to qBittorrent
```

## Limitations

- Only refusals **at add time** trigger the fallback. If Decypharr accepts a torrent and it fails later (for example, an uncached download that stalls on the debrid side), that's handled by the *arr's normal failed-download handling, not by this service.
- Fallback torrents seed according to your qBittorrent settings.
- Tested with Radarr v6, Sonarr v4 and Decypharr 2.7.

## License

[MIT](LICENSE)
