#!/usr/bin/env python3
"""
Batch car travel times from ONE origin to a list of destinations, written to a
single self-contained results page (results.html) that you can host on a website.

On the page, each route is a row you can click to expand and see its map inline.
Also writes summary.csv.

Needs travel_time_map.py in the same folder (for the shared lookup helpers).

Setup:  pip install requests
Usage:  python batch_travel_maps.py
        python batch_travel_maps.py "Wageningen, Netherlands"
        python batch_travel_maps.py "Wageningen" -f my_places.txt -d site_output

Edit DESTINATIONS below, or pass -f with a text file (one destination per line,
lines starting with # are ignored).

To publish: upload results.html to your website. No server-side code is needed.
To preview locally, leave the built-in viewer on (it serves the file over
http://127.0.0.1 so the OSM tile servers receive a Referer header).
"""

import argparse
import csv
import functools
import html
import http.server
import json
import sys
import time
import webbrowser
from pathlib import Path

import requests

from travel_time_map import HEADERS, OSRM_URL, QuietHandler, format_duration, geocode

# ---- Edit this list ---------------------------------------------------------
DESTINATIONS = [
    "Amsterdam Centraal, Netherlands",
    "Utrecht Centraal, Netherlands",
    "Rotterdam Centraal, Netherlands",
    "Arnhem Centraal, Netherlands",
    "Eindhoven Centraal, Netherlands",
]
# -----------------------------------------------------------------------------

PAUSE_SECONDS = 1.1  # Nominatim allows at most ~1 request per second

PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<meta name="referrer" content="strict-origin-when-cross-origin">
<title>Routes from __TITLE__</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css">
<style>
  :root {
    --bg: #f6f7f9; --card: #ffffff; --text: #1c1f24; --muted: #667085;
    --line: #e4e7ec; --hover: #f0f3f8; --accent: #2563eb;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #14161a; --card: #1d2025; --text: #e8eaed; --muted: #9aa3af;
      --line: #2c3037; --hover: #252932; --accent: #6ea0ff;
    }
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--bg); color: var(--text);
    font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; line-height: 1.45;
  }
  main { max-width: 760px; margin: 0 auto; padding: 2rem 1rem 3rem; }
  h1 { font-size: 1.5rem; margin: 0 0 .25rem; }
  .sub { color: var(--muted); margin: 0 0 1.25rem; font-size: .95rem; }
  .toolbar { display: flex; justify-content: flex-end; margin-bottom: .5rem; }
  .toolbar button {
    background: none; border: 0; color: var(--accent); cursor: pointer;
    font: inherit; font-size: .9rem; padding: .25rem .5rem;
  }
  .list { background: var(--card); border: 1px solid var(--line); border-radius: 10px; overflow: hidden; }
  .item + .item { border-top: 1px solid var(--line); }
  .head {
    display: grid; grid-template-columns: 1fr auto auto 1.25rem; gap: .75rem; align-items: center;
    width: 100%; padding: .85rem 1rem; background: none; border: 0; color: inherit;
    font: inherit; text-align: left; cursor: pointer;
  }
  .head:hover { background: var(--hover); }
  .head:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
  .dest { font-weight: 600; }
  .time { font-weight: 600; font-variant-numeric: tabular-nums; }
  .dist { color: var(--muted); font-variant-numeric: tabular-nums; font-size: .9rem; }
  .chev { color: var(--muted); transition: transform .15s ease; }
  .item.open .chev { transform: rotate(90deg); }
  .panel { padding: 0 1rem 1rem; }
  .map { height: 380px; border-radius: 8px; border: 1px solid var(--line); }
  footer { color: var(--muted); font-size: .8rem; margin-top: 1rem; }
  @media (max-width: 520px) {
    .head { grid-template-columns: 1fr auto 1.25rem; }
    .dist { display: none; }
    .map { height: 300px; }
  }
</style>
</head>
<body>
<main>
  <h1>Routes from __TITLE__</h1>
  <p class="sub" id="sub"></p>
  <div class="toolbar"><button type="button" id="toggle-all">Expand all</button></div>
  <div class="list" id="list"></div>
  <footer>
    Typical driving times, without live traffic. Map data &copy; OpenStreetMap contributors;
    routing by OSRM.
  </footer>
