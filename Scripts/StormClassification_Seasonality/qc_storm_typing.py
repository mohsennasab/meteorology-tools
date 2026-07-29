#!/usr/bin/env python3
"""
Step 3: Quality control of the TC / NT classification.

Two layers of QC, run together:

1. Programmatic checks. Every event is screened and flagged, and the
   results go to qc_report.csv and qc_summary.txt. Flags:

     multi_tc          TC event within matching range of more than one storm.
                       Review which storm actually caused the rain.
     padding_only      TC event that matches only through the one-day padding,
                       not the storm's actual passage time near the watershed.
     distant_track     TC event whose matched passage is farther than
                       DISTANT_TRACK_KM from the maximum-precipitation location.
                       Because the classifier already requires a fix within
                       that radius, this flag should stay empty and acts as a
                       consistency check on the spatial match.
     borderline_nt     NT event whose window comes within
                       BORDERLINE_THRESHOLD_HOURS of a nearby storm's passage.
                       Close to matching; verify it is really non-tropical.
     fallback_source   Event filled from ranked-storms.csv instead of a
                       STAC item in step 1.

   The script also verifies internal consistency: TC + NT = total, event
   windows are valid, matched storm ids exist in the shapefile, and event
   ids are unique.

2. Visual QC maps. One PNG per TC event and per borderline NT event,
   showing the storm track (colored by status), the transposition domain and
   watershed outlines for reference, the event's maximum-precipitation point,
   and a timeline of the temporal overlap. File names sort by rank so they can
   be reviewed alongside the StormHub thumbnails:

     RANK###_TC_<name>_<date>.png
     RANK###_TC_MULTI_<names>_<date>.png
     RANK###_BORDERLINE_NT_<date>.png

Inputs : output/classified_storms.csv      (from classify_storms.py)
         input/IBTrACS_*.shp + .dbf        (same full file used in step 2)
         input/transposition_domain.geojson (optional, for map outline)
         input/watershed.geojson            (optional, for map outline)
         input/basemap/land.geojson         (optional, faded basemap)
         input/basemap/states.geojson       (optional, faded basemap)
Outputs: output/qc/qc_report.csv
         output/qc/qc_summary.txt
         output/qc/maps/RANK###_*.png

Usage:
    python qc_storm_typing.py
    python qc_storm_typing.py --no-maps          # programmatic checks only
    python qc_storm_typing.py --distance-km 300

Author: Mohsen Tahmasebi Nasab
AECOM FFRD Upper Tennessee Validation Basin Study
"""

import argparse
import csv
import json
import math
import sys
from datetime import datetime, timedelta
from pathlib import Path

from classify_storms import load_ibtracs, summarize_storm

# ===========================================================================
# USER SETTINGS
# Edit these for your project, or override them on the command line.
# ===========================================================================

# Classified catalog from step 2.
CLASSIFIED_CSV = r"output\classified_storms.csv"

# The same full IBTrACS shapefile used by classify_storms.py.
IBTRACS_SHP = r"input\IBTrACS_since1980_list_v4r01.shp"

# Transposition domain and watershed polygons (GeoJSON). Both are optional
# and only drawn on the maps. Leave blank to skip them.
DOMAIN_GEOJSON = r"input\transposition_domain.geojson"
WATERSHED_GEOJSON = r"input\watershed.geojson"

# Folder holding land.geojson and states.geojson for a faded basemap under the
# map panel. Optional. Leave blank to skip the basemap. The included files are
# Natural Earth 1:50m data: global land (continuous, so it never cuts off at
# the frame edge) and North American state boundaries.
BASEMAP_DIR = r"input\basemap"

# Fixed map extent (lon_min, lon_max, lat_min, lat_max), or None to auto-fit.
# None is the default so each map shows the entire storm track, the watershed,
# and the transposition domain. Set explicit numbers if you would rather frame
# every map the same way for a given basin.
MAP_EXTENT = None

# Output directory.
OUTPUT_DIR = r"output\qc"

# Must match the value used in classify_storms.py.
TEMPORAL_WINDOW_DAYS = 1

# NT events whose window comes within this many hours of a track span are
# flagged borderline and get a map.
BORDERLINE_THRESHOLD_HOURS = 48

