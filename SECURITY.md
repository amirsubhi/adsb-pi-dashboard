# Security

## What the dashboard is designed for

ADS-B Pi Dashboard runs on a receiver at home and is meant to be opened from
devices on the same network. **It has no login.** Anyone who can reach its
port can see everything it shows, including your receiver's position.

- Don't port-forward it or otherwise expose it to the internet. For access
  from outside, use a VPN such as Tailscale or WireGuard.
- To limit it to the Pi itself (for example behind your own reverse proxy with
  authentication), set `bind = 127.0.0.1` in `settings.ini`.

## What it does to protect you

- **Read-only.** No page or API endpoint changes anything on the Pi. Settings
  are changed by editing `settings.ini` over SSH, because a settings page
  without a login could be used by any device on the network, or by a
  website open in your browser (cross-site request forgery).
- **Other websites can't read the API.** No `Access-Control-Allow-Origin`
  header is sent unless you set `cors_origin` to one specific origin.
- **Content Security Policy:** scripts only from the Pi itself, no inline
  scripts, no framing by other sites, no referrer sent (except on map tile
  requests, which tile servers require). Images from other sites are allowed
  only from the map tile provider you chose.
- **Escaping:** text that comes from the receiver, a feeder or the system
  journal is escaped before it's shown, so a spoofed callsign can't inject
  markup.
- **Input validation:** query parameters are checked and capped; bad values
  get a 400 error. Each connection has a timeout.
- **Fixed file list:** the server only serves a fixed list of files, so no
  request path can reach other files on the Pi.
- **Location rounding:** the receiver position is shown to about 1 km unless
  you turn on `show_exact_location`.
- **Sandboxed service:** the systemd unit runs as your user with
  `ProtectSystem=strict` (only its data folder is writable),
  `NoNewPrivileges`, `PrivateTmp` and related options.
- **No third-party scripts at runtime:** Leaflet and the map data are bundled.
  Their versions and SHA-256 checksums are in `THIRD_PARTY_NOTICES.md`.

## Privacy

The only requests that leave your network are the live map's street-map
tiles (OpenStreetMap by default, or CARTO). The provider sees your public IP
address and the area of the map you view. Set `map_tiles = off` to stop them;
the map then uses only the bundled outline.

## Reporting a problem

If you find a security issue, please report it privately through GitHub's
**Security → Report a vulnerability** on this repository rather than in a
public issue. Include what you found, how to reproduce it, and the version
(shown on the Settings page). If that option isn't available, open an issue that
only asks for a private contact, without the details.