</main>

<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>
<script>
  const DATA = __DATA__;
  const list = document.getElementById('list');
  const items = [];

  document.getElementById('sub').textContent =
    DATA.routes.length + ' destinations, sorted by travel time. Click a row to see its map.';

  function popupText(label, name) {
    const el = document.createElement('div');
    el.textContent = label + ': ' + name;
    return el;
  }

  function initMap(el, route) {
    const map = L.map(el);
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19,
      attribution: '&copy; OpenStreetMap contributors'
    }).addTo(map);
    const line = L.polyline(route.path, { color: '#2563eb', weight: 5, opacity: 0.85 }).addTo(map);
    L.circleMarker(route.start, { radius: 8, color: '#fff', weight: 2, fillColor: '#16a34a', fillOpacity: 1 })
      .addTo(map).bindPopup(popupText('Start', DATA.origin));
    L.circleMarker(route.end, { radius: 8, color: '#fff', weight: 2, fillColor: '#dc2626', fillOpacity: 1 })
      .addTo(map).bindPopup(popupText('End', route.destination));
    map.fitBounds(line.getBounds(), { padding: [30, 30] });
    return map;
  }

  DATA.routes.forEach(function (route, i) {
    const item = document.createElement('div');
    item.className = 'item';

    const head = document.createElement('button');
    head.type = 'button';
    head.className = 'head';
    head.setAttribute('aria-expanded', 'false');
    head.setAttribute('aria-controls', 'panel-' + i);

    const dest = document.createElement('span');
    dest.className = 'dest';
    dest.textContent = route.destination;
    const time = document.createElement('span');
    time.className = 'time';
    time.textContent = route.duration_text;
    const dist = document.createElement('span');
    dist.className = 'dist';
    dist.textContent = route.distance_km.toFixed(1) + ' km';
    const chev = document.createElement('span');
    chev.className = 'chev';
    chev.setAttribute('aria-hidden', 'true');
    chev.textContent = '\u25B6';
    head.append(dest, dist, time, chev);

    const panel = document.createElement('div');
    panel.className = 'panel';
    panel.id = 'panel-' + i;
    panel.hidden = true;
    const mapEl = document.createElement('div');
    mapEl.className = 'map';
    panel.appendChild(mapEl);

    let map = null;
    function setOpen(open) {
      head.setAttribute('aria-expanded', String(open));
      panel.hidden = !open;
      item.classList.toggle('open', open);
      if (open) {
        // Create the map lazily, once the panel is visible so Leaflet can measure it
        if (!map) { map = initMap(mapEl, route); }
        else { map.invalidateSize(); }
      }
    }
    head.addEventListener('click', function () {
      setOpen(head.getAttribute('aria-expanded') !== 'true');
    });

    item.append(head, panel);
    list.appendChild(item);
    items.push({ setOpen: setOpen, isOpen: function () { return !panel.hidden; } });
  });

  const toggleAll = document.getElementById('toggle-all');
  toggleAll.addEventListener('click', function () {
    const expand = toggleAll.textContent === 'Expand all';
    items.forEach(function (it) { it.setOpen(expand); });
    toggleAll.textContent = expand ? 'Collapse all' : 'Expand all';
  });
