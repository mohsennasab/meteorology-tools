# StormCatalog Mass Curve Generator

This script adds mass curve plots to an existing StormCatalog-style STAC catalog.

It reads each storm item JSON, fetches hourly AORC precipitation for the event window, computes:

- cumulative precipitation at the peak AORC grid cell
- cumulative watershed-mean precipitation
- hourly precipitation for both series

Then it saves a `{item_id}.mass_curve.png` file beside each item JSON and records it as the item's `mass_curve` asset.

## Folder Layout

The script expects a catalog like this:

```text
Inputs/
  Upper-Tennessee_huc04.geojson
  72hr-events/
    1/
      1.json
      1.mass_curve.png
    2/
      2.json
```

The included example under `Inputs/72hr-events` contains 10 copied event folders for testing.

## User Inputs

Open `mass_curve.py` and edit the `USER INPUTS` block near the top.

Important settings:

- `CATALOG_DIR`: folder that contains duration folders such as `72hr-events`
- `BASE_WATERSHED_PATH`: base watershed GeoJSON used by the catalog
- `DURATIONS_TO_PROCESS`: duration folders to process, for example `["72hr-events"]`
- `REGENERATE_EXISTING`: set to `True` to rewrite PNGs even if they already exist
- `PROCESS_LIMIT`: optional limit for quick tests, or `None` for all items
- `NUM_WORKERS`: number of parallel workers

After editing those values, run the script. No command-line arguments are needed.

## Environment Setup

The script needs Python plus geospatial, plotting, and StormHub libraries. The easiest setup is to use the existing `stormhub` conda environment if it is available on your machine.

### Option 1: Use Existing Conda Environment

```powershell
conda activate stormhub
python meteorology-tools/Scripts/StormCatalog_MassCurve/mass_curve.py
```

This environment should include `stormhub`, `hecdss`, `geopandas`, `xarray`, `rioxarray`, `pandas`, and `matplotlib`.

### Option 2: Create a New Environment

If you do not already have a working environment, create one:

```powershell
conda create -n masscurve python=3.11 -y
conda activate masscurve
conda install -c conda-forge geopandas xarray rioxarray pandas matplotlib s3fs zarr shapely -y
```

Then install or make available the local `stormhub` package used by this project. `stormhub` is required because the script uses it to fetch AORC Zarr data from S3.

You also need `hecdss` in the environment because `stormhub` imports it:

```powershell
python -c "import stormhub, hecdss; print('environment ready')"
```

If that command fails, use the project `stormhub` environment or install the missing project dependencies before running the script.

## Run

From the repository root:

```powershell
conda activate stormhub
python meteorology-tools/Scripts/StormCatalog_MassCurve/mass_curve.py
```

The script logs progress to the terminal. A successful run looks like:

```text
Found 10 items pending mass curve generation
Generated mass curve for item 1 (72hr)
Complete: 10 generated, 0 skipped, 0 failed
```

## Notes

- The script fetches AORC data from S3, so network access is required.
- Existing PNGs are overwritten when `REGENERATE_EXISTING = True`.
- If `REGENERATE_EXISTING = False`, items with an existing `mass_curve` asset are skipped.
- The DSS files in the item folders are not read by this script.
- Use the same base watershed GeoJSON that was used to create the catalog.

