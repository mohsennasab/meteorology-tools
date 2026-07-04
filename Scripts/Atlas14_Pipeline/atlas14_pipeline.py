"""
NOAA Atlas 14 PDS pipeline - download, mosaic, clip, basin-mean, frequency curve.

Pipeline for any HUC and any combination of NOAA Atlas 14 volumes:
  1. Download Atlas 14 PDS rasters (best estimate + 90% lower/upper CI) for each
     return period × volume × duration combination.
  2. Mosaic across volumes per (T, suffix) using rasterio.merge in "first wins"
     mode (no resampling, no value averaging).
  3. Clip the mosaic to the watershed using rasterio.mask (no resampling).
  4. Compute basin-mean / min / max / pixel count, converting raw raster values
     from inches × 1000 (NOAA storage convention) to inches at the reporting step.
  5. Save a CSV with all return periods and a log-x frequency-curve PNG with the
     90% CI as a shaded ribbon.

CRS handling: Atlas 14 grids ship in NAD83 (EPSG:4269); the watershed is
reprojected to the source raster CRS before clipping (rasterio.mask.mask is the
clip primitive - no resampling). No pixel values are altered anywhere.

NOAA HDSC volume codes (lowercase): orb=Vol2 (Ohio River Basin), se=Vol9
(Southeast), mw=Vol8 (Midwest), tx=Vol11 (Texas), ne=Vol10 (Northeast).
"""

# ============================ USER CONFIG ============================
RETURN_PERIODS = [1, 2, 5, 10, 25, 50, 100]   # years
DURATION_DAYS  = 3                              # 3-day = 72-hr; Atlas 14 code "03da"
VOLUMES        = ["orb", "se"]                  # volumes to mosaic, in priority order
WATERSHED_PATH = (
    r"C:/OneDrive/OneDrive - AECOM/FFRD/Validation Basin/Transposition Domain/"
    r"Comparison/Data/Watershed/Upper-Tennessee_huc04.geojson"
)
OUTPUT_DIR     = (
    r"C:/OneDrive/OneDrive - AECOM/FFRD/Validation Basin/Transposition Domain/"
    r"Comparison/Data/atlas14"
)
WATERSHED_LABEL = "Upper Tennessee (HUC 0601)"
# =====================================================================

# matplotlib MUST be imported before rasterio in the slamcomp env (DLL conflict on libexpat)
import matplotlib as mpl
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker

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


WATERSHED_PATH = Path(WATERSHED_PATH)
OUTPUT_DIR     = Path(OUTPUT_DIR)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

NOAA_BASE_URL  = "https://hdsc.nws.noaa.gov/pub/hdsc/data"
DUR_CODE       = f"{DURATION_DAYS:02d}da"
SUFFIXES       = {"": "best estimate", "u": "upper 90% CI", "l": "lower 90% CI"}


def asset_filename(vol: str, T: int, suffix: str) -> str:
    return f"{vol}{T}yr{DUR_CODE}{suffix}.zip"


def download_one(vol: str, T: int, suffix: str):
    """Download a single Atlas 14 zip; cache by file presence. Returns Path or None."""
    fname = asset_filename(vol, T, suffix)
    url   = f"{NOAA_BASE_URL}/{vol}/{fname}"
    out   = OUTPUT_DIR / fname
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
    """Extract zip if needed; return the .asc file inside (or None)."""
    extract_dir = zip_path.with_suffix("")
    extract_dir.mkdir(parents=True, exist_ok=True)
    asc = list(extract_dir.glob("*.asc"))
    if asc:
        return asc[0]
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(extract_dir)
    asc = list(extract_dir.glob("*.asc"))
    return asc[0] if asc else None


def normalize_crs(asc_file: Path, target_crs="EPSG:4269") -> Path:
    """Rewrite an ASC to a GeoTIFF with an explicit CRS so volumes mosaic cleanly.
    NOAA's .prj strings differ across volumes (orb vs se) even though both are
    physically NAD83. This function only swaps the CRS declaration - pixel grid,
    pixel values, transform, and nodata are copied byte-for-byte from the source."""
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
    """Mosaic source rasters with method='first' (preserves NOAA values exactly).
    All sources are first normalized to EPSG:4269 GeoTIFF so the merge succeeds
    regardless of cross-volume .prj differences. No values are altered."""
    normalized = [normalize_crs(f) for f in asc_files]
    sources = [rasterio.open(f) for f in normalized]
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
    """Clip mosaic to watershed (no resampling) and compute basin-mean / min / max."""
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
    """Download + extract + mosaic + clip + stats for one (T, suffix)."""
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
    mosaic_tif = OUTPUT_DIR / f"mosaic_{T}yr{DUR_CODE}{suffix}.tif"
    mosaic_and_save(asc_files, mosaic_tif)
    return basin_stats(mosaic_tif)


# ============================ MAIN ============================

print(f"NOAA Atlas 14 PDS pipeline — {WATERSHED_LABEL}")
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
csv_out = OUTPUT_DIR / "Atlas14_72hr_PDS_basin_mean.csv"
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
ax.set_title(f"NOAA Atlas 14 PDS — {DURATION_DAYS*24}-hr basin-mean precipitation, "
             f"{WATERSHED_LABEL}", fontsize=14)
ax.legend(loc="lower right", fontsize=11)

png_out = OUTPUT_DIR / "Atlas14_72hr_PDS_frequency_curve.png"
fig.savefig(png_out, dpi=200, bbox_inches="tight", facecolor="white")
print(f"\nWrote {png_out}")
