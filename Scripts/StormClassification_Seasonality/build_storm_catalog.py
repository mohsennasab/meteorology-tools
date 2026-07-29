#!/usr/bin/env python3
"""
Step 1: Build the storm catalog from StormHub outputs.

Reads the STAC item JSON files that StormHub writes for each ranked event
(one folder per event under the 72hr-events directory) and collects them
into a single catalog CSV. The STAC items are used directly because they
carry the exact event start and end datetimes plus the precipitation
statistics, so nothing is rounded or re-derived.

Input  : StormHub 72hr-events directory (STAC items, one JSON per event)
Output : output/storm_catalog.csv

Usage:
    python build_storm_catalog.py
    python build_storm_catalog.py --events-dir "D:/path/to/72hr-events"

Author: Mohsen Tahmasebi Nasab
AECOM FFRD Upper Tennessee Validation Basin Study
"""

import argparse
import csv
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

# ===========================================================================
# USER SETTINGS
# Edit these paths for your project, or override them on the command line.
# ===========================================================================

# StormHub 72hr-events directory. Each event lives in a numbered subfolder
# (1, 2, 3, ...) that contains <id>.json, a thumbnail, and a DSS file.
EVENTS_DIR = r"C:\OneDrive\OneDrive - AECOM\FFRD\Validation Basin\updated_analysis_July2026\StormHub\24hr-temp\24hr-temp\72hr-events"

# Where the catalog CSV gets written.
OUTPUT_CSV = r"output\storm_catalog.csv"

# If an event folder is missing its STAC JSON (this happens; event 169 in
# the Upper Tennessee catalog only has a thumbnail), fall back to the
# matching row of ranked-storms.csv in the events directory. Fallback rows
# are marked in the "source" column so they are easy to audit. Note that
# ranked-storms.csv can come from a different StormHub run, so a fallback
# date may differ by a day from what the STAC item would have said.
USE_CSV_FALLBACK = True

# ===========================================================================
# END OF USER SETTINGS
# ===========================================================================


def parse_iso(ts):
    """Parse a STAC timestamp like 2024-09-25T00:00:00Z into a datetime."""
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).replace(tzinfo=None)


def read_stac_item(json_path):
    """Pull the fields we need out of one STAC item JSON."""
    with open(json_path) as f:
        item = json.load(f)

    props = item["properties"]
    stats = props["aorc:statistics"]
    maxloc = props.get("aorc:max_precip_location", {})
    geom = item.get("geometry", {}).get("coordinates", [None, None])

    start = parse_iso(props["start_datetime"])
    end = parse_iso(props["end_datetime"])

    return {
        "event_id": int(item["id"]),
        "start_datetime": start.strftime("%Y-%m-%dT%H:%M:%S"),
        "end_datetime": end.strftime("%Y-%m-%dT%H:%M:%S"),
        "duration_hours": round((end - start).total_seconds() / 3600, 3),
        "precip_mean_in": stats["mean"],
        "precip_min_in": stats["min"],
        "precip_max_in": stats["max"],
        "max_precip_lat": maxloc.get("latitude", ""),
        "max_precip_lon": maxloc.get("longitude", ""),
        "centroid_lon": geom[0],
        "centroid_lat": geom[1],
        "source": "stac_item",
    }


def read_fallback_rows(events_dir):
    """
    Load ranked-storms.csv (written by StormHub next to the event folders)
    keyed by por_rank. Only used for events whose STAC JSON is missing.
    """
    csv_path = events_dir / "ranked-storms.csv"
    if not csv_path.exists():
        return {}

    rows = {}
    with open(csv_path) as f:
        for row in csv.DictReader(f):
            rows[int(row["por_rank"])] = row
    return rows


def read_default_duration(events_dir):
    """Read the event duration from params-config.json if StormHub wrote one."""
    cfg_path = events_dir / "params-config.json"
    if cfg_path.exists():
        with open(cfg_path) as f:
            cfg = json.load(f)
        return cfg.get("storm_duration_hours", 72)
    return 72


