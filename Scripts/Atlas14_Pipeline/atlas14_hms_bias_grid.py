"""Create the NOAA Atlas 14 precipitation bias grid used by HEC-HMS.

The December 2025 FFRD SOP specifies the annual-maximum-series 72-hour,
1/100-AEP (100-year) Atlas 14 precipitation field as the preferred HEC-HMS
Bias Grid when Atlas 14 covers the complete transposition domain.

This tool takes the best-estimate 100-year, 3-day Atlas 14 mosaic produced by
``atlas14_watershed_frequency_summary.py``, extracts the full transposition
domain, converts NOAA's stored values to inches, projects the result to the
FFRD Albers Equal Area CRS, and writes a float32 GeoTIFF, a 600-DPI review map,
and a JSON audit record. The source Atlas 14 raster, transposition-domain file,
and watershed file are read-only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import geopandas as gpd
import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
import rasterio
from matplotlib.lines import Line2D
from matplotlib.ticker import FuncFormatter
from rasterio.crs import CRS
from rasterio.features import geometry_mask, geometry_window
from rasterio.transform import array_bounds
from rasterio.warp import Resampling, calculate_default_transform, reproject
from shapely.geometry import box, mapping


SCRIPT_DIR = Path(__file__).resolve().parent
REPOSITORY_ROOT = SCRIPT_DIR.parents[1]
COMPARISON_ROOT = SCRIPT_DIR.parents[2]

DEFAULT_SOURCE_RASTER = (
    COMPARISON_ROOT / "Data" / "atlas14" / "mosaic_100yr03da.tif"
)
DEFAULT_TRANSPOSITION_DOMAIN = (
    COMPARISON_ROOT
    / "Data"
    / "TDs"
    / "SLAM_SIG_GSL0_24_72hr_Intersect.geojson"
)
DEFAULT_WATERSHED = (
    COMPARISON_ROOT / "Data" / "Watershed" / "Upper-Tennessee_huc04.geojson"
)
DEFAULT_OUTPUT_DIR = REPOSITORY_ROOT / "outputs" / "na14" / "hms_bias_grid"
DEFAULT_OUTPUT_NAME = "atlas14_72hr_100yr_hms_bias_grid.tif"
DEFAULT_PLOT_DPI = 600

# SOP Volume II, Table 3.1: NAD83 Albers Equal Area, international feet.
FFRD_ALBERS_PROJ4 = (
    "+proj=aea +lat_0=23 +lon_0=-96 +lat_1=29.5 +lat_2=45.5 "
    "+x_0=0 +y_0=0 +datum=NAD83 +units=ft +no_defs +type=crs"
)
FFRD_ALBERS_CRS = CRS.from_string(FFRD_ALBERS_PROJ4)

OUTPUT_NODATA = -9999.0
SOP_REFERENCE = (
    "FFRD SOP December 2025, Volume II 5.7.1 and Job Aid 3 1.4"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create an HEC-HMS Atlas 14 bias-grid GeoTIFF for the complete "
            "transposition domain."
        )
    )
    parser.add_argument(
        "--source-raster",
        type=Path,
        default=DEFAULT_SOURCE_RASTER,
        help=(
            "Best-estimate Atlas 14 100-year, 3-day mosaic. Default: "
            f"{DEFAULT_SOURCE_RASTER}"
        ),
    )
    parser.add_argument(
        "--transposition-domain",
        type=Path,
        default=DEFAULT_TRANSPOSITION_DOMAIN,
        help=f"Transposition-domain polygon. Default: {DEFAULT_TRANSPOSITION_DOMAIN}",
    )
    parser.add_argument(
        "--watershed-path",
        type=Path,
        default=DEFAULT_WATERSHED,
        help=(
            "Watershed polygon used for the black boundary on the review map. "
            f"Default: {DEFAULT_WATERSHED}"
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Output directory. Default: {DEFAULT_OUTPUT_DIR}",
    )
    parser.add_argument(
        "--output-name",
        default=DEFAULT_OUTPUT_NAME,
        help=f"Output GeoTIFF filename. Default: {DEFAULT_OUTPUT_NAME}",
    )
    parser.add_argument(
        "--source-scale",
        type=float,
        default=0.001,
        help=(
            "Multiplier that converts source pixels to inches. NOAA Atlas 14 "
            "ASCII grids store inches times 1000, so the default is 0.001."
        ),
    )
    parser.add_argument(
        "--resampling",
        choices=("bilinear", "nearest"),
        default="bilinear",
        help=(
            "Resampling used only when projecting to the FFRD Albers CRS. "
            "The default uses bilinear resampling for the continuous Atlas 14 "
            "surface."
        ),
    )
    parser.add_argument(
        "--plot-dpi",
        type=int,
        default=DEFAULT_PLOT_DPI,
        help=f"Review-map resolution in dots per inch. Default: {DEFAULT_PLOT_DPI}",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Allow replacement of the GeoTIFF, PNG, and JSON audit record.",
    )
    return parser.parse_args()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def combined_geometry(frame: gpd.GeoDataFrame):
    geometry = frame.geometry
    if hasattr(geometry, "union_all"):
        return geometry.union_all()
    return geometry.unary_union


def load_polygon_layer(path: Path, label: str) -> gpd.GeoDataFrame:
    if not path.is_file():
        raise FileNotFoundError(f"{label} file not found: {path}")

    layer = gpd.read_file(path)
    if layer.empty:
        raise ValueError(f"{label} file has no features: {path}")
    if layer.crs is None:
        raise ValueError(f"{label} file does not define a CRS.")
    if layer.geometry.isna().any() or layer.geometry.is_empty.any():
        raise ValueError(f"{label} file contains null or empty geometry.")
    if not layer.geometry.is_valid.all():
        raise ValueError(f"{label} geometry is invalid.")
    if not layer.geom_type.isin(("Polygon", "MultiPolygon")).all():
        raise ValueError(f"{label} must contain polygon geometry.")
    return layer


def validate_output_name(name: str) -> None:
    path = Path(name)
    if path.name != name:
        raise ValueError("--output-name must be a filename, not a path.")
    if path.suffix.lower() not in (".tif", ".tiff"):
        raise ValueError("--output-name must use a .tif or .tiff extension.")


def check_output_targets(
    output_raster: Path, plot_path: Path, audit_path: Path, overwrite: bool
) -> None:
    existing = [
        path
        for path in (output_raster, plot_path, audit_path)
        if path.exists()
    ]
    if existing and not overwrite:
        listed = "\n  ".join(str(path) for path in existing)
        raise FileExistsError(
            "Output already exists. Use --overwrite to replace it:\n  " + listed
        )


def extract_source_window(
    source: rasterio.io.DatasetReader,
    domain_in_source_crs: gpd.GeoDataFrame,
) -> tuple[np.ndarray, Any]:
    domain_geometry = combined_geometry(domain_in_source_crs)
    source_footprint = box(*source.bounds)
    if not source_footprint.covers(domain_geometry):
        missing_area = domain_geometry.difference(source_footprint).area
        raise ValueError(
            "Atlas 14 source raster does not cover the complete transposition "
            f"domain; uncovered area in source CRS units squared: {missing_area:.6g}"
        )

    geometry = [mapping(domain_geometry)]
    window = geometry_window(
        source,
        geometry,
        pad_x=2,
        pad_y=2,
        north_up=True,
        rotated=False,
    )
    raw = source.read(1, window=window, masked=True)
    return raw, source.window_transform(window)


def convert_to_inches(raw: np.ma.MaskedArray, scale: float) -> np.ndarray:
    if not np.isfinite(scale) or scale <= 0:
        raise ValueError("--source-scale must be a positive finite number.")

    values = np.full(raw.shape, OUTPUT_NODATA, dtype=np.float32)
    valid = ~np.ma.getmaskarray(raw)
    values[valid] = raw.data[valid].astype(np.float32) * np.float32(scale)

    converted = values[valid]
    if converted.size == 0:
        raise ValueError("No valid Atlas 14 values were found in the source window.")
    if not np.isfinite(converted).all() or (converted <= 0).any():
        raise ValueError("Converted Atlas 14 precipitation contains invalid values.")
    return values


def project_and_mask(
    source_values: np.ndarray,
    source_transform: Any,
    source_crs: CRS,
    domain: gpd.GeoDataFrame,
    resampling_name: str,
) -> tuple[np.ndarray, Any, gpd.GeoDataFrame]:
    height, width = source_values.shape
    left, bottom, right, top = array_bounds(height, width, source_transform)
    destination_transform, destination_width, destination_height = (
        calculate_default_transform(
            source_crs,
            FFRD_ALBERS_CRS,
            width,
            height,
            left,
            bottom,
            right,
            top,
        )
    )

    destination = np.full(
        (destination_height, destination_width),
        OUTPUT_NODATA,
        dtype=np.float32,
    )
    resampling = (
        Resampling.bilinear
        if resampling_name == "bilinear"
        else Resampling.nearest
    )
    reproject(
        source=source_values,
        destination=destination,
        src_transform=source_transform,
        src_crs=source_crs,
        src_nodata=OUTPUT_NODATA,
        dst_transform=destination_transform,
        dst_crs=FFRD_ALBERS_CRS,
        dst_nodata=OUTPUT_NODATA,
        resampling=resampling,
    )

    domain_in_output_crs = domain.to_crs(FFRD_ALBERS_CRS)
    output_geometry = [mapping(combined_geometry(domain_in_output_crs))]
    inside_domain = geometry_mask(
        output_geometry,
        out_shape=destination.shape,
        transform=destination_transform,
        invert=True,
        all_touched=True,
    )
    destination[~inside_domain] = OUTPUT_NODATA

    missing_inside = inside_domain & (destination == OUTPUT_NODATA)
    if missing_inside.any():
        raise ValueError(
            "The projected Atlas 14 grid has NoData cells inside the "
            f"transposition domain ({int(missing_inside.sum())} cells)."
        )
    return destination, destination_transform, domain_in_output_crs


def write_bias_grid(
    output_path: Path,
    values: np.ndarray,
    transform: Any,
    source_path: Path,
    domain_path: Path,
    resampling_name: str,
) -> None:
    profile = {
        "driver": "GTiff",
        "height": values.shape[0],
        "width": values.shape[1],
        "count": 1,
        "dtype": "float32",
        "crs": FFRD_ALBERS_CRS,
        "transform": transform,
        "nodata": OUTPUT_NODATA,
        "compress": "lzw",
        "predictor": 3,
        "tiled": True,
        "BIGTIFF": "IF_SAFER",
    }
    with rasterio.open(output_path, "w", **profile) as output:
        output.write(values, 1)
        output.update_tags(
            PRODUCT="HEC-HMS precipitation bias grid",
            SOURCE="NOAA Atlas 14 precipitation-frequency estimates",
            SOURCE_RASTER=str(source_path.resolve()),
            TRANSPOSITION_DOMAIN=str(domain_path.resolve()),
            DURATION_HOURS="72",
            RETURN_PERIOD_YEARS="100",
            ANNUAL_EXCEEDANCE_PROBABILITY="0.01",
            ESTIMATE_TYPE="annual maximum series best estimate",
            UNITS="inches",
            RESAMPLING=resampling_name,
            SOP_REFERENCE=SOP_REFERENCE,
        )


def write_review_map(
    output_path: Path,
    plot_path: Path,
    domain: gpd.GeoDataFrame,
    watershed: gpd.GeoDataFrame,
    plot_dpi: int,
) -> None:
    if plot_dpi <= 0:
        raise ValueError("--plot-dpi must be a positive integer.")

    with rasterio.open(output_path) as dataset:
        values = dataset.read(1, masked=True)
        bounds = dataset.bounds
        output_crs = dataset.crs

    domain_for_plot = domain.to_crs(output_crs)
    watershed_for_plot = watershed.to_crs(output_crs)
    color_map = mpl.colormaps["viridis"].copy()
    color_map.set_bad("white")

    with mpl.rc_context({"font.family": "Arial", "font.size": 14}):
        figure, axis = plt.subplots(figsize=(10, 8), constrained_layout=True)
        image = axis.imshow(
            values,
            extent=(bounds.left, bounds.right, bounds.bottom, bounds.top),
            origin="upper",
            cmap=color_map,
            interpolation="nearest",
            zorder=1,
        )
        domain_for_plot.boundary.plot(
            ax=axis,
            color="#555555",
            linewidth=0.9,
            linestyle="--",
            zorder=2,
        )
        watershed_for_plot.boundary.plot(
            ax=axis,
            color="black",
            linewidth=1.5,
            zorder=3,
        )

        color_bar = figure.colorbar(image, ax=axis, shrink=0.86, pad=0.025)
        color_bar.set_label(
            "72-hour, 100-year precipitation depth (inches)",
            fontsize=13,
        )
        color_bar.ax.tick_params(labelsize=11)

        axis.set_title(
            "NOAA Atlas 14 72-hour, 100-year HEC-HMS bias grid",
            fontsize=14,
            fontweight="normal",
            pad=12,
        )
        axis.set_xlabel("FFRD Albers easting (million feet)", fontsize=13)
        axis.set_ylabel("FFRD Albers northing (million feet)", fontsize=13)
        million_feet = FuncFormatter(lambda value, _position: f"{value / 1e6:.1f}")
        axis.xaxis.set_major_formatter(million_feet)
        axis.yaxis.set_major_formatter(million_feet)
        axis.tick_params(labelsize=11)
        axis.grid(color="#d9d9d9", linewidth=0.5, alpha=0.55, zorder=0)
        axis.set_aspect("equal")
        axis.legend(
            handles=[
                Line2D(
                    [0],
                    [0],
                    color="black",
                    linewidth=1.5,
                    label="Watershed boundary",
                ),
                Line2D(
                    [0],
                    [0],
                    color="#555555",
                    linewidth=0.9,
                    linestyle="--",
                    label="Transposition-domain boundary",
                ),
            ],
            loc="lower left",
            fontsize=11,
            frameon=True,
            framealpha=0.95,
        )
        figure.savefig(
            plot_path,
            dpi=plot_dpi,
            bbox_inches="tight",
            facecolor="white",
            metadata={
                "Title": (
                    "NOAA Atlas 14 72-hour, 100-year HEC-HMS bias grid"
                ),
                "Description": (
                    "Bias-grid raster with watershed and transposition-domain "
                    "boundaries."
                ),
            },
        )
        plt.close(figure)


def raster_summary(
    output_path: Path,
    plot_path: Path,
    source_path: Path,
    domain_path: Path,
    watershed_path: Path,
    source_scale: float,
    resampling_name: str,
    plot_dpi: int,
    domain: gpd.GeoDataFrame,
    watershed: gpd.GeoDataFrame,
) -> dict[str, Any]:
    with rasterio.open(output_path) as dataset:
        values = dataset.read(1, masked=True)
        valid = values.compressed()
        if valid.size == 0:
            raise ValueError("Written bias grid contains no valid pixels.")
        return {
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "product": "HEC-HMS precipitation bias grid",
            "sop_reference": SOP_REFERENCE,
            "source_raster": str(source_path.resolve()),
            "source_raster_sha256": file_sha256(source_path),
            "transposition_domain": str(domain_path.resolve()),
            "transposition_domain_sha256": file_sha256(domain_path),
            "watershed": str(watershed_path.resolve()),
            "watershed_sha256": file_sha256(watershed_path),
            "output_raster": str(output_path.resolve()),
            "output_raster_sha256": file_sha256(output_path),
            "review_map_png": str(plot_path.resolve()),
            "review_map_png_sha256": file_sha256(plot_path),
            "review_map_dpi": plot_dpi,
            "source_scale_to_inches": source_scale,
            "resampling": resampling_name,
            "crs": dataset.crs.to_wkt(),
            "shape": [dataset.height, dataset.width],
            "pixel_size": [abs(dataset.transform.a), abs(dataset.transform.e)],
            "units": "inches",
            "nodata": dataset.nodata,
            "valid_pixel_count": int(valid.size),
            "minimum_in": float(valid.min()),
            "mean_in": float(valid.mean()),
            "maximum_in": float(valid.max()),
            "domain_feature_count": int(len(domain)),
            "domain_bounds_source_crs": [
                float(value) for value in domain.total_bounds
            ],
            "watershed_feature_count": int(len(watershed)),
            "watershed_bounds_source_crs": [
                float(value) for value in watershed.total_bounds
            ],
        }


def main() -> int:
    args = parse_args()
    validate_output_name(args.output_name)

    source_path = args.source_raster.resolve()
    domain_path = args.transposition_domain.resolve()
    watershed_path = args.watershed_path.resolve()
    output_dir = args.output_dir.resolve()
    output_path = output_dir / args.output_name
    plot_path = output_path.with_suffix(".png")
    audit_path = output_path.with_suffix(".json")

    if not source_path.is_file():
        raise FileNotFoundError(f"Atlas 14 source raster not found: {source_path}")
    domain = load_polygon_layer(domain_path, "Transposition domain")
    watershed = load_polygon_layer(watershed_path, "Watershed")
    check_output_targets(
        output_path, plot_path, audit_path, args.overwrite
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    with rasterio.open(source_path) as source:
        if source.crs is None:
            raise ValueError("Atlas 14 source raster does not define a CRS.")
        domain_in_source_crs = domain.to_crs(source.crs)
        raw, source_transform = extract_source_window(
            source, domain_in_source_crs
        )
        source_values = convert_to_inches(raw, args.source_scale)
        bias_grid, output_transform, _domain_output = project_and_mask(
            source_values,
            source_transform,
            source.crs,
            domain,
            args.resampling,
        )

    write_bias_grid(
        output_path,
        bias_grid,
        output_transform,
        source_path,
        domain_path,
        args.resampling,
    )
    write_review_map(
        output_path,
        plot_path,
        domain,
        watershed,
        args.plot_dpi,
    )
    summary = raster_summary(
        output_path,
        plot_path,
        source_path,
        domain_path,
        watershed_path,
        args.source_scale,
        args.resampling,
        args.plot_dpi,
        domain,
        watershed,
    )
    audit_path.write_text(
        json.dumps(summary, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"Bias grid: {output_path}")
    print(f"Review map ({args.plot_dpi} DPI): {plot_path}")
    print(f"Audit record: {audit_path}")
    print(
        "Valid cells: {valid_pixel_count:,}; range: {minimum_in:.3f} to "
        "{maximum_in:.3f} inches; mean: {mean_in:.3f} inches".format(**summary)
    )
    print("HEC-HMS: import as a Precipitation-Normal Grid and select it as Bias Grid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
