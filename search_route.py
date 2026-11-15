#!/usr/bin/env python3
"""
Free car travel time between two locations, plus an interactive route map.

Uses OpenStreetMap data (no API key needed):
  - Nominatim: place name -> coordinates
  - OSRM public server: driving route
  - Leaflet: map rendering (loaded in the browser from a CDN)

Note: no live traffic, so this is the typical driving time.

Setup:  pip install requests
Usage:  python travel_time_map.py "Wageningen, Netherlands" "Amsterdam Centraal"
        python travel_time_map.py "Wageningen" "Utrecht" -o utrecht.html

The map is served from a small local web server (Ctrl+C to stop). This is needed
because OSM's tile servers reject requests that have no Referer header, which is
what happens if you open the HTML file directly from disk.
"""

import argparse
import functools
import html
import http.server
import json
import sys
import webbrowser
from pathlib import Path

import requests

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OSRM_URL = "https://router.project-osrm.org/route/v1/driving"
HEADERS = {"User-Agent": "travel-time-script/1.0 (personal use)"}


def geocode(place: str) -> tuple[float, float]:
    """Return (longitude, latitude) for a place name or address."""
    params = {"q": place, "format": "json", "limit": 1}
    r = requests.get(NOMINATIM_URL, params=params, headers=HEADERS, timeout=15)
    r.raise_for_status()
    results = r.json()
    if not results:
        raise RuntimeError(f"Could not find location: {place!r}")
    return float(results[0]["lon"]), float(results[0]["lat"])


def get_route(origin: str, destination: str) -> dict:
    lon1, lat1 = geocode(origin)
    lon2, lat2 = geocode(destination)

    url = f"{OSRM_URL}/{lon1},{lat1};{lon2},{lat2}"
    params = {"overview": "full", "geometries": "geojson"}
    r = requests.get(url, params=params, headers=HEADERS, timeout=30)
    r.raise_for_status()
    data = r.json()
    if data.get("code") != "Ok" or not data.get("routes"):
        raise RuntimeError("No driving route found between these locations.")

    route = data["routes"][0]
    return {
        "duration_seconds": int(route["duration"]),
        "distance_meters": route["distance"],
        # GeoJSON coordinates are [lon, lat]; Leaflet wants [lat, lon]
        "path": [[lat, lon] for lon, lat in route["geometry"]["coordinates"]],
        "start": [lat1, lon1],
        "end": [lat2, lon2],
    }


def format_duration(seconds: int) -> str:
    hours, rem = divmod(seconds, 3600)
    minutes = rem // 60
    return f"{hours} h {minutes} min" if hours else f"{minutes} min"


MAP_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="strict-origin-when-cross-origin">
<title>Route: __ORIGIN__ to __DEST__</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css">
<style>
  html, body { height: 100%; margin: 0; font-family: system-ui, sans-serif; }
  #map { height: 100%; }
  .info {
    position: absolute; top: 12px; right: 12px; z-index: 1000;
    background: #fff; color: #222; padding: 12px 16px; border-radius: 8px;
    box-shadow: 0 2px 10px rgba(0,0,0,.3); max-width: 280px; line-height: 1.4;
  }
  .info b { font-size: 1.15em; }
  .info small { color: #666; }
</style>
</head>
<body>
<div id="map"></div>
<div class="info">
  <div><b>__DURATION__</b> by car</div>
  <div>__DISTANCE__ km</div>
  <small>__ORIGIN__ &rarr; __DEST__<br>Typical time, no live traffic</small>
</div>
<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>
<script>
  const path = __PATH__;
  const start = __START__;
  const end = __END__;

  const map = L.map('map');
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19,
    attribution: '&copy; OpenStreetMap contributors'
  }).addTo(map);

  const line = L.polyline(path, { color: '#2563eb', weight: 5, opacity: 0.85 }).addTo(map);
  L.marker(start).addTo(map).bindPopup('Start: __ORIGIN_JS__');
  L.marker(end).addTo(map).bindPopup('End: __DEST_JS__');
  map.fitBounds(line.getBounds(), { padding: [40, 40] });
</script>
</body>
</html>
"""


def build_map(route: dict, origin: str, destination: str, out_path: Path) -> None:
    page = (
        MAP_TEMPLATE
        .replace("__ORIGIN_JS__", html.escape(origin, quote=True).replace("'", "\\'"))
        .replace("__DEST_JS__", html.escape(destination, quote=True).replace("'", "\\'"))
        .replace("__ORIGIN__", html.escape(origin))
        .replace("__DEST__", html.escape(destination))
        .replace("__DURATION__", format_duration(route["duration_seconds"]))
        .replace("__DISTANCE__", f"{route['distance_meters'] / 1000:.1f}")
        .replace("__PATH__", json.dumps(route["path"]))
        .replace("__START__", json.dumps(route["start"]))
        .replace("__END__", json.dumps(route["end"]))
    )
    out_path.write_text(page, encoding="utf-8")


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # keep the terminal clean


def serve_map(out_path: Path, open_browser: bool) -> None:
    """Serve the map over http://127.0.0.1 so the browser sends a Referer to the tile server.

    OSM's tile servers block requests without a Referer, which is what happens
    when an HTML file is opened directly from disk (file://).
    """
    handler = functools.partial(QuietHandler, directory=str(out_path.parent))
    with http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler) as httpd:  # port 0 = any free port
        url = f"http://127.0.0.1:{httpd.server_address[1]}/{out_path.name}"
        print(f"Serving map at: {url}")
        print("Press Ctrl+C to stop.")
        if open_browser:
            webbrowser.open(url)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.")


def main():
    parser = argparse.ArgumentParser(description="Car travel time + route map between two locations.")
    parser.add_argument("origin", help="Start address or place name")
    parser.add_argument("destination", help="End address or place name")
    parser.add_argument("-o", "--output", default="route_map.html", help="Output HTML file")
    parser.add_argument("--no-open", action="store_true", help="Don't open the map in a browser")
    parser.add_argument("--no-serve", action="store_true",
                        help="Only save the HTML file (map tiles won't load if opened directly from disk)")
    args = parser.parse_args()

    try:
        route = get_route(args.origin, args.destination)
    except (RuntimeError, requests.RequestException) as e:
        sys.exit(f"Error: {e}")

    print(f"From:     {args.origin}")
    print(f"To:       {args.destination}")
    print(f"Distance: {route['distance_meters'] / 1000:.1f} km")
    print(f"Travel time (no traffic): {format_duration(route['duration_seconds'])}")

    out_path = Path(args.output).resolve()
    build_map(route, args.origin, args.destination, out_path)
    print(f"Map saved to: {out_path}")

    if not args.no_serve:
        serve_map(out_path, open_browser=not args.no_open)


if __name__ == "__main__":
    main()