# Matching radius in km. This must equal MATCH_RADIUS_KM in classify_storms.py.
# The QC uses it both to rebuild the spatiotemporal matches and to flag any
# distant_track outliers (which should not occur when the two values agree).
# 500 km follows the TC rainfall attribution radius of Khouakhi et al. (2017);
# Kunkel et al. (2010) used a similar 5-degree (about 500 km) search radius.
DISTANT_TRACK_KM = 500.0

# ===========================================================================
# END OF USER SETTINGS
# ===========================================================================


STATUS_COLORS = {
    "HU": "#8c1a10", "TS": "#c44536", "TD": "#e0863d",
    "SS": "#c98a2d", "SD": "#c9a86a",
    "EX": "#3d6cb3", "PT": "#5b7a99", "LO": "#8a8a8a",
    "DB": "#b0b0b0", "WV": "#b0b0b0",
}
STATUS_LABELS = {
    "HU": "Hurricane", "TS": "Tropical storm", "TD": "Tropical depression",
    "SS": "Subtropical storm", "SD": "Subtropical depression",
    "EX": "Extratropical", "PT": "Post-tropical", "LO": "Remnant low",
    "DB": "Disturbance", "WV": "Tropical wave",
}

# Highlighter colors for the first and last track fix.
TRACK_START_COLOR = "#39ff14"   # highlighter green
TRACK_END_COLOR = "#ff1f1f"     # highlighter red


