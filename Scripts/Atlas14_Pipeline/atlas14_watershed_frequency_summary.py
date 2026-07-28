"""
NOAA Atlas 14 watershed precipitation-frequency summary.

The script:
  1. Downloads Atlas 14 PDS rasters for each return period, NOAA volume,
     duration, and confidence bound.
  2. Mosaics the NOAA volumes for each return period and confidence bound. The
     first listed volume takes priority. Values are not resampled or averaged.
  3. Clips each mosaic to the watershed in memory.
  4. Calculates the watershed mean, minimum, maximum, and pixel count. The
     calculation converts NOAA's stored values from inches x 1,000 to inches.
  5. Saves a CSV and a frequency-curve PNG with the 90% confidence interval.

Atlas 14 grids use NAD83 (EPSG:4269). The script projects the watershed to the
raster CRS before clipping. The clip does not resample the raster. This tool
does not create an HEC-HMS bias grid.

NOAA HDSC volume codes (lowercase): orb=Vol2 (Ohio River Basin), se=Vol9
(Southeast), mw=Vol8 (Midwest), tx=Vol11 (Texas), ne=Vol10 (Northeast).
"""

# This is a watershed statistics tool. The watershed clip is evaluated in
# memory to report the precipitation-frequency range; it is not an HEC-HMS
# bias-grid workflow.

# ============================ USER CONFIG ============================
RETURN_PERIODS = [1, 2, 5, 10, 25, 50, 100]   # years
DURATION_DAYS  = 3                              # 3-day = 72-hr; Atlas 14 code "03da"
VOLUMES        = ["orb", "se"]                  # volumes to mosaic, in priority order
WATERSHED_PATH = "/workspaces/meteorology-tools/inputs/watershed/Upper-Tennessee_huc04.geojson"
OUTPUT_DIR     = "/workspaces/meteorology-tools/outputs/na14"
WATERSHED_LABEL = "Upper Tennessee (HUC 0601)"
# =====================================================================

# Import matplotlib before rasterio in the slamcomp environment to avoid a
# libexpat DLL conflict.
import matplotlib as mpl
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker

import argparse
import zipfile
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd
import geopandas as gpd
from shapely.geometry import mapping

