#!/usr/bin/env python3
"""Generate and add mass curve plots to a STAC storm catalog.

This script adds mass curve visualizations to existing storm items in a STAC catalog.
Assumes the catalog structure is already in place with item JSONs and AORC metadata.

Edit the USER INPUTS block below, then run this file.
"""

from __future__ import annotations

import functools
import gc
import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import geopandas as gpd
import matplotlib
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd
import rioxarray  # noqa: F401 — registers .rio accessor
import xarray as xr
from shapely.affinity import translate

# AORC precipitation variable name and unit conversion.
AORC_PRECIP_VARIABLE = "APCP_surface"
MM_TO_INCH_CONVERSION_FACTOR = 0.0393701

# Standard duration folders used by StormCatalog outputs.
DEFAULT_DURATIONS = ["3hr-events", "6hr-events", "12hr-events", "24hr-events", "48hr-events", "72hr-events"]


# =============================================================================
# USER INPUTS - EDIT THIS BLOCK, THEN RUN THE SCRIPT
# =============================================================================
#
# This script is meant to be run directly from your Python editor or IDE.
# Update the values below to match the catalog and watershed files on your
# machine, then press Run. No command-line arguments are needed.

# Folder that contains the storm catalog duration folders, such as:
#   3hr-events/
#   6hr-events/
#   12hr-events/
#   24hr-events/
#   48hr-events/
#   72hr-events/
#
# For the included local example, this points to:
#   meteorology-tools/Scripts/StormCatalog_MassCurve/Inputs
SCRIPT_DIR = Path(__file__).resolve().parent
CATALOG_DIR = SCRIPT_DIR / "Inputs"

# Base watershed GeoJSON used to transpose the watershed to each storm location.
# This must be the same base watershed used when the storm catalog was created.
BASE_WATERSHED_PATH = CATALOG_DIR / "Upper-Tennessee_huc04.geojson"

# Duration folders to process. Use only folders that exist under CATALOG_DIR.
# Examples:
#   ["72hr-events"]
#   ["24hr-events", "48hr-events", "72hr-events"]
#   DEFAULT_DURATIONS
DURATIONS_TO_PROCESS = ["72hr-events"]

# Set to True to overwrite plots even when the item JSON already has a
# mass_curve asset. False skips finished items and is safer for routine reruns.
REGENERATE_EXISTING = True

# Optional cap on how many pending items to process. Use None for all pending
# items. This is useful for quick testing, for example PROCESS_LIMIT = 10.
PROCESS_LIMIT = None

# Parallel processing controls. Lower NUM_WORKERS if S3/network access is slow
# or if the machine is memory constrained.
NUM_WORKERS = 8
BATCH_SIZE = 32


