# Storm Transposition Domain Tools

This repository contains notebooks and scripts used for storm transposition domain review, storm catalog checks, NOAA Atlas 14 basin statistics, IBTrACS screening, and storm mass curve plots.

This version is intended for local or devcontainer-based project work. It does not include all basin-specific input data needed to run the workflows end to end. Before running a notebook or script, update the file path variables near the top of that notebook or script so they point to the correct files on your local machine or inside your container.

Binder is not supported for this version because the workflows depend on local/project-specific data and external data services.

## Quick Start With Devcontainer

This repository includes a VS Code devcontainer that builds a reproducible Linux-based Python environment with micromamba. This is the recommended way to run the notebooks because the current notebooks use paths under `/workspaces/meteorology-tools/`.

Requirements:

- Git
- Docker Desktop or another Docker-compatible container runtime
- VS Code
- VS Code Dev Containers extension

On Windows, Docker-based devcontainers usually require WSL2. If VS Code reports that WSL is not installed, open PowerShell as Administrator and run:

```powershell
wsl --install
```

Restart Windows after installation, start Docker Desktop, and then reopen the project in the devcontainer. If your organization blocks WSL installation or admin elevation, the devcontainer workflow may not be available on that machine; use the local Conda setup below instead.

Clone the repository:

```bash
git clone https://github.com/mohsennasab/meteorology-tools.git
cd meteorology-tools
```

Open the repository folder in VS Code. When prompted, choose **Reopen in Container**. You can also use the command palette:

```text
Dev Containers: Reopen in Container
```

The container creates and activates the `mtools-base` environment from `environment.yml`. After the container finishes building, add or mount the project-specific input files expected by your workflow, then update the path variables near the top of each notebook or script.

Inside the devcontainer, common paths are:

```text
/workspaces/meteorology-tools/inputs/watershed/
/workspaces/meteorology-tools/inputs/transposition_domains/
/workspaces/meteorology-tools/inputs/storm_catalog/
/workspaces/meteorology-tools/inputs/conus/
/workspaces/meteorology-tools/inputs/prism/
/workspaces/meteorology-tools/outputs/
```

In VS Code, select the container Python interpreter and notebook kernel:

```text
Python: Select Interpreter
/opt/conda/envs/mtools-base/bin/python
```

For notebooks, use the kernel picker and select the same `mtools-base` interpreter. You can verify the active environment from the VS Code terminal:

```bash
which python
python -m pip --version
python -c "import geopandas, shapely, pyproj; print(geopandas.__version__, shapely.__version__, pyproj.__version__)"
ls $CONDA_PREFIX/share/proj/proj.db
```

Expected geospatial versions for this devcontainer are `geopandas >= 1.0.1`, `shapely >= 2.1.2`, and `pyproj >= 3.7`. If `shapely` reports an older pip-installed version, remove it and restore the conda-forge package:

```bash
python -m pip show shapely
python -m pip uninstall -y shapely
micromamba install -n mtools-base -c conda-forge --force-reinstall shapely=2.1.2
```

If your micromamba build does not accept `--force-reinstall`, use:

```bash
micromamba remove -n mtools-base shapely
micromamba install -n mtools-base -c conda-forge shapely=2.1.2
```

Then restart the notebook kernel and rerun from the top. This avoids `GeoSeries.union_all()` errors caused by pip Shapely overriding conda-forge Shapely.

Start Jupyter Lab from the VS Code terminal if needed:

```bash
jupyter lab --ip 0.0.0.0 --no-browser
```

Then open and run the notebooks in `notebooks/`.

## Local Conda Setup

If you are not using the devcontainer, create the Conda environment locally:

```bash
conda env create -f environment.yml
conda activate meteorology-tools
jupyter lab
```

When running locally, replace any `/workspaces/meteorology-tools/...` paths in the notebooks or scripts with paths that exist on your machine.

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

Run scripts from the repository root so relative paths and output folders are easier to manage:

```bash
python Scripts/Atlas14_Pipeline/atlas14_pipeline.py
python Scripts/StormCatalog_MassCurve/mass_curve.py
```

The mass curve script requires project-specific packages such as `stormhub` and `hecdss`. If those packages are not available in `meteorology-tools` or `mtools-base`, run that script from an environment where they are installed.

## Input Data

The repository keeps an `inputs/` folder structure as a template, but most project-specific data files must be supplied by the user.

Expected input categories include:

- `inputs/watershed/`: watershed polygon files, such as GeoJSON.
- `inputs/transposition_domains/`: SLAM-SIG or other transposition domain shapefiles.
- `inputs/storm_catalog/`: storm catalog outputs, ranked storm files, max precipitation locations, and valid-domain files.
- `inputs/conus/`: CONUS boundary data used by some map workflows.
- `inputs/IBTrACS_Lines/`: local IBTrACS shapefile/netCDF files if not downloading them during notebook execution.
- `inputs/prism/`: PRISM precipitation, dewpoint, and DEM rasters used by the domain review notebook.

Some PRISM files are included under `inputs/prism/`, but basin-specific watershed, transposition domain, and storm catalog files are not generally included.

The current notebook/script defaults are configured for the Upper Tennessee example and expect these local files unless you edit the setup cells:

```text
inputs/watershed/Upper-Tennessee_huc04.geojson
inputs/transposition_domains/TD.AORC.0601.SLAM-SIG.24hr.2024.v1.shp
inputs/transposition_domains/TD.AORC.0601.SLAM-SIG.72hr.2024.v1.shp
inputs/storm_catalog/SLAM-SIG-GSL0-Intersection-transpo_valid.json
inputs/storm_catalog/max_precip_locations.geojson
inputs/storm_catalog/ranked-storms.csv
inputs/conus/cb_2018_us_nation_20m/cb_2018_us_nation_20m.shp
inputs/prism/annual/prism_ppt_us_30s_2020_avg_30y.tif
inputs/prism/annual/prism_tdmean_us_30s_2020_avg_30y.tif
inputs/prism/dem/PRISM_us_dem_800m_bil.bil
```

## Network And Data Access

Several workflows require internet or external data access:

- NOAA Atlas 14 pipeline: requires access to NOAA HDSC Atlas 14 download endpoints.
- AORC mass curve generation: requires access to AORC data on S3 and the project-specific `stormhub` dependencies.
- IBTrACS screening: may download NOAA/NCEI IBTrACS shapefile and NetCDF data if local copies are not present.
- Basemap plotting: map basemap cells use `contextily` and may need internet access for OpenStreetMap tiles.

If your organization blocks one of these services, download the required data separately and update the local paths in the notebooks/scripts.

The notebooks use OpenStreetMap Mapnik with a fixed low zoom to reduce tile downloads while keeping major labels:

```python
OSM_BASEMAP_SOURCE = cx.providers.OpenStreetMap.Mapnik
OSM_BASEMAP_ZOOM = 6
```

If tile downloads are slow or blocked by corporate networking, the geospatial calculations still work; only the basemap layer is affected. Use an approved company proxy or ask IT to allow the relevant tile-host domains rather than bypassing network controls.

## Requirements

For local notebooks and most geospatial workflows:

```bash
conda env create -f environment.yml
conda activate meteorology-tools
```

Key libraries include `geopandas`, `rasterio`, `shapely`, `pyproj`, `scipy`, `matplotlib`, `contextily`, `mapclassify`, `xarray`, `rioxarray`, and `jupyterlab`.

Some script workflows need additional project environments. For example, `Scripts/StormCatalog_MassCurve/mass_curve.py` should be run in an environment that includes `stormhub` and `hecdss` if those packages are not available in the devcontainer/local environment:

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
