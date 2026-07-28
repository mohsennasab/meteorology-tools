# NOAA Atlas 14 Tools

This folder contains two separate Atlas 14 workflows. They have different
purposes and products:

| Tool | Purpose | Is the output an HEC-HMS bias grid? |
|---|---|---|
| `atlas14_watershed_frequency_summary.py` | Characterize the range of Atlas 14 precipitation-frequency values within a watershed. | No |
| `atlas14_hms_bias_grid.py` | Create the 72-hour, 100-year Atlas 14 precipitation field for use as the HEC-HMS Bias Grid across the complete transposition domain. | Yes |

Use the first tool for watershed statistics and the second to create the
HEC-HMS input specified by the December 2025 FFRD SOP. Do not use the
watershed statistics as the HEC-HMS Bias Grid.

## 1. Watershed precipitation-frequency summary

### Purpose

`atlas14_watershed_frequency_summary.py` describes the magnitude and range of
Atlas 14 precipitation-frequency estimates within a watershed. It supports
review of watershed precipitation depths and creation of a basin-average
frequency curve.

The tool clips each Atlas 14 mosaic to the watershed **in memory** to calculate
the watershed mean, minimum, maximum, and valid-pixel count. It does not save a
watershed-clipped GeoTIFF and it does not create an HEC-HMS Bias Grid.

### Processing

1. Download Atlas 14 best-estimate and 90-percent confidence-bound ZIP files.
2. Extract the NOAA ASCII rasters.
3. Write CRS-consistent GeoTIFF copies tagged as NAD83 (`EPSG:4269`) so rasters
   from different NOAA volumes can be combined.
4. Mosaic the configured NOAA volumes using a first-wins rule with no
   resampling or averaging.
5. Clip each mosaic to the watershed in memory.
6. Convert NOAA's stored values from inches times 1,000 to inches for the
   watershed statistics.
7. Write the watershed-statistics CSV and frequency-curve PNG.

The CRS step only makes the metadata consistent between Atlas 14 volumes. It
does not adjust precipitation values.

### Inputs

- Default watershed polygon:
  `inputs/watershed/Upper-Tennessee_huc04.geojson`
- Atlas 14 data/cache directory for downloaded ZIP files, extracted rasters,
  and mosaics. Existing files are reused without rebuilding the mosaics.
- Internet access to NOAA's HDSC Atlas 14 download server
- Duration, return periods, and NOAA volumes configured near the top of the
  script

Current defaults:

- Duration: 3 days (72 hours)
- Return periods: 1, 2, 5, 10, 25, 50, and 100 years
- Atlas 14 volumes: Ohio River Basin (`orb`) followed by Southeast (`se`)
- Volume priority: `orb` wins where the two source rasters overlap

### Outputs

The output directory contains:

- `Atlas14_72hr_PDS_basin_mean.csv`
- `Atlas14_72hr_PDS_frequency_curve.png`
- downloaded ZIP files;
- extracted NOAA ASCII rasters;
- CRS-consistent GeoTIFF intermediates; and
- full Atlas 14 mosaics such as `mosaic_100yr03da.tif`.

The full mosaics retain NOAA's stored integer values (inches times 1,000).
Only the in-memory values used for statistics are converted to inches.

### Run

```bash
python Scripts/Atlas14_Pipeline/atlas14_watershed_frequency_summary.py
```

Use explicit paths to keep cached Atlas 14 data separate from the final
reporting products:

```powershell
python Scripts/Atlas14_Pipeline/atlas14_watershed_frequency_summary.py `
  --watershed-path "C:\path\to\watershed.geojson" `
  --atlas14-data-dir "C:\path\to\atlas14-cache" `
  --output-dir "C:\path\to\results"