matplotlib.use("Agg")
plt.rcParams.update(
    {
        "font.family": "Arial",
        "font.size": 18,
        "axes.titlesize": 18,
        "axes.labelsize": 18,
        "xtick.labelsize": 18,
        "ytick.labelsize": 18,
    }
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


@functools.lru_cache(maxsize=8)
def _load_base_watershed(base_watershed_path: str) -> gpd.GeoDataFrame:
    """Load and cache a watershed GeoDataFrame."""
    return gpd.read_file(base_watershed_path)


def apply_aorc_transform(
    base_watershed_gdf: gpd.GeoDataFrame,
    transform_c: float,
    transform_f: float,
) -> gpd.GeoDataFrame:
    """Return a copy of base_watershed_gdf with all geometries shifted by (c, f) degrees."""
    shifted = base_watershed_gdf.copy()
    shifted["geometry"] = shifted.geometry.apply(lambda g: translate(g, xoff=transform_c, yoff=transform_f))
    return shifted


def fetch_precip_timeseries(
    start_dt: datetime,
    end_dt: datetime,
    aoi_gdf: gpd.GeoDataFrame,
) -> xr.DataArray:
    """Fetch AORC APCP_surface over aoi_gdf for the storm time window.

    Adjusts start_dt by +1hr because AORC timestamps are period-ending.
    Returns a lazy DataArray in mm (not yet materialized).
    """
    from stormhub.met.zarr_to_dss import get_aorc_paths, get_s3_zarr_data

    fetch_start = start_dt.replace(tzinfo=None) + timedelta(hours=1)
    fetch_end = end_dt.replace(tzinfo=None)
    s3_paths = get_aorc_paths(fetch_start, fetch_end)
    ds = get_s3_zarr_data(s3_paths, aoi_gdf, fetch_start, fetch_end, [AORC_PRECIP_VARIABLE])
    return ds[AORC_PRECIP_VARIABLE]


def compute_point_mass_curve(
    da: xr.DataArray, lat: float, lon: float
) -> tuple[pd.Series, pd.Series]:
    """Return (hourly_inches, cumulative_inches) at the nearest AORC grid cell to (lat, lon)."""
    point = da.sel(latitude=lat, longitude=lon, method="nearest").compute()
    hourly = pd.Series(
        point.values * MM_TO_INCH_CONVERSION_FACTOR,
        index=pd.DatetimeIndex(da.time.values),
    )
    return hourly, hourly.cumsum()


def compute_areal_mean_mass_curve(
    da: xr.DataArray, transposed_watershed_gdf: gpd.GeoDataFrame
) -> tuple[pd.Series, pd.Series]:
    """Return (hourly_inches, cumulative_inches) as spatial mean over the transposed watershed."""
    if da.rio.crs is None:
        da = da.rio.write_crs("EPSG:4326")
    geoms = [transposed_watershed_gdf.geometry.iloc[0]]
    clipped = da.rio.clip(geoms, drop=True, all_touched=True)
    spatial_mean = clipped.mean(dim=["latitude", "longitude"]).compute()
    hourly = pd.Series(
        spatial_mean.values * MM_TO_INCH_CONVERSION_FACTOR,
        index=pd.DatetimeIndex(da.time.values),
    )
    return hourly, hourly.cumsum()


def plot_mass_curves(
    point_hourly: pd.Series,
    point_cumulative: pd.Series,
    areal_hourly: pd.Series,
    areal_cumulative: pd.Series,
    item_id: str,
    duration_hours: int,
    start_dt: datetime,
    end_dt: datetime,
    output_path: str,
) -> str:
    """Create and save a stacked-panel mass curve figure. Returns output_path.

    Top panel: accumulated precipitation lines (point + areal).
    Bottom panel: hourly precipitation bars (point + areal), side-by-side.
    """
    title = f"Storm {item_id} | {duration_hours}-hour Precipitation Mass Curve"
    subtitle = (
        f"{start_dt.strftime('%b %d, %Y %H:%M')} - "
        f"{end_dt.strftime('%b %d, %Y %H:%M')} UTC"
    )

    fig, (ax1, ax2) = plt.subplots(
        2,
        1,
        figsize=(18, 11),
        sharex=True,
        gridspec_kw={"height_ratios": [1.0, 1.0], "hspace": 0.14},
    )

    # Side-by-side bars: 20 min wide each, offset ±10 min
    bar_w = 20 / 60 / 24  # days (matplotlib date units)
    half_w = bar_w / 2
    x = mdates.date2num(point_hourly.index.to_pydatetime())

    b1 = ax2.bar(
        x - half_w,
        point_hourly.values,
        width=bar_w,
        color="steelblue",
        alpha=0.55,
        label="Hourly peak AORC grid cell precip",
    )
    b2 = ax2.bar(
        x + half_w,
        areal_hourly.values,
        width=bar_w,
        color="darkorange",
        alpha=0.55,
        label="Hourly watershed mean precip",
    )
    ax2.set_ylabel("Hourly\nPrecipitation\n(inches)")
    ax2.set_ylim(bottom=0)

    # Cumulative lines on top axis.
    l1, = ax1.plot(
        point_cumulative.index,
        point_cumulative.values,
        color="steelblue",
        linewidth=3,
        label="Cumulative peak AORC grid cell precip",
    )
    l2, = ax1.plot(
        areal_cumulative.index,
        areal_cumulative.values,
        color="darkorange",
        linewidth=3,
        linestyle="--",
        label="Cumulative watershed mean precip",
    )
    ax1.set_ylabel("Accumulated\nPrecipitation\n(inches)")
    fig.suptitle(title, y=0.958, fontsize=22)
    fig.text(0.5, 0.918, subtitle, ha="center", va="center", fontsize=15)
    ax1.set_ylim(bottom=0)

    # End-of-line totals — annotate just inside the right edge
    final_pt = point_cumulative.iloc[-1]
    final_ar = areal_cumulative.iloc[-1]
    plot_start = pd.Timestamp(start_dt)
    plot_end = pd.Timestamp(end_dt)
    if plot_start.tzinfo is not None:
        plot_start = plot_start.tz_convert(None)
    if plot_end.tzinfo is not None:
        plot_end = plot_end.tz_convert(None)
    time_span = plot_end - plot_start
    label_x = plot_end + time_span * 0.13
    x_right = plot_end + time_span * 0.14
    max_cumulative = max(point_cumulative.max(), areal_cumulative.max())
    cumulative_ymax = max_cumulative * 1.22 if max_cumulative > 0 else 1
    label_gap = max_cumulative * 0.055 if max_cumulative > 0 else 0.05
    point_label_y = min(final_pt + label_gap, cumulative_ymax * 0.95)
    areal_label_y = min(final_ar + label_gap, cumulative_ymax * 0.88)
    ax1.set_xlim(plot_start, x_right)
    ax1.set_ylim(0, cumulative_ymax)
    ax1.text(
        label_x,
        point_label_y,
        f"Peak total: {final_pt:.2f} in",
        ha="right",
        va="center",
        color="steelblue",
        fontsize=18,
        fontweight="bold",
    )
    ax1.text(
        label_x,
        areal_label_y,
        f"Watershed mean total: {final_ar:.2f} in",
        ha="right",
        va="center",
        color="darkorange",
        fontsize=18,
        fontweight="bold",
    )

    max_hourly = max(point_hourly.max(), areal_hourly.max())
    ax2.set_ylim(0, max_hourly * 1.18 if max_hourly > 0 else 1)
    ax2.set_xlabel("Date/Time (UTC)")

    if duration_hours <= 6:
        tick_interval = 1
    elif duration_hours <= 12:
        tick_interval = 2
    elif duration_hours <= 24:
        tick_interval = 4
    elif duration_hours <= 48:
        tick_interval = 6
    else:
        tick_interval = 12
    tick_positions = pd.date_range(plot_start, plot_end, freq=f"{tick_interval}h")
    ax2.set_xticks(tick_positions)
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%b %d\n%H:%M"))
    ax2.tick_params(axis="x", rotation=0)

    for ax in (ax1, ax2):
        ax.grid(True, alpha=0.3)

    fig.legend(
        handles=[l1, l2, b1, b2],
        loc="upper center",
        ncol=4,
        frameon=False,
        fontsize=14,
        bbox_to_anchor=(0.5, 0.895),
        columnspacing=1.2,
        handlelength=2.2,
    )
    fig.subplots_adjust(left=0.09, right=0.965, top=0.85, bottom=0.12)
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return output_path


def _update_item_asset(item_path: str, png_filename: str) -> None:
    """Add mass_curve PNG as a STAC asset by directly editing the item JSON."""
    with open(item_path) as f:
        item_dict = json.load(f)
    item_dict.setdefault("assets", {})["mass_curve"] = {
        "href": png_filename,
        "type": "image/png",
        "roles": ["thumbnail"],
        "title": "Mass Curve",
    }
    with open(item_path, "w") as f:
        json.dump(item_dict, f, indent=2)


def generate_mass_curve_for_item(
    item_path: str,
    base_watershed_path: str,
    force: bool = False,
) -> Optional[str]:
    """Generate a mass curve plot for one STAC item and register it as an asset.

    Returns the PNG path on success, None if the asset already exists (skip).
    Raises on failure so the caller can record the error.
    Pass force=True to overwrite an existing plot without modifying the JSON.
    """
    item_path = str(item_path)

    with open(item_path) as f:
        item_dict = json.load(f)

    already_done = "mass_curve" in item_dict.get("assets", {})
    if already_done and not force:
        return None

    item_id = item_dict["id"]
    props = item_dict["properties"]
    start_dt = datetime.fromisoformat(props["start_datetime"].replace("Z", "+00:00"))
    end_dt = datetime.fromisoformat(props["end_datetime"].replace("Z", "+00:00"))
    duration_hours = int((end_dt - start_dt).total_seconds() / 3600)
    lat = props["aorc:max_precip_location"]["latitude"]
    lon = props["aorc:max_precip_location"]["longitude"]
    transform = props["aorc:transform"]

    base_ws = _load_base_watershed(base_watershed_path)
    transposed_ws = apply_aorc_transform(base_ws, transform["c"], transform["f"])
    aoi_gdf = gpd.GeoDataFrame(
        geometry=[transposed_ws.geometry.iloc[0].buffer(0.1)], crs="EPSG:4326"
    )

    da = fetch_precip_timeseries(start_dt, end_dt, aoi_gdf)
    point_hourly, point_cumulative = compute_point_mass_curve(da, lat, lon)
    areal_hourly, areal_cumulative = compute_areal_mean_mass_curve(da, transposed_ws)
    del da
    gc.collect()

    png_filename = f"{item_id}.mass_curve.png"
    output_path = str(Path(item_path).parent / png_filename)
    plot_mass_curves(
        point_hourly,
        point_cumulative,
        areal_hourly,
        areal_cumulative,
        item_id,
        duration_hours,
        start_dt,
        end_dt,
        output_path,
    )
    if not already_done:
        _update_item_asset(item_path, png_filename)

    logger.info(f"Generated mass curve for item {item_id} ({duration_hours}hr)")
    return output_path


def discover_pending_items(catalog_dir: str, durations: list[str], force: bool = False) -> list[dict]:
    """Scan duration folders and return items to process.

    Without force, skips items that already have the mass_curve asset.
    With force, returns all items so their plots get overwritten.
    """
    pending = []
    for duration in durations:
        duration_dir = os.path.join(catalog_dir, duration)
        if not os.path.isdir(duration_dir):
            logger.warning(f"Duration directory not found: {duration_dir}")
            continue
        entries = sorted(
            (e for e in os.scandir(duration_dir) if e.is_dir()),
            key=lambda e: int(e.name) if e.name.isdigit() else 0,
        )
        for entry in entries:
            item_path = os.path.join(entry.path, f"{entry.name}.json")
            if not os.path.exists(item_path):
                continue
            try:
                with open(item_path) as f:
                    item_dict = json.load(f)
            except (json.JSONDecodeError, OSError):
                logger.warning(f"Could not read {item_path}")
                continue
            has_asset = "mass_curve" in item_dict.get("assets", {})
            if force or not has_asset:
                pending.append({"item_path": item_path, "item_id": entry.name, "duration": duration})
    return pending


def _worker(args: dict) -> dict:
    """Worker function to process a single item."""
    item_path = args["item_path"]
    try:
        result = generate_mass_curve_for_item(item_path, args["base_watershed_path"], force=args["force"])
        outcome = "skipped" if result is None else "success"
        return {"item_path": item_path, "outcome": outcome, "error": None}
    except Exception as e:
        return {"item_path": item_path, "outcome": "failed", "error": str(e)}


def run(
    catalog_dir: str,
    base_watershed_path: str,
    num_workers: int = 8,
    batch_size: int = 32,
    limit: int = None,
    force: bool = False,
    durations: list = None,
) -> None:
    """Generate mass curves for all items in a storm catalog.

    Args:
        catalog_dir: Root directory of the storm catalog.
        base_watershed_path: Path to the base watershed GeoJSON.
        num_workers: Number of worker threads.
        batch_size: Items per batch before gc.collect().
        limit: Process only the first N pending items.
        force: Regenerate plots even if mass_curve asset already exists.
        durations: Duration folders to process (default: all standard durations).
    """
    pending = discover_pending_items(catalog_dir, durations or DEFAULT_DURATIONS, force=force)
    if limit:
        pending = pending[:limit]
    total = len(pending)
    logger.info(f"Found {total} items pending mass curve generation")

    if total == 0:
        logger.info("No items to process.")
        return

    done = skipped = failed = 0
    for batch_start in range(0, total, batch_size):
        batch = [
            dict(item, base_watershed_path=base_watershed_path, force=force)
            for item in pending[batch_start : batch_start + batch_size]
        ]

        with ThreadPoolExecutor(max_workers=num_workers) as executor:
            futures = {executor.submit(_worker, item): item for item in batch}
            for future in as_completed(futures):
                result = future.result()
                if result["outcome"] == "success":
                    done += 1
                elif result["outcome"] == "skipped":
                    skipped += 1
                else:
                    failed += 1
                    logger.error(f"Failed: {result['item_path']} — {result['error']}")

        batch_end = min(batch_start + batch_size, total)
        logger.info(
            f"Batch {batch_start}–{batch_end}: {done} done, {skipped} skipped, {failed} failed "
            f"({done + skipped + failed}/{total} total)"
        )
        gc.collect()

    logger.info(f"Complete: {done} generated, {skipped} skipped, {failed} failed")


def validate_user_inputs() -> None:
    """Fail early with clear messages when the editable input block is not ready."""
    if not Path(CATALOG_DIR).is_dir():
        raise FileNotFoundError(f"CATALOG_DIR does not exist or is not a folder: {CATALOG_DIR}")
    if not Path(BASE_WATERSHED_PATH).is_file():
        raise FileNotFoundError(f"BASE_WATERSHED_PATH does not exist or is not a file: {BASE_WATERSHED_PATH}")
    if not DURATIONS_TO_PROCESS:
        raise ValueError("DURATIONS_TO_PROCESS must list at least one duration folder.")
    missing = [duration for duration in DURATIONS_TO_PROCESS if not (Path(CATALOG_DIR) / duration).is_dir()]
    if missing:
        raise FileNotFoundError(
            "These duration folders were not found under CATALOG_DIR: "
            + ", ".join(missing)
        )


def main():
    """Run mass curve generation from the USER INPUTS block."""
    validate_user_inputs()
    run(
        catalog_dir=str(CATALOG_DIR),
        base_watershed_path=str(BASE_WATERSHED_PATH),
        num_workers=NUM_WORKERS,
        batch_size=BATCH_SIZE,
        limit=PROCESS_LIMIT,
        force=REGENERATE_EXISTING,
        durations=DURATIONS_TO_PROCESS,
    )


if __name__ == "__main__":
    main()
