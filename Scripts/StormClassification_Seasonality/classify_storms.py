#!/usr/bin/env python3
"""
Step 2: Classify catalog storms as Tropical Cyclone (TC) or Non-Tropical (NT).

Each event in the storm catalog is checked against IBTrACS tropical cyclone
tracks. An event is TC when some IBTrACS storm has a track fix that is both
within MATCH_RADIUS_KM of the event's maximum-precipitation location and
inside the event window padded by TEMPORAL_WINDOW_DAYS on each side. Otherwise
the event is NT.

The event window comes straight from the catalog's start and end datetimes
(built from the StormHub STAC items in step 1), so no fixed duration is
assumed here.

Why the script does its own spatial match:
  - The distance test is the matching rule used in the TC rainfall literature.
    Khouakhi, Villarini and Vecchi (2017) count a day's rainfall as tropical
    cyclone rainfall when the storm center of circulation is within 500 km of
    the rain gauge during a window of plus or minus one day. Kunkel et al.
    (2010) associate a heavy daily precipitation event with a tropical cyclone
    when it falls on the same day within about 5 degrees (roughly 500 km) of a
    storm center, noting that most tropical cyclones are smaller than 500 km by
    the extent of their gale-force winds, so that radius is wide enough to
    catch the events they produce.
  - Because the spatial test is applied here, the IBTrACS input does not need
    to be clipped to any domain in GIS. The full since-1980 track file is the
    correct input, and storms that were never near the watershed at the event
    time simply fail the distance test.
  - Any storm in IBTrACS was tropical at some point in its life, since the
    archive only tracks systems that at least one agency called tropical. That
    is why a storm that passes the watershed as an extratropical, post-tropical,
    or remnant low still counts as TC. Its moisture is tropical in origin, and
    post-tropical remnants produce some of the largest floods in the eastern US
    (Smith et al. 2011; Evans et al. 2017).

Inputs : output/storm_catalog.csv          (from build_storm_catalog.py)
         input/IBTrACS_*.shp + .dbf        (full since-1980 track file)
Outputs: output/classified_storms.csv
         output/classification_summary.txt
         output/classification_metadata.json
         output/classification_log.txt

Usage:
    python classify_storms.py
    python classify_storms.py --catalog output/storm_catalog.csv --ibtracs input/my_tracks.shp

Author: Mohsen Tahmasebi Nasab
AECOM FFRD Upper Tennessee Validation Basin Study
"""

import argparse
import csv
import json
import math
import struct
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

# ===========================================================================
# USER SETTINGS
# Edit these for your project, or override them on the command line.
# ===========================================================================

# Storm catalog CSV from step 1.
CATALOG_CSV = r"output\storm_catalog.csv"

# IBTrACS since-1980 lines shapefile. This can be the full, unclipped file:
# the script does the spatial matching itself, so no GIS clip is needed. The
# track coordinates (LAT, LON) and the .dbf attributes are both read, so the
# .dbf must sit next to the .shp.
IBTRACS_SHP = r"input\IBTrACS_since1980_list_v4r01.shp"

# Output directory.
OUTPUT_DIR = r"output"

# Days of padding added before the start and after the end of the event
# window when testing for overlap with an IBTrACS track fix. One day is the
# common choice in the literature (Khouakhi et al. 2017 use plus or minus one
# day) and covers the 3-hourly IBTrACS resolution plus rain that arrives ahead
# of or behind the storm center.
TEMPORAL_WINDOW_DAYS = 1

# Matching radius in kilometers. A track fix must fall within this distance of
# the event's maximum-precipitation location to count. 500 km is the tropical
# cyclone rainfall attribution radius of Khouakhi et al. (2017); Kunkel et al.
# (2010) used a comparable 5-degree (about 500 km) search radius.
MATCH_RADIUS_KM = 500.0

# ===========================================================================
# END OF USER SETTINGS
# ===========================================================================