```

The duration, return periods, NOAA volumes, and watershed label remain in the
configuration block near the top of the file.

## 2. HEC-HMS Atlas 14 bias-grid builder

### Purpose and SOP basis

`atlas14_hms_bias_grid.py` creates the precipitation field that is imported
into HEC-HMS as a **Precipitation-Normal Grid** and selected in the
Meteorologic Model's **Bias Grid** option.

The December 2025 FFRD SOP specifies:

- NOAA Atlas 14 as the preferred source where it covers the domain;
- the annual-maximum-series, 3-day/72-hour, 1/100-AEP (100-year) field;
- coverage of the complete transposition domain;
- mosaicking when the domain spans multiple Atlas 14 volumes; and
- GeoTIFF output.

References:

- FFRD SOP Volume II, Section 5.7.1
- FFRD SOP Job Aid 3, Section 1.4

### Inputs

The default inputs are:

- Atlas 14 best-estimate mosaic:
  `.../atlas14/mosaic_100yr03da.tif`
- Transposition domain:
  `.../SLAM_SIG_GSL0_24_72hr_Intersect.geojson`
- Watershed polygon used for the review-map outline:
  `.../Upper-Tennessee_huc04.geojson`

The Atlas 14 mosaic is an output from
`atlas14_watershed_frequency_summary.py`. The bias-grid tool reads the mosaic
and the two polygon files without modifying them.

### Processing

1. Confirm that the source raster covers the full transposition domain.
2. Read a padded source window around the complete domain.
3. Convert NOAA's stored values from inches times 1,000 to float32 inches.
4. Project the continuous precipitation-frequency surface to the standard FFRD
   NAD83 Albers Equal Area CRS in feet.
5. Mask the output to the transposition-domain polygon using all touched edge
   cells.
6. Verify that no NoData cells occur inside the transposition domain.
7. Write a 600-DPI PNG review map using an Arial 14-point, normal-weight title,
   the viridis color scale, black watershed outlines, and a dashed
   transposition-domain outline for context.
8. Write the compressed GeoTIFF and a JSON audit record containing input
   hashes, projection, grid shape, pixel size, and precipitation statistics.

The default reprojection uses bilinear resampling because the Atlas 14 field is
a continuous precipitation-frequency surface. Use `--resampling nearest` if
the project requires source-cell values without interpolation.

### Outputs

Default output directory:

`outputs/na14/hms_bias_grid`

Files:

- `atlas14_72hr_100yr_hms_bias_grid.tif`
- `atlas14_72hr_100yr_hms_bias_grid.png`
- `atlas14_72hr_100yr_hms_bias_grid.json`

The GeoTIFF contains precipitation depth in inches, uses float32 values, and
stores `-9999` as NoData outside the domain. The PNG is a 600-DPI review and
presentation map; it is not an HEC-HMS input. The JSON file provides processing
and traceability information, including the watershed and PNG hashes; it is
also not an HEC-HMS input.

### Run with defaults

From the `meteorology-tools` repository root:

```bash
python Scripts/Atlas14_Pipeline/atlas14_hms_bias_grid.py
```

### Run with explicit paths

```powershell
python Scripts/Atlas14_Pipeline/atlas14_hms_bias_grid.py `
  --source-raster "C:\path\to\mosaic_100yr03da.tif" `
  --transposition-domain "C:\path\to\transposition-domain.geojson" `
  --watershed-path "C:\path\to\watershed.geojson" `
  --output-dir "C:\path\to\output"
```

The tool refuses to replace an existing GeoTIFF, PNG, or audit record unless
`--overwrite` is supplied. The PNG is 600 DPI by default. Use `--plot-dpi`
when the project requires another resolution.

### HEC-HMS use

After technical review of the GeoTIFF:

1. Create/import it as a **Precipitation-Normal Grid** in the HEC-HMS project.
2. Open the Meteorologic Model that uses the Gridded Precipitation method.
3. Select the imported grid in the **Bias Grid** option.

## Python requirements

Both tools use packages already listed in the repository environment:

- `geopandas`
- `rasterio`
- `numpy`
- `matplotlib`
- `pandas` for the watershed summary tool
