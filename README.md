# Storm Transposition Domain Tools

This repository contains notebooks and scripts used for storm transposition domain review, storm catalog checks, NOAA Atlas 14 basin statistics, IBTrACS screening, and storm mass curve plots.

This version is intended for local or devcontainer-based project work. It does not include all basin-specific input data needed to run the workflows end to end. Before running a notebook or script, update the file path variables near the top of that notebook or script so they point to the correct files on your local machine or inside your container.

Binder is not supported for this version because the workflows depend on local/project-specific data and external data services.

## Quick Start

Clone the repository and create the environment:

```bash
git clone https://github.com/mohsennasab/meteorology-tools.git
cd meteorology-tools
conda env create -f environment.yml
conda activate meteorology-tools
jupyter lab
```

Then open the notebooks in `notebooks/` and update the path variables in the setup cells before running them.

If you use VS Code, this repository also includes a `.devcontainer/` configuration. In the devcontainer, the notebooks currently expect paths under `/workspaces/meteorology-tools/`. If you run outside the devcontainer, replace those paths with paths that exist on your local machine.

## Notebooks

| Notebook | Purpose |
|---|---|
| `notebooks/mtools_01_domain_selection.ipynb` | Reviews candidate transposition domains using area ratio, PRISM precipitation, elevation, dewpoint, and map-based checks. |
| `notebooks/mtools_02_storm_catalog_maps.ipynb` | Creates storm catalog maps and QC plots, including max precipitation locations, seasonal views, density maps, calendars, and distribution checks. |
| `notebooks/mtools_03_ibtracs_screening.ipynb` | Screens IBTrACS tropical cyclone tracks against a transposition domain and cross-references storm catalog events with IBTrACS. |

Each notebook has a setup cell near the top with input and output paths such as watershed files, transposition domain shapefiles, storm catalog files, PRISM rasters, and output folders. Edit those paths before running.

## Scripts

The `Scripts/` folder contains standalone workflows with their own README files.

| Folder | Contents |
|---|---|
| `Scripts/Atlas14_Pipeline/` | Downloads NOAA Atlas 14 rasters, mosaics selected volumes, clips to a watershed, computes basin statistics, and writes a frequency curve. |
| `Scripts/StormCatalog_MassCurve/` | Reads a storm catalog, fetches AORC precipitation, and regenerates mass curve PNGs for storm items. |

The scripts also contain user-editable path/configuration blocks near the top. Update those settings before running.

## Input Data

The repository keeps an `inputs/` folder structure as a template, but most project-specific data files must be supplied by the user.

Expected input categories include:

- `inputs/watershed/`: watershed polygon files, such as GeoJSON.
- `inputs/transposition_domains/`: SLAM-SIG or other transposition domain shapefiles.
- `inputs/storm_catalog/`: storm catalog outputs, ranked storm files, max precipitation locations, and valid-domain files.
- `inputs/conus/`: CONUS boundary data used by some map workflows.
- `inputs/IBTrACS/` or similar: local IBTrACS shapefile/netCDF files if not downloading them during notebook execution.
- `inputs/prism/`: PRISM precipitation, dewpoint, and DEM rasters used by the domain review notebook.

Some PRISM files are included under `inputs/prism/`, but basin-specific watershed, transposition domain, and storm catalog files are not generally included.

## Network And Data Access

Several workflows require internet or external data access:

- NOAA Atlas 14 pipeline: requires access to NOAA HDSC Atlas 14 download endpoints.
- AORC mass curve generation: requires access to AORC data on S3 and the project-specific `stormhub` dependencies.
- IBTrACS screening: may download NOAA/NCEI IBTrACS shapefile and NetCDF data if local copies are not present.
- Basemap plotting: map basemap cells use `contextily` and may need internet access for map tiles.

If your organization blocks one of these services, download the required data separately and update the local paths in the notebooks/scripts.

## Requirements

For notebooks and most geospatial workflows:

```bash
conda env create -f environment.yml
conda activate meteorology-tools
```

Key libraries include `geopandas`, `rasterio`, `shapely`, `pyproj`, `scipy`, `matplotlib`, `contextily`, `mapclassify`, `xarray`, `rioxarray`, and `jupyterlab`.

Some script workflows need additional project environments. For example, `Scripts/StormCatalog_MassCurve/mass_curve.py` should be run in an environment that includes `stormhub` and `hecdss`:

```bash
conda activate stormhub
python Scripts/StormCatalog_MassCurve/mass_curve.py
```

## Repository Layout

```text
meteorology-tools/
  .devcontainer/
  documents/
  inputs/
    conus/
    IBTrACS_Lines/
    prism/
    storm_catalog/
    transposition_domains/
    watershed/
  notebooks/
    mtools_01_domain_selection.ipynb
    mtools_02_storm_catalog_maps.ipynb
    mtools_03_ibtracs_screening.ipynb
  outputs/
    01_domain_selection/
    02_storm_catalog_maps/
    03_ibtracs_screening/
    na14/
  Scripts/
    Atlas14_Pipeline/
    StormCatalog_MassCurve/
  environment.yml
  README.md
```

Outputs created by notebooks and scripts are written to the configured output folders. The default output folders are under `outputs/`, but you can change them in each workflow's setup/configuration block.

## Attribution

Data sources include PRISM Climate Group, NOAA AORC, NOAA Atlas 14, NOAA/NCEI IBTrACS, USGS Watershed Boundary Dataset, and SLAM-SIG transposition domains.