</script>
</body>
</html>
"""


def route_between(start: tuple, end: tuple) -> dict:
    """Driving route between two (lon, lat) points."""
    url = f"{OSRM_URL}/{start[0]},{start[1]};{end[0]},{end[1]}"
    r = requests.get(url, params={"overview": "full", "geometries": "geojson"},
                     headers=HEADERS, timeout=30)
    r.raise_for_status()
    data = r.json()
    if data.get("code") != "Ok" or not data.get("routes"):
        raise RuntimeError("no driving route found")
    route = data["routes"][0]
    return {
        "duration_seconds": int(route["duration"]),
        "distance_km": route["distance"] / 1000,
        # GeoJSON is [lon, lat]; Leaflet wants [lat, lon]. Round to keep the page small.
        "path": [[round(lat, 5), round(lon, 5)] for lon, lat in route["geometry"]["coordinates"]],
        "start": [round(start[1], 5), round(start[0], 5)],
        "end": [round(end[1], 5), round(end[0], 5)],
    }


def load_destinations(path: str | None) -> list[str]:
    if not path:
        return DESTINATIONS
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return [ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith("#")]


def write_page(rows: list[dict], origin: str, out_path: Path) -> None:
    ordered = sorted(rows, key=lambda r: r["duration_seconds"])
    data = {
        "origin": origin,
        "routes": [
            {
                "destination": r["destination"],
                "distance_km": round(r["distance_km"], 1),
                "duration_text": format_duration(r["duration_seconds"]),
                "path": r["path"],
                "start": r["start"],
                "end": r["end"],
            }
            for r in ordered
        ],
    }
    # Escape "</" so destination names can never close the <script> tag
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    page = PAGE_TEMPLATE.replace("__DATA__", payload).replace("__TITLE__", html.escape(origin))
    out_path.write_text(page, encoding="utf-8")


def write_csv(rows: list[dict], origin: str, out_path: Path) -> None:
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["origin", "destination", "distance_km", "travel_time_minutes"])
        for r in rows:
            w.writerow([origin, r["destination"], f'{r["distance_km"]:.1f}',
                        round(r["duration_seconds"] / 60)])


def serve_folder(out_dir: Path, filename: str, open_browser: bool) -> None:
    """Preview over http://127.0.0.1 so OSM tile servers get a Referer header."""
    handler = functools.partial(QuietHandler, directory=str(out_dir))
    with http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler) as httpd:
        url = f"http://127.0.0.1:{httpd.server_address[1]}/{filename}"
        print(f"\nPreview at: {url}")
        print("Press Ctrl+C to stop.")
        if open_browser:
            webbrowser.open(url)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.")


def main():
    parser = argparse.ArgumentParser(description="Results page with expandable route maps from one origin.")
    parser.add_argument("origin", nargs="?", help="Starting location (prompted for if omitted)")
    parser.add_argument("-f", "--file", help="Text file with destinations, one per line")
    parser.add_argument("-d", "--dir", default="route_maps", help="Output folder (default: route_maps)")
    parser.add_argument("-o", "--output", default="results.html", help="Results page filename")
    parser.add_argument("--no-serve", action="store_true", help="Only write the files, no local preview")
    parser.add_argument("--no-open", action="store_true", help="Don't open the browser automatically")
    args = parser.parse_args()

    origin = args.origin or input("Starting location: ").strip()
    if not origin:
        sys.exit("Error: no starting location given.")

    destinations = load_destinations(args.file)
    if not destinations:
        sys.exit("Error: the destination list is empty.")

    out_dir = Path(args.dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        origin_coords = geocode(origin)  # look up the origin only once
    except (RuntimeError, requests.RequestException) as e:
        sys.exit(f"Error: {e}")

    rows, failed = [], []
    for i, dest in enumerate(destinations, start=1):
        print(f"[{i}/{len(destinations)}] {origin} -> {dest} ... ", end="", flush=True)
        try:
            time.sleep(PAUSE_SECONDS)  # be polite to the free geocoder
            route = route_between(origin_coords, geocode(dest))
        except (RuntimeError, requests.RequestException) as e:
            print(f"FAILED ({e})")
            failed.append(dest)
            continue
        rows.append({"destination": dest, **route})
        print(f"{route['distance_km']:.1f} km, {format_duration(route['duration_seconds'])}")

    if not rows:
        sys.exit("No routes could be calculated.")

    page_path = out_dir / args.output
    write_page(rows, origin, page_path)
    write_csv(rows, origin, out_dir / "summary.csv")

    print(f"\nSaved {len(rows)} route(s) to: {page_path}")
    if failed:
        print(f"Skipped {len(failed)} destination(s): {', '.join(failed)}")

    if not args.no_serve:
        serve_folder(out_dir, args.output, open_browser=not args.no_open)


if __name__ == "__main__":
    main()