# Atlas 14 Basin Mean Pipeline

This folder contains a copy of the NOAA Atlas 14 processing script used for the Upper Tennessee basin.
The script downloads Atlas 14 point precipitation surface rasters, combines the requested NOAA volumes,
clips the result to the watershed, and writes basin statistics plus a frequency-curve plot.

## What the script does

`atlas14_pipeline.py` runs the following steps:

1. Downloads Atlas 14 ZIP files for each return period, duration, and confidence-bound suffix.
2. Extracts the `.asc` raster from each ZIP and caches it on disk.
3. Rewrites the raster CRS metadata to `EPSG:4269` so rasters from different NOAA volumes can be mosaicked together.
4. Mosaics the selected volumes using a first-wins rule, which keeps NOAA values unchanged.
5. Clips the mosaic to the watershed boundary.
6. Converts the stored raster values from inches times 1000 back to inches.
7. Calculates basin mean, minimum, maximum, and valid pixel count.
8. Saves a CSV summary and a PNG frequency curve with the 90 percent confidence interval shaded.

## Current settings

The script is configured for:

- Watershed: `Data/Watershed/Upper-Tennessee_huc04.geojson`
- Output folder: `Data/atlas14`
- Duration: 3 days, which is the 72 hour Atlas 14 product
- Return periods: 1, 2, 5, 10, 25, 50, and 100 years
- NOAA volumes: `orb` and `se`

Those settings live near the top of the script and can be changed if you want to reuse the workflow for a different basin.

## Inputs

The script expects:

- A watershed polygon in GeoJSON format
- Internet access to NOAA's HDSC download server
- Raster and vector Python geospatial libraries such as `rasterio`, `geopandas`, `numpy`, `pandas`, and `matplotlib`

## Outputs

The script writes these files into `Data/atlas14`:

- `Atlas14_72hr_PDS_basin_mean.csv`
- `Atlas14_72hr_PDS_frequency_curve.png`
- Intermediate downloaded ZIP files
- Extracted `.asc` rasters
- Normalized `.tif` rasters
- Mosaicked `.tif` rasters

## How to run

Run the script from the repository root so the hardcoded paths resolve correctly:

```bash
python meteorology-tools/Scripts/atlas14_pipeline.py
```

The script starts processing immediately when it is executed. There is no separate command-line interface.

## Notes

- The script does not resample raster values.
- The watershed is reprojected to match the raster CRS before clipping.
- The `orb` volume is given priority over `se` when both cover the same area.
- If a download fails, the script prints the error and keeps moving to the next file.
