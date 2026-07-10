# Storm Transposition Domain Tools

This repository contains notebooks and scripts used for storm transposition domain review, storm catalog checks, Atlas 14 basin statistics, and storm mass curve plots.

The notebooks include a small example dataset so they can run on Binder or on a local machine. The scripts in `Scripts/` are standalone workflows. Some of them need local paths, a conda environment, and network access.

[![Binder](https://mybinder.org/badge_logo.svg)](https://mybinder.org/v2/gh/mohsennasab/meteorology-tools/main?labpath=notebooks)

## Quick Start

Run in your browser:

Click the Binder badge above. The first launch can take a few minutes while Binder builds the environment. Later launches are usually faster.

Run locally:

```bash
git clone https://github.com/mohsennasab/meteorology-tools.git
cd meteorology-tools
conda env create -f environment.yml
conda activate meteorology-tools
jupyter lab
```

Then open a notebook in `notebooks/` and run the cells.

## Notebooks

| Notebook | Purpose |
|---|---|
| `notebooks/three_stage_sop_domain_selection.ipynb` | Reviews candidate transposition domains using area ratio, PRISM precipitation, elevation, dewpoint, and map-based checks. |
| `notebooks/storm_catalog_maps.ipynb` | Creates storm catalog maps and QC plots, including max precipitation locations, seasonal views, density maps, calendars, and distribution checks. |
| `notebooks/ibtracs_td_screening.ipynb` | Screens IBTrACS tropical cyclone tracks against a transposition domain and maps the clipped tracks. |

To use your own basin, domain, or catalog, edit the path variables in the setup cell near the top of each notebook.

## Scripts

The `Scripts/` folder contains standalone workflows with their own README files.

| Folder | Contents |
|---|---|
| `Scripts/Atlas14_Pipeline/` | Downloads NOAA Atlas 14 rasters, mosaics selected volumes, clips to a watershed, computes basin statistics, and writes a frequency curve. |
| `Scripts/StormCatalog_MassCurve/` | Reads a storm catalog, fetches AORC precipitation, and regenerates mass curve PNGs for storm items. |

See each folder's `README.md` before running the scripts. The mass curve script should be run in the `stormhub` conda environment because it needs project-specific libraries such as `stormhub` and `hecdss`.

## Repository Layout

```text
meteorology-tools/
  notebooks/
  example_data/
  documents/
  outputs/
  Scripts/
    Atlas14_Pipeline/
      atlas14_pipeline.py
      README.md
    StormCatalog_MassCurve/
      mass_curve.py
      README.md
      Inputs/
  environment.yml
  README.md
```

## Example Data

The notebooks use the example data under `example_data/`.

Main folders:

- `watershed/`: basin polygons
- `transposition_domains/`: transposition domain shapefiles and valid-domain polygons
- `storm_catalog/`: storm catalog max precipitation locations
- `IBTrACS_Lines/`: IBTrACS line tracks when available locally
- `prism/`: PRISM precipitation, dewpoint, and DEM rasters used by the domain review notebook

Some PRISM files are downsampled so the notebooks run more easily on Binder. For local production work, replace them with the native-resolution PRISM rasters while keeping the same filenames.

## Requirements

For notebooks, use:

```bash
conda env create -f environment.yml
conda activate meteorology-tools
```

Key notebook libraries include `geopandas`, `rasterio`, `shapely`, `pyproj`, `scipy`, `matplotlib`, `contextily`, `mapclassify`, and `jupyterlab`.

Some script workflows need additional project environments. For example, `Scripts/StormCatalog_MassCurve/mass_curve.py` should be run with:

```bash
conda activate stormhub
python Scripts/StormCatalog_MassCurve/mass_curve.py
```

## Notes

- Map basemaps use `contextily`, so those cells need internet access.
- Atlas 14 downloads require internet access to NOAA.
- Mass curve generation requires internet access to AORC data on S3.
- Outputs created by notebooks are written to `outputs/`.
- Script outputs are written in the folders configured inside each script.

## Attribution

Data sources include PRISM Climate Group, NOAA AORC, NOAA Atlas 14, NOAA/NCEI IBTrACS, USGS Watershed Boundary Dataset, and SLAM-SIG transposition domains.