# Saffir-Simpson labels for the USA_SSHS field (IBTrACS column documentation).
SSHS_LABELS = {
    -5: "Unknown", -4: "Post-tropical", -3: "Disturbance",
    -2: "Subtropical", -1: "Tropical Depression", 0: "Tropical Storm",
    1: "Category 1", 2: "Category 2", 3: "Category 3",
    4: "Category 4", 5: "Category 5",
}

# NATURE and USA_STATUS codes that mean the system was tropical or
# subtropical at that track point (see IBTrACS documentation).
TROPICAL_NATURE = {"TS", "SS"}
TROPICAL_USA_STATUS = {"HU", "HR", "TS", "TD", "SD", "SS"}


# ---------------------------------------------------------------------------
# Geometry and time helpers
# ---------------------------------------------------------------------------
def haversine_km(lat1, lon1, lat2, lon2):
    """Great-circle distance in kilometers."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def gap_hours(event_start, event_end, span_start, span_end):
    """Hours between two intervals; 0 when they overlap."""
    if event_start <= span_end and event_end >= span_start:
        return 0.0
    if event_end < span_start:
        return (span_start - event_end).total_seconds() / 3600
    return (event_start - span_end).total_seconds() / 3600


def to_number(value, default):
    """Coerce a DBF field to a number. Different readers return different
    types (int, float, string, None), so normalize here."""
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


# ---------------------------------------------------------------------------
# DBF reading. Tries geopandas, then dbfread, then a small built-in parser,
# so the script runs even in a bare Python install.
# ---------------------------------------------------------------------------
def read_dbf_builtin(dbf_path):
    """Minimal dBase III reader. Returns a list of dicts (strings/numbers)."""
    records = []
    with open(dbf_path, "rb") as f:
        header = f.read(32)
        n_records = struct.unpack("<I", header[4:8])[0]
        header_size, record_size = struct.unpack("<HH", header[8:12])

        # Field descriptors: 32 bytes each, terminated by 0x0D
        fields = []
        n_fields = (header_size - 33) // 32
        for _ in range(n_fields):
            desc = f.read(32)
            name = desc[:11].split(b"\x00")[0].decode("ascii")
            ftype = desc[11:12].decode("ascii")
            flen = desc[16]
            fields.append((name, ftype, flen))

        f.seek(header_size)
        for _ in range(n_records):
            raw = f.read(record_size)
            if not raw or raw[0:1] == b"\x1a":
                break
            if raw[0:1] == b"*":      # deleted record
                continue
            rec = {}
            pos = 1
            for name, ftype, flen in fields:
                val = raw[pos:pos + flen].decode("latin-1").strip()
                pos += flen
                if ftype in ("N", "F") and val:
                    try:
                        val = float(val) if ("." in val or "e" in val.lower()) else int(val)
                    except ValueError:
                        pass
                rec[name] = val
            records.append(rec)
    return records


def load_ibtracs(shp_path, log):
    """Load the IBTrACS attribute table, grouped by storm id (SID)."""
    shp_path = Path(shp_path)
    dbf_path = shp_path.with_suffix(".dbf")
    if not dbf_path.exists():
        sys.exit(f"ERROR: {dbf_path} not found (must sit next to the .shp)")

    records = None
    try:
        import geopandas as gpd
        records = gpd.read_file(shp_path).to_dict("records")
        log(f"Read IBTrACS with geopandas: {len(records)} track points")
    except ImportError:
        pass

    if records is None:
        try:
            from dbfread import DBF
            records = list(DBF(dbf_path, load=True).records)
            log(f"Read IBTrACS with dbfread: {len(records)} track points")
        except ImportError:
            records = read_dbf_builtin(dbf_path)
            log(f"Read IBTrACS with built-in DBF parser: {len(records)} track points")

    # Group by storm, dropping "spur" tracks (alternate positions that the
    # IBTrACS documentation says not to count).
    storms = defaultdict(list)
    spurs = 0
    for rec in records:
        if "spur" in str(rec.get("TRACK_TYPE", "")).lower():
            spurs += 1
            continue
        storms[rec["SID"]].append(rec)

    log(f"Grouped into {len(storms)} storms ({spurs} spur points dropped)")
    return dict(storms)


# ---------------------------------------------------------------------------
# Track fixes and storm characterization
# ---------------------------------------------------------------------------
def track_fixes(recs):
    """One tuple per parseable track fix, sorted in time.

    Each tuple is (timestamp, lat, lon, status, nature, wind_kt, sshs)."""
    fixes = []
    for r in recs:
        try:
            ts = datetime.strptime(str(r.get("ISO_TIME", "")), "%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            continue
        try:
            lat, lon = float(r["LAT"]), float(r["LON"])
        except (ValueError, TypeError, KeyError):
            continue
        status = str(r.get("USA_STATUS", "")).strip()
        nature = str(r.get("NATURE", "")).strip()
        wind = int(to_number(r.get("USA_WIND"), 0))
        sshs = int(to_number(r.get("USA_SSHS"), -5))
        fixes.append((ts, lat, lon, status, nature, wind, sshs))
    return sorted(fixes, key=lambda p: p[0])


def characterize(fixes):
    """Peak wind, category, phases and label over a set of track fixes.

    Used both for a storm's whole life and for the subset of fixes that pass
    near the watershed during an event."""
    natures, statuses = set(), set()
    max_wind, max_sshs = 0, -5
    for _, _, _, status, nature, wind, sshs in fixes:
        if nature:
            natures.add(nature)
        if status:
            statuses.add(status)
        max_wind = max(max_wind, wind)
        max_sshs = max(max_sshs, sshs)

    if max_sshs >= -2:
        peak = SSHS_LABELS[max_sshs]
    elif "EX" in statuses or "ET" in natures:
        peak = "Post-Tropical/Extratropical"
    elif "LO" in statuses or "DS" in natures:
        peak = "Remnant Low/Dissipating"
    else:
        peak = "Unknown"

    return {
        "natures": sorted(natures),
        "statuses": sorted(statuses),
        "max_wind_kt": max_wind,
        "max_sshs": max_sshs,
        "peak_label": peak,
    }


def summarize_storm(recs):
    """Whole-life summary of one IBTrACS storm. Kept for the QC map labels."""
    fixes = track_fixes(recs)
    times = [p[0] for p in fixes]
    ch = characterize(fixes)
    return {
        "sid": recs[0]["SID"],
        "name": recs[0].get("NAME", "UNNAMED"),
        "natures": ch["natures"],
        "statuses": ch["statuses"],
        "max_wind_kt": ch["max_wind_kt"],
        "max_sshs": ch["max_sshs"],
        "peak_label": ch["peak_label"],
        "times": times,
        "tropical_in_life": bool(set(ch["natures"]) & TROPICAL_NATURE
                                 or set(ch["statuses"]) & TROPICAL_USA_STATUS),
    }


def build_storm_table(storms, log):
    """One entry per storm: its name, track fixes, and full time span."""
    table = {}
    for sid, recs in storms.items():
        fixes = track_fixes(recs)
        if not fixes:
            log(f"  WARNING: {sid} has no parseable track fixes, skipped")
            continue
        table[sid] = {
            "sid": sid,
            "name": recs[0].get("NAME", "UNNAMED"),
            "fixes": fixes,
            "track_start": fixes[0][0],
            "track_end": fixes[-1][0],
        }
    log(f"Built track table for {len(table)} storms")
    return table


# ---------------------------------------------------------------------------
# Spatiotemporal matching
# ---------------------------------------------------------------------------
def passage_match(fixes, plat, plon, win_start, win_end, radius_km):
    """Test one storm against one event window.

    Returns a summary of the storm's passage near the rainfall maximum when at
    least one fix falls inside the padded window and within radius_km of the
    maximum-precipitation location, or None when the storm does not qualify."""
    near = [p for p in fixes
            if win_start <= p[0] <= win_end
            and haversine_km(plat, plon, p[1], p[2]) <= radius_km]
    if not near:
        return None
    dmin = min(haversine_km(plat, plon, p[1], p[2]) for p in near)
    ch = characterize(near)
    ch.update({
        "min_dist_km": dmin,
        "passage_start": min(p[0] for p in near),
        "passage_end": max(p[0] for p in near),
    })
    return ch


def nearest_spatial_gap(fixes, plat, plon, ev_start, ev_end, radius_km):
    """Temporal gap between an event window and the times a storm spent within
    radius_km of the rainfall maximum. Returns None when the storm never came
    within radius_km. Used to flag near-miss NT events."""
    near_times = [p[0] for p in fixes
                  if haversine_km(plat, plon, p[1], p[2]) <= radius_km]
    if not near_times:
        return None
    return gap_hours(ev_start, ev_end, min(near_times), max(near_times))


def event_precip_point(ev):
    """(lat, lon) of the event maximum-precipitation location, or None."""
    try:
        return float(ev["max_precip_lat"]), float(ev["max_precip_lon"])
    except (ValueError, TypeError, KeyError):
        return None


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------
def load_catalog(csv_path):
    events = []
    with open(csv_path) as f:
        for row in csv.DictReader(f):
            row["_start"] = datetime.strptime(row["start_datetime"], "%Y-%m-%dT%H:%M:%S")
            row["_end"] = datetime.strptime(row["end_datetime"], "%Y-%m-%dT%H:%M:%S")
            events.append(row)
    return events


def classify(events, storm_table, window_days, radius_km, log):
    """Tag each event TC or NT by the spatiotemporal match."""
    pad = timedelta(days=window_days)
    results = []
    no_point = []

    for ev in events:
        out = {k: v for k, v in ev.items() if not k.startswith("_")}
        point = event_precip_point(ev)

        matches = []
        if point is not None:
            plat, plon = point
            win_start, win_end = ev["_start"] - pad, ev["_end"] + pad
            for sid, st in storm_table.items():
                if st["track_start"] > win_end or st["track_end"] < win_start:
                    continue
                pm = passage_match(st["fixes"], plat, plon, win_start, win_end, radius_km)
                if pm:
                    matches.append((sid, st["name"], pm))
        elif ev.get("source", "").startswith("ranked-storms"):
            no_point.append(ev["event_id"])

        if matches:
            # If more than one storm qualifies, keep the strongest by peak wind
            # during its passage and record the others for review.
            sid, name, pm = max(matches, key=lambda m: m[2]["max_wind_kt"])
            gap = gap_hours(ev["_start"], ev["_end"], pm["passage_start"], pm["passage_end"])
            out.update({
                "classification": "TC",
                "track_gap_hours": round(gap, 1),
                "min_track_distance_km": round(pm["min_dist_km"], 0),
                "matched_tc_name": name,
                "matched_tc_sid": sid,
                "matched_tc_nature": "|".join(pm["natures"]),
                "matched_tc_status": "|".join(pm["statuses"]),
                "matched_tc_peak_near_precip": pm["peak_label"],
                "matched_tc_max_wind_kt": pm["max_wind_kt"],
                "matched_tc_max_sshs": pm["max_sshs"],
                "other_tc_matches": "|".join(
                    m[1] for m in matches if m[0] != sid),
            })
            log(f"  TC  | event {ev['event_id']:>3s} | {ev['start_datetime'][:10]} | "
                f"{name} ({sid}) | {pm['peak_label']} | {pm['max_wind_kt']} kt | "
                f"{round(pm['min_dist_km'])} km"
                + (f" | also: {out['other_tc_matches']}" if out["other_tc_matches"] else ""))
        else:
            out.update({
                "classification": "NT",
                "track_gap_hours": "",
                "min_track_distance_km": "",
                "matched_tc_name": "", "matched_tc_sid": "",
                "matched_tc_nature": "", "matched_tc_status": "",
                "matched_tc_peak_near_precip": "", "matched_tc_max_wind_kt": "",
                "matched_tc_max_sshs": "", "other_tc_matches": "",
            })
        results.append(out)

    if no_point:
        log(f"  NOTE: {len(no_point)} event(s) had no maximum-precipitation "
            f"location and could not be spatially tested (set NT): {no_point}")
    return results


# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------
def write_outputs(results, storm_table, args, out_dir, log_lines):
    out_dir.mkdir(parents=True, exist_ok=True)
    tc = [r for r in results if r["classification"] == "TC"]
    nt = [r for r in results if r["classification"] == "NT"]

    # 1. Classified catalog
    csv_path = out_dir / "classified_storms.csv"
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(results[0].keys()))
        writer.writeheader()
        writer.writerows(results)

    # 2. Metadata JSON
    matched = sorted({(r["matched_tc_sid"], r["matched_tc_name"]) for r in tc})
    meta = {
        "tool": "classify_storms.py",
        "run_timestamp": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "inputs": {
            "catalog_csv": str(args.catalog),
            "ibtracs_shapefile": str(args.ibtracs),
        },
        "parameters": {
            "temporal_window_days": args.window,
            "match_radius_km": args.radius,
            "event_window": "actual start/end datetimes from the storm catalog",
            "spatial_matching": (
                "each event matched to IBTrACS storms with a track fix within "
                f"{args.radius:g} km of the event maximum-precipitation location and "
                "inside the padded event window (done in this script, no GIS clip); "
                "radius follows Khouakhi et al. (2017) and Kunkel et al. (2010)"),
            "tc_lifecycle_treatment": "all phases count as TC, including extratropical, post-tropical, and remnant stages",
        },
        "results": {
            "total_events": len(results),
            "tc_count": len(tc),
            "nt_count": len(nt),
            "tc_fraction_pct": round(100 * len(tc) / len(results), 2),
            "unique_tcs_matched": len(matched),
            "ibtracs_storms_loaded": len(storm_table),
        },
        "matched_tropical_cyclones": [{"sid": s, "name": n} for s, n in matched],
        "references": [
            "Knapp et al. (2010), IBTrACS, BAMS 91, 363-376, doi:10.1175/2009BAMS2755.1",
            "Khouakhi, Villarini and Vecchi (2017), J. Climate 30, 359-372, doi:10.1175/JCLI-D-16-0298.1",
            "Kunkel et al. (2010), GRL 37, L24706, doi:10.1029/2010GL045164",
            "Smith, Villarini and Baeck (2011), J. Hydrometeorol. 12, 294-309",
            "Evans et al. (2017), MWR 145, 4317-4344, doi:10.1175/MWR-D-17-0027.1",
            "Sturdevant-Rees et al. (2001), WRR 37, 2143-2168, doi:10.1029/2000WR900310",
            "FFRD Meteorology SOP, Section 1.5 (storm typing)",
        ],
    }
    with open(out_dir / "classification_metadata.json", "w") as f:
        json.dump(meta, f, indent=2)

    # 3. Summary report
    by_name = defaultdict(list)
    for r in tc:
        by_name[r["matched_tc_name"]].append(r)

    lines = []
    lines.append("=" * 78)
    lines.append("STORM CLASSIFICATION SUMMARY (TC vs NT)")
    lines.append("=" * 78)
    lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")
    lines.append(f"Total events:        {len(results)}")
    lines.append(f"Tropical (TC):       {len(tc)} ({100 * len(tc) / len(results):.1f}%)")
    lines.append(f"Non-tropical (NT):   {len(nt)} ({100 * len(nt) / len(results):.1f}%)")
    lines.append(f"Unique TCs matched:  {len(by_name)}")
    lines.append(f"Match rule:          track fix within {args.radius:g} km of the rainfall "
                 f"maximum and inside the event window padded by {args.window} day(s)")
    lines.append("")
    lines.append("TC EVENTS (by event id = rank)")
    lines.append("-" * 78)
    lines.append(f"{'Rank':>5}  {'Start date':12}  {'Mean(in)':>8}  {'TC name':16}  "
                 f"{'Phase near precip':26}  {'Wind(kt)':>8}  {'Dist(km)':>8}")
    lines.append("-" * 78)
    for r in sorted(tc, key=lambda x: int(x["event_id"])):
        lines.append(f"{r['event_id']:>5}  {r['start_datetime'][:10]:12}  "
                     f"{float(r['precip_mean_in']):>8.2f}  {r['matched_tc_name']:16}  "
                     f"{r['matched_tc_peak_near_precip']:26}  "
                     f"{str(r['matched_tc_max_wind_kt']):>8}  "
                     f"{str(r['min_track_distance_km']):>8}")
        if r["other_tc_matches"]:
            lines.append(f"{'':>5}  note: also within range of {r['other_tc_matches']}, "
                         f"review which storm caused the rain")
    lines.append("")
    lines.append("MATCHED TROPICAL CYCLONES")
    lines.append("-" * 78)
    for name in sorted(by_name):
        events = by_name[name]
        ranks = ", ".join(str(e["event_id"]) for e in
                          sorted(events, key=lambda x: int(x["event_id"])))
        lines.append(f"{name:16}  {len(events)} event(s)   ranks: {ranks}")
    lines.append("")
    lines.append("NOTES")
    lines.append("-" * 78)
    lines.append("- Phase near precip reflects what the storm was while passing within")
    lines.append(f"  {args.radius:g} km of the rainfall maximum, not its peak over the ocean.")
    lines.append("  A major hurricane at sea often reaches this basin as a depression or")
    lines.append("  remnant.")
    lines.append("- Post-tropical and remnant systems still count as TC because their")
    lines.append("  moisture is tropical in origin and they drive the upper tail of")
    lines.append("  flood frequency in the eastern US.")
    lines.append("- Manual review of borderline matches is recommended, especially")
    lines.append("  events matched near the edge of the padded window and events that")
    lines.append("  are within range of more than one TC.")

    with open(out_dir / "classification_summary.txt", "w") as f:
        f.write("\n".join(lines))

    # 4. Log
    with open(out_dir / "classification_log.txt", "w") as f:
        f.write("\n".join(log_lines))

    print(f"\nOutputs written to {out_dir}:")
    for name in ("classified_storms.csv", "classification_summary.txt",
                 "classification_metadata.json", "classification_log.txt"):
        print(f"  {name}")


def main():
    parser = argparse.ArgumentParser(description="Classify catalog storms as TC or NT.")
    parser.add_argument("--catalog", default=CATALOG_CSV, help="Storm catalog CSV from step 1")
    parser.add_argument("--ibtracs", default=IBTRACS_SHP, help="IBTrACS since-1980 shapefile")
    parser.add_argument("--output-dir", default=OUTPUT_DIR, help="Output directory")
    parser.add_argument("--window", type=float, default=TEMPORAL_WINDOW_DAYS,
                        help="Padding in days around the event window")
    parser.add_argument("--radius", type=float, default=MATCH_RADIUS_KM,
                        help="Matching radius in km to the rainfall maximum")
    args = parser.parse_args()

    log_lines = []

    def log(msg):
        log_lines.append(msg)
        print(msg)

    log("=" * 60)
    log("STORM CLASSIFICATION: TC vs NT")
    log("=" * 60)
    log(f"Catalog:  {args.catalog}")
    log(f"IBTrACS:  {args.ibtracs}")
    log(f"Window:   {args.window} day(s) around the event window")
    log(f"Radius:   {args.radius:g} km to the rainfall maximum")
    log("")

    if args.window < 0:
        sys.exit("ERROR: --window must be zero or positive")
    if args.radius <= 0:
        sys.exit("ERROR: --radius must be positive")

    storms = load_ibtracs(args.ibtracs, log)
    storm_table = build_storm_table(storms, log)
    if not storm_table:
        sys.exit(f"ERROR: no usable tracks in {args.ibtracs}")
    events = load_catalog(args.catalog)
    if not events:
        sys.exit(f"ERROR: no events found in {args.catalog}")
    log(f"Loaded {len(events)} catalog events")
    log("")

    results = classify(events, storm_table, args.window, args.radius, log)

    tc_n = sum(1 for r in results if r["classification"] == "TC")
    log("")
    log(f"RESULT: {tc_n} TC, {len(results) - tc_n} NT "
        f"({100 * tc_n / len(results):.1f}% tropical)")

    write_outputs(results, storm_table, args, Path(args.output_dir), log_lines)


if __name__ == "__main__":
    main()