# ---------------------------------------------------------------------------
# Basics
# ---------------------------------------------------------------------------
def haversine_km(lat1, lon1, lat2, lon2):
    """Great-circle distance in kilometers."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def parse_dt(value):
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value.strip(), fmt)
        except ValueError:
            continue
    return datetime.strptime(value.strip()[:10], "%Y-%m-%d")


def load_events(csv_path):
    events = []
    with open(csv_path) as f:
        for row in csv.DictReader(f):
            row["_start"] = parse_dt(row["start_datetime"])
            row["_end"] = parse_dt(row["end_datetime"])
            events.append(row)
    return events


def load_polygon(geojson_path):
    """Return the outer ring(s) of the first feature as [[(lon, lat), ...]]."""
    path = Path(geojson_path) if geojson_path else None
    if not path or not path.exists():
        return None
    with open(path) as f:
        gj = json.load(f)
    feature = gj["features"][0] if "features" in gj else gj
    geom = feature["geometry"]
    if geom["type"] == "Polygon":
        return [geom["coordinates"][0]]
    if geom["type"] == "MultiPolygon":
        return [poly[0] for poly in geom["coordinates"]]
    return None


def _geojson_shapes(path, want_polygons):
    """Coordinate lists from a GeoJSON file. Polygon exterior rings when
    want_polygons is True, otherwise line coordinate sequences."""
    if not path.exists():
        return []
    with open(path) as f:
        gj = json.load(f)
    out = []
    for ft in gj.get("features", [gj]):
        geom = ft.get("geometry", ft)
        gtype, coords = geom["type"], geom["coordinates"]
        if want_polygons:
            if gtype == "Polygon":
                out.append(coords[0])
            elif gtype == "MultiPolygon":
                out.extend(poly[0] for poly in coords)
        else:
            if gtype == "LineString":
                out.append(coords)
            elif gtype == "MultiLineString":
                out.extend(coords)
    return out


def load_basemap(basemap_dir):
    """Load faded-basemap geometry. Returns (land_rings, state_lines), each a
    list of coordinate sequences, or ([], []) when the files are absent."""
    d = Path(basemap_dir) if basemap_dir else None
    if not d or not d.exists():
        return [], []
    land = _geojson_shapes(d / "land.geojson", want_polygons=True)
    states = _geojson_shapes(d / "states.geojson", want_polygons=False)
    return land, states


def track_points(records):
    """(timestamp, lat, lon, status) for each parseable track point, sorted."""
    pts = []
    for r in records:
        try:
            ts = datetime.strptime(str(r["ISO_TIME"]), "%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError, KeyError):
            continue
        try:
            lat, lon = float(r["LAT"]), float(r["LON"])
        except (ValueError, TypeError, KeyError):
            continue
        pts.append((ts, lat, lon, str(r.get("USA_STATUS", "")).strip()))
    return sorted(pts, key=lambda p: p[0])


def gap_hours(event_start, event_end, span_start, span_end):
    """Hours between two intervals; 0 when they overlap."""
    if event_start <= span_end and event_end >= span_start:
        return 0.0
    if event_end < span_start:
        return (span_start - event_end).total_seconds() / 3600
    return (event_start - span_end).total_seconds() / 3600


# ---------------------------------------------------------------------------
# Programmatic QC
# ---------------------------------------------------------------------------
def build_track_index(storms):
    """Per-storm metadata used by both the checks and the maps."""
    index = {}
    for sid, records in storms.items():
        pts = track_points(records)
        if not pts:
            continue
        lats = [p[1] for p in pts]
        lons = [p[2] for p in pts]
        index[sid] = {
            "sid": sid,
            "info": summarize_storm(records),
            "points": pts,
            "span_start": pts[0][0],
            "span_end": pts[-1][0],
            "lat_min": min(lats), "lat_max": max(lats),
            "lon_min": min(lons), "lon_max": max(lons),
        }
    return index


def near_bbox(tr, plat, plon, radius_km):
    """Cheap reject: is the point plausibly within radius_km of the storm's
    bounding box? One degree is at least 111 km, so radius_km / 111 degrees is
    a safe margin. Skips the full haversine loop for storms on other continents."""
    margin = radius_km / 111.0 + 1.0
    return (tr["lat_min"] - margin <= plat <= tr["lat_max"] + margin
            and tr["lon_min"] - margin <= plon <= tr["lon_max"] + margin)


def consistency_checks(events, track_index):
    """Structural checks on the classified catalog. Returns report lines."""
    lines = []
    failures = 0

    def check(name, ok, detail):
        nonlocal failures
        lines.append(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")
        if not ok:
            failures += 1

    tc = [e for e in events if e["classification"] == "TC"]
    nt = [e for e in events if e["classification"] == "NT"]
    check("classification totals", len(tc) + len(nt) == len(events),
          f"{len(tc)} TC + {len(nt)} NT = {len(tc) + len(nt)} of {len(events)}")

    bad_windows = [e["event_id"] for e in events if e["_end"] <= e["_start"]]
    check("event windows", not bad_windows,
          "all end datetimes after start datetimes" if not bad_windows
          else f"invalid windows for events {bad_windows}")

    ids = [e["event_id"] for e in events]
    check("unique event ids", len(ids) == len(set(ids)),
          f"{len(set(ids))} unique ids for {len(ids)} events")

    unknown = sorted({e["matched_tc_sid"] for e in tc
                      if e["matched_tc_sid"] and e["matched_tc_sid"] not in track_index})
    check("matched storm ids exist in shapefile", not unknown,
          "all matched SIDs found" if not unknown else f"missing SIDs: {unknown}")

    dup_dates = sorted({e["start_datetime"] for e in events
                        if sum(1 for x in events
                               if x["start_datetime"] == e["start_datetime"]) > 1})
    check("no duplicate start datetimes", not dup_dates,
          "all start datetimes unique" if not dup_dates
          else f"duplicates: {dup_dates}")

    return lines, failures


def evaluate_event(ev, track_index, window_days, borderline_hours, radius_km):
    """Compute flags and QC metrics for one event, using the same
    spatiotemporal match as classify_storms.py (a track fix within radius_km of
    the rainfall maximum and inside the padded event window)."""
    pad = timedelta(days=window_days)
    flags = []

    result = {
        "event_id": ev["event_id"],
        "start_datetime": ev["start_datetime"],
        "classification": ev["classification"],
        "matched_tc_name": ev.get("matched_tc_name", ""),
        "overlapping_sids": [],
        "nearest_sid": None,
        "track_gap_hours": "",
        "min_track_distance_km": "",
        "flags": flags,
    }

    if ev.get("source", "").startswith("ranked-storms"):
        flags.append("fallback_source")

    try:
        plat = float(ev["max_precip_lat"])
        plon = float(ev["max_precip_lon"])
    except (ValueError, TypeError, KeyError):
        plat = plon = None

    if plat is None:
        # No rainfall maximum to test against (a fallback event). Nothing more
        # to flag beyond fallback_source.
        return result

    win_start, win_end = ev["_start"] - pad, ev["_end"] + pad

    # Storms with at least one fix inside the padded window and within the
    # matching radius of the rainfall maximum. These are the real matches.
    overlapping = []
    for sid, tr in track_index.items():
        if tr["span_start"] > win_end or tr["span_end"] < win_start:
            continue
        if not near_bbox(tr, plat, plon, radius_km):
            continue
        if any(win_start <= p[0] <= win_end
               and haversine_km(plat, plon, p[1], p[2]) <= radius_km
               for p in tr["points"]):
            overlapping.append(sid)
    result["overlapping_sids"] = overlapping

    if ev["classification"] == "TC":
        if len(overlapping) > 1:
            flags.append("multi_tc")

        # Gap between the event window and the matched storm's passage near the
        # rainfall maximum. Zero means direct overlap; positive means the match
        # came through the one-day padding only.
        sid = ev.get("matched_tc_sid", "")
        if sid in track_index:
            near = [p for p in track_index[sid]["points"]
                    if win_start <= p[0] <= win_end
                    and haversine_km(plat, plon, p[1], p[2]) <= radius_km]
            if near:
                pstart, pend = min(p[0] for p in near), max(p[0] for p in near)
                g = gap_hours(ev["_start"], ev["_end"], pstart, pend)
                result["track_gap_hours"] = round(g, 1)
                if g > 0:
                    flags.append("padding_only")
                dmin = min(haversine_km(plat, plon, p[1], p[2]) for p in near)
                result["min_track_distance_km"] = round(dmin, 0)
                if dmin > radius_km:
                    flags.append("distant_track")
    else:
        # NT: find the storm that came within radius of the rainfall maximum
        # with the smallest gap in time, to flag near misses for review.
        nearest_gap = float("inf")
        nearest_sid = None
        for sid, tr in track_index.items():
            if not near_bbox(tr, plat, plon, radius_km):
                continue
            near_times = [p[0] for p in tr["points"]
                          if haversine_km(plat, plon, p[1], p[2]) <= radius_km]
            if not near_times:
                continue
            g = gap_hours(ev["_start"], ev["_end"], min(near_times), max(near_times))
            if g < nearest_gap:
                nearest_gap, nearest_sid = g, sid
        if nearest_sid is not None:
            result["nearest_sid"] = nearest_sid
            result["track_gap_hours"] = round(nearest_gap, 1)
            if 0 < nearest_gap <= borderline_hours:
                flags.append("borderline_nt")

    return result


# ---------------------------------------------------------------------------
# Visual QC maps
# ---------------------------------------------------------------------------
def setup_matplotlib():
    try:
        import matplotlib
        matplotlib.use("Agg")
        from plot_style import apply_style
        apply_style()
        return True
    except ImportError:
        print("matplotlib not installed; skipping QC maps")
        return False


def draw_map_panel(ax, storms_to_plot, ev, domain_rings, watershed_rings,
                   basemap, extent):
    """Track map with a faded basemap, the domain, watershed, and max-precip
    location."""
    from matplotlib.lines import Line2D
    from matplotlib.patches import Polygon as MplPolygon, Patch

    # Faded mono basemap for geographic reference, drawn under everything.
    land_rings, state_lines = basemap
    for ring in land_rings:
        ax.add_patch(MplPolygon(ring, closed=True, facecolor="#eeece6",
                                edgecolor="#d3cec2", linewidth=0.5, zorder=0))
    for line in state_lines:
        ax.plot([c[0] for c in line], [c[1] for c in line],
                color="#dcd8cc", linewidth=0.5, zorder=0.5)

    handles = []
    if domain_rings:
        for ring in domain_rings:
            ax.add_patch(MplPolygon(ring, closed=True, linewidth=1.2,
                                    edgecolor="#3a5a40", facecolor="#dfe8df",
                                    alpha=0.5, zorder=1))
        handles.append(Patch(facecolor="#dfe8df", edgecolor="#3a5a40",
                             label="Transposition domain"))
    if watershed_rings:
        for ring in watershed_rings:
            ax.add_patch(MplPolygon(ring, closed=True, linewidth=1.0,
                                    edgecolor="#1d3557", facecolor="#c7d8e8",
                                    alpha=0.6, zorder=2))
        handles.append(Patch(facecolor="#c7d8e8", edgecolor="#1d3557",
                             label="Watershed"))

    statuses_seen = set()
    xs, ys = [], []
    for sid, tr in storms_to_plot.items():
        pts = tr["points"]
        lats = [p[1] for p in pts]
        lons = [p[2] for p in pts]
        xs += lons
        ys += lats

        for i in range(len(pts) - 1):
            color = STATUS_COLORS.get(pts[i][3], "#8a8a8a")
            ax.plot([lons[i], lons[i + 1]], [lats[i], lats[i + 1]],
                    color=color, linewidth=2.2, solid_capstyle="round", zorder=4)
        for i, (ts, lat, lon, status) in enumerate(pts):
            statuses_seen.add(status)
            ax.plot(lon, lat, "o", color=STATUS_COLORS.get(status, "#8a8a8a"),
                    markersize=5.5, markeredgecolor="white",
                    markeredgewidth=0.6, zorder=5)
            # Label once per day (00Z) to keep the map readable.
            if ts.hour == 0:
                ax.annotate(ts.strftime("%m/%d"), (lon, lat),
                            textcoords="offset points", xytext=(7, 5),
                            fontsize=6.5, color="#333333",
                            bbox=dict(boxstyle="round,pad=0.15",
                                      facecolor="white", edgecolor="#cccccc",
                                      alpha=0.85), zorder=6)

        ax.plot(lons[0], lats[0], "s", color=TRACK_START_COLOR, markersize=10,
                markeredgecolor="#1a1a1a", markeredgewidth=0.9, zorder=7)
        ax.plot(lons[-1], lats[-1], "D", color=TRACK_END_COLOR, markersize=10,
                markeredgecolor="#1a1a1a", markeredgewidth=0.9, zorder=7)

    # Event maximum-precipitation location, when the catalog has it
    try:
        plat, plon = float(ev["max_precip_lat"]), float(ev["max_precip_lon"])
        ax.plot(plon, plat, "X", color="#111111", markersize=11,
                markeredgecolor="white", markeredgewidth=1, zorder=8)
        handles.append(Line2D([0], [0], marker="X", color="none",
                              markerfacecolor="#111111", markersize=9,
                              label="Event max precipitation"))
        xs.append(plon)
        ys.append(plat)
    except (ValueError, TypeError, KeyError):
        pass

    for status in ("HU", "TS", "TD", "SS", "SD", "EX", "PT", "LO", "DB", "WV"):
        if status in statuses_seen:
            handles.append(Line2D([0], [0], marker="o", color="none",
                                  markerfacecolor=STATUS_COLORS[status],
                                  markersize=7,
                                  label=STATUS_LABELS[status]))
    handles.append(Line2D([0], [0], marker="s", color="none",
                          markerfacecolor=TRACK_START_COLOR,
                          markeredgecolor="#1a1a1a", markeredgewidth=0.7,
                          markersize=8, label="Track start"))
    handles.append(Line2D([0], [0], marker="D", color="none",
                          markerfacecolor=TRACK_END_COLOR,
                          markeredgecolor="#1a1a1a", markeredgewidth=0.7,
                          markersize=8, label="Track end"))

    # Extent. By default auto-fit so the whole track, the watershed, and the
    # transposition domain are all visible. Including the domain and watershed
    # rings guarantees both fit even when the track runs off to one side.
    if extent:
        ax.set_xlim(extent[0], extent[1])
        ax.set_ylim(extent[2], extent[3])
    else:
        for rings in (domain_rings, watershed_rings):
            if rings:
                for ring in rings:
                    xs += [pt[0] for pt in ring]
                    ys += [pt[1] for pt in ring]
        if xs:
            margin = 0.8
            ax.set_xlim(min(xs) - margin, max(xs) + margin)
            ax.set_ylim(min(ys) - margin, max(ys) + margin)
    # Keep an equal geographic aspect, but let the data limits expand to fill
    # the axes box so the map panel keeps the full width of the timeline panel
    # below it.
    ax.set_aspect("equal", adjustable="datalim")
    ax.grid(True, alpha=0.25, linestyle=":")
    ax.set_xlabel("Longitude (degrees)")
    ax.set_ylabel("Latitude (degrees)")
    # Legend in two horizontal rows above the map, between the title and the
    # plot, so it never covers a track. Five items per row.
    ax.legend(handles=handles, loc="lower center", bbox_to_anchor=(0.5, 1.0),
              ncol=5, fontsize=8, columnspacing=1.3, handletextpad=0.4,
              borderaxespad=0.3, frameon=True)


def draw_timeline_panel(ax, ev, storms_to_plot, window_days):
    """Horizontal bars showing the event window against each track span."""
    import matplotlib.dates as mdates
    from matplotlib.patches import Patch

    pad = timedelta(days=window_days)
    ax.barh(y=0.6, width=mdates.date2num(ev["_end"]) - mdates.date2num(ev["_start"]),
            left=mdates.date2num(ev["_start"]), height=0.22,
            color="#3d6cb3", alpha=0.55, edgecolor="#2b4d80", linewidth=1)
    dur_h = (ev["_end"] - ev["_start"]).total_seconds() / 3600
    ax.text(mdates.date2num(ev["_start"] + (ev["_end"] - ev["_start"]) / 2), 0.6,
            f"Event {ev['event_id']} ({dur_h:.0f} h)",
            ha="center", va="center", fontsize=8, color="#1d3050")

    y = 0.2
    for sid, tr in storms_to_plot.items():
        name = tr["info"]["name"]
        s0, s1 = tr["span_start"], tr["span_end"]
        ax.barh(y=y, width=mdates.date2num(s1 + pad) - mdates.date2num(s0 - pad),
                left=mdates.date2num(s0 - pad), height=0.16,
                color="#c44536", alpha=0.15, edgecolor="#a03328",
                linewidth=0.8, linestyle="--")
        ax.barh(y=y, width=max(mdates.date2num(s1) - mdates.date2num(s0), 0.02),
                left=mdates.date2num(s0), height=0.16,
                color="#c44536", alpha=0.7, edgecolor="#a03328", linewidth=1)
        ax.text(mdates.date2num(s0 + (s1 - s0) / 2), y - 0.14,
                f"{name} track", ha="center", va="top",
                fontsize=7.5, color="#7a251b")
        y -= 0.35

    ax.set_ylim(y + 0.05, 0.95)
    ax.set_yticks([])
    locator = mdates.AutoDateLocator(minticks=4, maxticks=9)
    ax.xaxis.set_major_locator(locator)
    ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
    ax.set_xlabel("Date (UTC)")
    ax.grid(True, axis="x", alpha=0.25, linestyle=":")
    ax.legend(handles=[
        Patch(color="#3d6cb3", alpha=0.55, label="Event window"),
        Patch(color="#c44536", alpha=0.7, label="Track span near precip"),
        Patch(color="#c44536", alpha=0.15,
              label=f"{window_days:g}-day matching allowance"),
    ], loc="upper right", fontsize=7)

    times = [ev["_start"], ev["_end"]]
    for tr in storms_to_plot.values():
        times += [tr["span_start"] - pad, tr["span_end"] + pad]
    ax.set_xlim(mdates.date2num(min(times) - timedelta(hours=12)),
                mdates.date2num(max(times) + timedelta(hours=12)))


def make_qc_map(ev, qc, track_index, domain_rings, watershed_rings,
                window_days, maps_dir, basemap, extent):
    """One two-panel QC figure for a TC or borderline NT event."""
    import matplotlib.pyplot as plt

    if ev["classification"] == "TC":
        storms_to_plot = {sid: track_index[sid] for sid in qc["overlapping_sids"]
                          if sid in track_index}
        names = sorted({tr["info"]["name"] for tr in storms_to_plot.values()})
        if len(storms_to_plot) > 1:
            fname = (f"RANK{int(ev['event_id']):03d}_TC_MULTI_"
                     f"{'_'.join(names)}_{ev['start_datetime'][:10]}.png")
            subtitle = f"TC event, multiple overlapping storms: {', '.join(names)}"
        else:
            fname = (f"RANK{int(ev['event_id']):03d}_TC_"
                     f"{names[0] if names else 'UNNAMED'}_{ev['start_datetime'][:10]}.png")
            subtitle = f"TC event matched to {names[0] if names else 'unnamed storm'}"
    else:
        sid = qc["nearest_sid"]
        if sid not in track_index:
            return None
        storms_to_plot = {sid: track_index[sid]}
        name = track_index[sid]["info"]["name"]
        fname = (f"RANK{int(ev['event_id']):03d}_BORDERLINE_NT_"
                 f"{ev['start_datetime'][:10]}.png")
        subtitle = (f"NT event, {qc['track_gap_hours']:.0f} h from {name}. "
                    f"Verify the non-tropical classification")

    if not storms_to_plot:
        return None

    fig = plt.figure(figsize=(13, 9.5))
    # Leave headroom at the top for the two-row legend and the title.
    gs = fig.add_gridspec(2, 1, height_ratios=[3, 1], hspace=0.28, top=0.82)
    ax_map = fig.add_subplot(gs[0])
    ax_time = fig.add_subplot(gs[1])

    draw_map_panel(ax_map, storms_to_plot, ev, domain_rings, watershed_rings,
                   basemap, extent)
    ax_map.set_title(
        f"Rank {int(ev['event_id'])} event, {ev['start_datetime'][:10]}, "
        f"basin mean {float(ev['precip_mean_in']):.2f} in, "
        f"max {float(ev['precip_max_in']):.2f} in\n{subtitle}",
        fontsize=11, pad=48)

    draw_timeline_panel(ax_time, ev, storms_to_plot, window_days)
    ax_time.set_title("Temporal overlap", fontsize=10)

    path = maps_dir / fname
    fig.savefig(path)
    plt.close(fig)
    return path


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------
def write_report_csv(qc_results, path):
    fields = ["event_id", "start_datetime", "classification", "matched_tc_name",
              "flags", "track_gap_hours", "min_track_distance_km"]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in qc_results:
            w.writerow({
                "event_id": r["event_id"],
                "start_datetime": r["start_datetime"],
                "classification": r["classification"],
                "matched_tc_name": r["matched_tc_name"],
                "flags": "|".join(r["flags"]),
                "track_gap_hours": r["track_gap_hours"],
                "min_track_distance_km": r["min_track_distance_km"],
            })


def write_summary(check_lines, n_failures, qc_results, events, args,
                  n_maps, path):
    flagged = [r for r in qc_results if r["flags"]]
    by_flag = {}
    for r in flagged:
        for fl in r["flags"]:
            by_flag.setdefault(fl, []).append(r)

    lines = []
    lines.append("=" * 78)
    lines.append("STORM CLASSIFICATION QC SUMMARY")
    lines.append("=" * 78)
    lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"Events checked: {len(events)}")
    lines.append("")
    lines.append("CONSISTENCY CHECKS")
    lines.append("-" * 78)
    lines.extend(check_lines)
    lines.append(f"Overall: {'all checks passed' if n_failures == 0 else f'{n_failures} check(s) FAILED'}")
    lines.append("")
    lines.append("PARAMETERS")
    lines.append("-" * 78)
    lines.append(f"Temporal window:       {args.window} day(s), matching classify_storms.py")
    lines.append(f"Borderline threshold:  {args.borderline_hours} h from a nearby storm's passage")
    lines.append(f"Matching radius:       {args.distance_km:.0f} km from the event max-precip point")
    lines.append("")
    lines.append("FLAGGED EVENTS")
    lines.append("-" * 78)
    if not flagged:
        lines.append("None. No events require review under the current thresholds.")
    for flag in ("multi_tc", "padding_only", "distant_track", "borderline_nt",
                 "fallback_source"):
        if flag not in by_flag:
            continue
        lines.append(f"\n{flag} ({len(by_flag[flag])} event(s)):")
        for r in by_flag[flag]:
            extra = []
            if r["track_gap_hours"] != "":
                extra.append(f"gap {r['track_gap_hours']} h")
            if r["min_track_distance_km"] != "":
                extra.append(f"closest track point {r['min_track_distance_km']:.0f} km")
            lines.append(f"  rank {r['event_id']:>4}  {r['start_datetime'][:10]}  "
                         f"{r['classification']}  {r['matched_tc_name'] or '-':16}  "
                         f"{', '.join(extra)}")
    lines.append("")
    lines.append("REVIEW GUIDANCE")
    lines.append("-" * 78)
    lines.append("1. multi_tc: two or more storms passed within range of the rainfall")
    lines.append("   maximum. Open the map and decide which storm produced the rain.")
    lines.append("2. distant_track: a consistency check. It should stay empty because")
    lines.append("   the classifier already requires a fix within the matching radius.")
    lines.append("   Any entry means the QC radius and classify_storms.py disagree.")
    lines.append("3. padding_only: the event window touches only the one-day matching")
    lines.append("   allowance. Confirm the rainfall timing against the track.")
    lines.append("4. borderline_nt: nearly matched. Confirm the event is non-tropical")
    lines.append("   against the StormHub thumbnail and the track timing.")
    lines.append("5. fallback_source: the catalog row did not come from a STAC item.")
    lines.append("   Verify the event date and statistics independently.")
    lines.append("")
    lines.append(f"QC maps written: {n_maps}")

    with open(path, "w") as f:
        f.write("\n".join(lines))
    return lines


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="QC checks and maps for the TC/NT classification.")
    parser.add_argument("--classified", default=CLASSIFIED_CSV,
                        help="Classified catalog CSV from step 2")
    parser.add_argument("--ibtracs", default=IBTRACS_SHP,
                        help="Clipped IBTrACS shapefile (same as step 2)")
    parser.add_argument("--domain", default=DOMAIN_GEOJSON,
                        help="Transposition domain GeoJSON for the maps")
    parser.add_argument("--watershed", default=WATERSHED_GEOJSON,
                        help="Watershed GeoJSON for the maps")
    parser.add_argument("--basemap-dir", default=BASEMAP_DIR,
                        help="Folder with land.geojson and states.geojson for the faded basemap")
    parser.add_argument("--output-dir", default=OUTPUT_DIR, help="Output directory")
    parser.add_argument("--window", type=float, default=TEMPORAL_WINDOW_DAYS,
                        help="Temporal window in days; must match classify_storms.py")
    parser.add_argument("--borderline-hours", type=float, default=BORDERLINE_THRESHOLD_HOURS,
                        help="Gap threshold for borderline NT flags")
    parser.add_argument("--distance-km", type=float, default=DISTANT_TRACK_KM,
                        help="Distance threshold for distant_track flags")
    parser.add_argument("--no-maps", action="store_true",
                        help="Run the programmatic checks only")
    args = parser.parse_args()

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("STORM CLASSIFICATION QC")
    print("=" * 60)

    def echo(msg):
        print(msg)

    storms = load_ibtracs(args.ibtracs, echo)
    track_index = build_track_index(storms)
    events = load_events(args.classified)
    print(f"Loaded {len(events)} classified events")

    # Programmatic checks
    check_lines, n_failures = consistency_checks(events, track_index)
    print("\nConsistency checks:")
    for line in check_lines:
        print(f"  {line}")

    qc_results = [evaluate_event(ev, track_index, args.window,
                                 args.borderline_hours, args.distance_km)
                  for ev in events]
    write_report_csv(qc_results, out_dir / "qc_report.csv")

    # Maps
    n_maps = 0
    if not args.no_maps and setup_matplotlib():
        maps_dir = out_dir / "maps"
        maps_dir.mkdir(exist_ok=True)
        domain_rings = load_polygon(args.domain)
        watershed_rings = load_polygon(args.watershed)
        basemap = load_basemap(args.basemap_dir)
        if domain_rings is None:
            print("\nNo domain polygon found. Maps will show tracks only")
        if not basemap[0]:
            print("No basemap files found. Maps will draw without a basemap")

        print("\nGenerating QC maps...")
        for ev, qc in zip(events, qc_results):
            wants_map = (ev["classification"] == "TC"
                         or "borderline_nt" in qc["flags"])
            if not wants_map:
                continue
            path = make_qc_map(ev, qc, track_index, domain_rings,
                               watershed_rings, args.window, maps_dir,
                               basemap, MAP_EXTENT)
            if path:
                n_maps += 1
        print(f"  {n_maps} maps written to {maps_dir}")

    summary = write_summary(check_lines, n_failures, qc_results, events,
                            args, n_maps, out_dir / "qc_summary.txt")
    flagged = [r for r in qc_results if r["flags"]]
    print(f"\n{len(flagged)} event(s) flagged for review "
          f"(see {out_dir / 'qc_summary.txt'})")
    for flag in ("multi_tc", "padding_only", "distant_track", "borderline_nt",
                 "fallback_source"):
        n = sum(1 for r in flagged if flag in r["flags"])
        if n:
            print(f"  {flag}: {n}")

    if n_failures:
        print(f"\nWARNING: {n_failures} consistency check(s) failed. "
              f"Review before using the classification.")
        sys.exit(1)


if __name__ == "__main__":
    main()