def build_catalog(events_dir, use_fallback):
    """Walk the numbered event folders and collect one record per event."""
    event_dirs = sorted(
        (int(p.name) for p in events_dir.iterdir() if p.is_dir() and p.name.isdigit())
    )
    if not event_dirs:
        sys.exit(f"ERROR: no numbered event folders found in {events_dir}")

    print(f"Found {len(event_dirs)} event folders (ids {event_dirs[0]} to {event_dirs[-1]})")

    fallback = read_fallback_rows(events_dir) if use_fallback else {}
    default_duration = read_default_duration(events_dir)

    records = []
    missing = []

    for eid in event_dirs:
        json_path = events_dir / str(eid) / f"{eid}.json"

        if json_path.exists():
            records.append(read_stac_item(json_path))
            continue

        missing.append(eid)
        if eid in fallback:
            row = fallback[eid]
            # ranked-storms.csv stores the start as YYYY-MM-DDTHH
            start = datetime.strptime(row["storm_date"].strip(), "%Y-%m-%dT%H")
            end = start + timedelta(hours=default_duration)
            records.append({
                "event_id": eid,
                "start_datetime": start.strftime("%Y-%m-%dT%H:%M:%S"),
                "end_datetime": end.strftime("%Y-%m-%dT%H:%M:%S"),
                "duration_hours": float(default_duration),
                "precip_mean_in": float(row["mean"]),
                "precip_min_in": float(row["min"]),
                "precip_max_in": float(row["max"]),
                "max_precip_lat": "",
                "max_precip_lon": "",
                "centroid_lon": "",
                "centroid_lat": "",
                "source": "ranked-storms.csv (fallback)",
            })
            print(f"  WARNING: event {eid} has no STAC JSON. "
                  f"Filled from ranked-storms.csv ({row['storm_date']}). "
                  f"Check this event manually.")
        else:
            print(f"  WARNING: event {eid} has no STAC JSON and no fallback row. Skipped.")

    return records, missing


def qc_checks(records):
    """Basic sanity checks on the assembled catalog."""
    print("\nQC checks")
    print("-" * 60)

    n = len(records)
    if n == 0:
        sys.exit("ERROR: no events could be read; nothing to write")
    print(f"Events in catalog:        {n}")

    starts = [r["start_datetime"] for r in records]
    print(f"Date range:               {min(starts)[:10]} to {max(starts)[:10]}")

    durations = sorted(set(r["duration_hours"] for r in records))
    print(f"Event durations (hours):  {durations}")

    # Event ids should follow descending basin-mean precipitation. STAC items
    # from the same run always do; a fallback row from a different run can
    # break the order at ties, which is harmless but worth flagging.
    violations = [
        (records[i]["event_id"], records[i]["precip_mean_in"], records[i + 1]["precip_mean_in"])
        for i in range(n - 1)
        if records[i]["precip_mean_in"] < records[i + 1]["precip_mean_in"]
    ]
    if violations:
        print(f"Rank order check:         {len(violations)} out-of-order pairs {violations[:3]}")
    else:
        print("Rank order check:         PASS (mean precip decreases with event id)")

    fallback_ids = [r["event_id"] for r in records if r["source"] != "stac_item"]
    if fallback_ids:
        print(f"Fallback events:          {fallback_ids} (from ranked-storms.csv, verify manually)")
    else:
        print("Fallback events:          none")


def write_catalog(records, out_path):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "event_id", "start_datetime", "end_datetime", "duration_hours",
        "precip_mean_in", "precip_min_in", "precip_max_in",
        "max_precip_lat", "max_precip_lon", "centroid_lon", "centroid_lat",
        "source",
    ]
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(sorted(records, key=lambda r: r["event_id"]))
    print(f"\nCatalog written to: {out_path}")


def main():
    parser = argparse.ArgumentParser(description="Build storm catalog from StormHub STAC items.")
    parser.add_argument("--events-dir", default=EVENTS_DIR,
                        help="StormHub 72hr-events directory")
    parser.add_argument("--output", default=OUTPUT_CSV,
                        help="Output catalog CSV path")
    parser.add_argument("--no-fallback", action="store_true",
                        help="Skip events with missing STAC JSON instead of "
                             "filling them from ranked-storms.csv")
    args = parser.parse_args()

    events_dir = Path(args.events_dir)
    if not events_dir.exists():
        sys.exit(f"ERROR: events directory not found: {events_dir}")

    print("=" * 60)
    print("BUILD STORM CATALOG FROM STORMHUB STAC ITEMS")
    print("=" * 60)
    print(f"Events directory: {events_dir}")

    records, missing = build_catalog(events_dir, use_fallback=not args.no_fallback)
    qc_checks(records)
    write_catalog(records, Path(args.output))


if __name__ == "__main__":
    main()