import rasterio
import rasterio.mask
from rasterio.merge import merge


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Calculate Atlas 14 precipitation-frequency statistics for a "
            "watershed. This tool does not create an HEC-HMS bias grid."
        )
    )
    parser.add_argument(
        "--watershed-path",
        type=Path,
        default=Path(WATERSHED_PATH),
        help="Watershed polygon used for the in-memory statistics clip.",
    )
    parser.add_argument(
        "--atlas14-data-dir",
        type=Path,
        default=Path(OUTPUT_DIR),
        help=(
            "Atlas 14 cache containing downloads, extracted rasters, and "
            "mosaics. Existing files are reused."
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help=(
            "Folder for the final CSV and PNG. Defaults to --atlas14-data-dir "
            "for backward compatibility."
        ),
    )
    return parser.parse_args()


ARGS = parse_args()
WATERSHED_PATH = ARGS.watershed_path.resolve()
ATLAS14_DATA_DIR = ARGS.atlas14_data_dir.resolve()
SUMMARY_OUTPUT_DIR = (
    ARGS.output_dir.resolve() if ARGS.output_dir else ATLAS14_DATA_DIR
)
ATLAS14_DATA_DIR.mkdir(parents=True, exist_ok=True)
SUMMARY_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

NOAA_BASE_URL  = "https://hdsc.nws.noaa.gov/pub/hdsc/data"
DUR_CODE       = f"{DURATION_DAYS:02d}da"
SUFFIXES       = {"": "best estimate", "u": "upper 90% CI", "l": "lower 90% CI"}


def asset_filename(vol: str, T: int, suffix: str) -> str:
    return f"{vol}{T}yr{DUR_CODE}{suffix}.zip"


def download_one(vol: str, T: int, suffix: str):
    """Download one Atlas 14 ZIP file, or reuse the cached file."""
    fname = asset_filename(vol, T, suffix)
    url   = f"{NOAA_BASE_URL}/{vol}/{fname}"
    out   = ATLAS14_DATA_DIR / fname
    if out.exists() and out.stat().st_size > 0:
        return out
    try:
        with urllib.request.urlopen(url, timeout=120) as resp:
            data = resp.read()
        out.write_bytes(data)
        print(f"      downloaded {fname} ({len(data)/1024:.0f} KB)")
        return out
    except Exception as e:
        print(f"      FAILED {fname} ({type(e).__name__}: {e})")
        return None


def extract_asc(zip_path: Path):
    """Extract the ZIP file when needed and return its ASC raster."""
    extract_dir = zip_path.with_suffix("")
    extract_dir.mkdir(parents=True, exist_ok=True)
    asc = list(extract_dir.glob("*.asc"))
    if asc:
        return asc[0]
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(extract_dir)
    asc = list(extract_dir.glob("*.asc"))
    return asc[0] if asc else None


def write_crs_consistent_geotiff(asc_file: Path, target_crs="EPSG:4269") -> Path:
    """Copy an ASC raster to GeoTIFF and assign a consistent CRS.

    NOAA volumes use different PRJ strings for the same NAD83 coordinate
    system. The pixel grid, values, transform, and NoData value do not change.
    """
    out = asc_file.with_suffix(".tif")
    if out.exists() and out.stat().st_size > 0:
        return out
    with rasterio.open(asc_file) as src:
        profile = src.profile.copy()
        data    = src.read(1)
    profile.update(driver="GTiff", crs=target_crs, compress="lzw")
    with rasterio.open(out, "w", **profile) as dst:
        dst.write(data, 1)
    return out


def mosaic_and_save(asc_files, out_tif: Path):
    """Mosaic source rasters with the first listed NOAA volume as priority."""
    if out_tif.exists() and out_tif.stat().st_size > 0:
        return out_tif
    crs_consistent = [write_crs_consistent_geotiff(f) for f in asc_files]
    sources = [rasterio.open(f) for f in crs_consistent]
    try:
        mosaic, mosaic_transform = merge(
            sources, method="first", nodata=sources[0].nodata
        )
        profile = sources[0].profile.copy()
        crs     = sources[0].crs
        nodata  = sources[0].nodata
    finally:
        for s in sources:
            s.close()
    profile.update(
        driver    = "GTiff",
        transform = mosaic_transform,
        width     = mosaic.shape[2],
        height    = mosaic.shape[1],
        crs       = crs,
        nodata    = nodata,
        compress  = "lzw",
    )
    with rasterio.open(out_tif, "w", **profile) as dst:
        dst.write(mosaic[0], 1)
    return out_tif


def basin_stats(mosaic_tif: Path):
    """Clip a mosaic in memory and calculate watershed statistics."""
    with rasterio.open(mosaic_tif) as src:
        ws = gpd.read_file(WATERSHED_PATH).to_crs(src.crs)
        geoms = [mapping(g) for g in ws.geometry]
        clipped, _ = rasterio.mask.mask(src, geoms, crop=True, nodata=src.nodata)
        nodata = src.nodata
    arr = clipped[0].astype(np.float64)
    if nodata is not None:
        arr = np.where(arr == nodata, np.nan, arr)
    arr = arr / 1000.0   # NOAA stores values as inches × 1000
    valid = arr[np.isfinite(arr)]
    if valid.size == 0:
        return None
    return {
        "basin_mean_in": float(valid.mean()),
        "basin_min_in":  float(valid.min()),
        "basin_max_in":  float(valid.max()),
        "n_pixels":      int(valid.size),
    }


def process_one(T: int, suffix: str):
    """Process one return period and confidence-bound suffix."""
    asc_files = []
    for vol in VOLUMES:
        zp = download_one(vol, T, suffix)
        if zp is None:
            continue
        asc = extract_asc(zp)
        if asc is not None:
            asc_files.append(asc)
    if not asc_files:
        return None
    mosaic_tif = ATLAS14_DATA_DIR / f"mosaic_{T}yr{DUR_CODE}{suffix}.tif"
    mosaic_and_save(asc_files, mosaic_tif)
    return basin_stats(mosaic_tif)


# ============================ MAIN ============================

print(f"NOAA Atlas 14 PDS pipeline: {WATERSHED_LABEL}")
print(f"  Volumes: {', '.join(VOLUMES)}")
print(f"  Duration: {DUR_CODE} ({DURATION_DAYS}-day = {DURATION_DAYS*24}-hr)")
print(f"  Return periods: {RETURN_PERIODS}\n")

rows = []
for T in RETURN_PERIODS:
    print(f"--- T = {T} yr ---")
    row = {"return_period_yr": T}
    for suffix, label in SUFFIXES.items():
        print(f"  {label}:")
        s = process_one(T, suffix)
        if s is None:
            print("    (no data)")
            continue
        if suffix == "":
            row.update({k: round(v, 3) if isinstance(v, float) else v
                        for k, v in s.items()})
        else:
            tag = "upper" if suffix == "u" else "lower"
            row[f"basin_mean_{tag}90_in"] = round(s["basin_mean_in"], 3)
        print(f"    basin-mean {s['basin_mean_in']:.2f} in "
              f"(min {s['basin_min_in']:.2f}, max {s['basin_max_in']:.2f}, "
              f"n={s['n_pixels']:,} pixels)")
    rows.append(row)

# ----- CSV -----
df = pd.DataFrame(rows)
csv_out = SUMMARY_OUTPUT_DIR / "Atlas14_72hr_PDS_basin_mean.csv"
df.to_csv(csv_out, index=False)
print(f"\nWrote {csv_out}\n")
print(df.to_string(index=False))

# ----- Frequency-curve PNG -----
mpl.rcParams.update({"font.family": "Arial", "font.size": 14})
fig, ax = plt.subplots(figsize=(11, 7), constrained_layout=True)

if {"basin_mean_lower90_in", "basin_mean_upper90_in"}.issubset(df.columns):
    ax.fill_between(
        df["return_period_yr"],
        df["basin_mean_lower90_in"],
        df["basin_mean_upper90_in"],
        color="navy", alpha=0.15, label="NOAA 90% confidence interval",
    )

ax.plot(df["return_period_yr"], df["basin_mean_in"],
        color="navy", linewidth=2.0, alpha=0.7, zorder=2)
ax.scatter(df["return_period_yr"], df["basin_mean_in"],
           color="navy", s=80, edgecolor="black", linewidth=0.5,
           zorder=3, label="Basin-mean (Atlas 14 PDS)")

ax.set_xscale("log")
xticks = list(RETURN_PERIODS)
ax.set_xticks(xticks)
ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{int(v)}"))
ax.xaxis.set_minor_locator(mticker.NullLocator())
ax.set_xlim(min(xticks) * 0.9, max(xticks) * 1.1)
ax.set_xlabel("Return period (yr)")
ax.set_ylabel(f"{DURATION_DAYS*24}-hr basin-mean precipitation (in)")

ax2 = ax.secondary_yaxis("right",
                         functions=(lambda y: y * 25.4, lambda y: y / 25.4))
ax2.set_ylabel("(mm)")

ax.grid(True, which="major", alpha=0.3)
ax.set_title(f"NOAA Atlas 14 PDS: {DURATION_DAYS*24}-hr basin-mean precipitation, "
             f"{WATERSHED_LABEL}", fontsize=14)
ax.legend(loc="lower right", fontsize=11)

png_out = SUMMARY_OUTPUT_DIR / "Atlas14_72hr_PDS_frequency_curve.png"
fig.savefig(png_out, dpi=200, bbox_inches="tight", facecolor="white")
print(f"\nWrote {png_out}")
