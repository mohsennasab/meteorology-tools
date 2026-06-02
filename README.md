# Storm Transposition Domain Tools

Reproducible Jupyter notebooks for **stochastic storm-transposition (SST) domain selection** and
**storm-catalog quality control**. The notebooks are watershed-agnostic. Point them at any basin,
transposition domain, and storm catalog. They ship with a small example dataset so they run end-to-end
out of the box, locally or in your browser on [mybinder.org](https://mybinder.org).

[![Binder](https://mybinder.org/badge_logo.svg)](https://mybinder.org/v2/gh/mohsennasab/meteorology-tools/main?labpath=notebooks)

---

## Quick start

**Run in your browser (no install).** Click the Binder badge above. `environment.yml` is detected
automatically. **Heads-up: the first few times the repo is opened on Binder, it takes ~5 minutes to
build the project image** before the notebooks open. After that, launches reuse the cached image and
start in seconds.

**Run locally.**

```bash
git clone https://github.com/mohsennasab/meteorology-tools.git
cd meteorology-tools
conda env create -f environment.yml
conda activate meteorology-tools
jupyter lab
```

Then open either notebook in `notebooks/` and run all cells.

---

## Notebooks

| Notebook | What it does | Data it uses |
|---|---|---|
| [`notebooks/three_stage_sop_domain_selection.ipynb`](notebooks/three_stage_sop_domain_selection.ipynb) | A **three-stage, SOP-driven domain-selection methodology**. Stage 1 hard-filters candidate transposition domains on Area Ratio (AR > 5); Stage 2 supports a meteorologist's visual review against the basin's PRISM precipitation / elevation / dewpoint envelope; Stage 3 produces descriptive QA statistics for the selected polygon. Ends with CONUS-extent PRISM maps overlaid with the domain boundaries. | Candidate transposition-domain shapefiles, basin polygon, PRISM 30-yr normals (precipitation, dewpoint, DEM). |
| [`notebooks/storm_catalog_maps.ipynb`](notebooks/storm_catalog_maps.ipynb) | A **configurable storm-catalog visualization & QC framework**. Produces a max-pixel plausibility flag, a storms-per-year bar chart, magnitude- and season-faceted max-precipitation maps, a hex-bin density heatmap, an event calendar, and a statistical distribution / max-to-mean ratio QC section. | A storm catalog's `max_precip_locations.geojson`, the watershed polygon, and the transposition-domain polygon. |

Both notebooks resolve their data paths automatically by walking up from the working directory to find
`example_data/`, so they run unchanged whether launched from the repository root or from inside
`notebooks/` (as Binder does). To run them on **your own** watershed / domain / catalog, edit the few
path variables in each notebook's **setup cell** near the top.

---

## Repository layout

```
.
├── notebooks/                 # the two runnable notebooks
├── example_data/              # everything the notebooks read (see "Data" below)
│   ├── watershed/             # basin polygons
│   ├── transposition_domains/ # transposition-domain shapefiles + valid-domain polygons
│   ├── storm_catalog/         # storm-catalog max-precip locations
│   └── prism/                 # PRISM 30-yr normals (annual precip, dewpoint, 800 m DEM)
├── documents/                 # supporting write-ups (add as needed)
├── outputs/                   # figures/tables written at run time (git-ignored)
├── environment.yml            # conda environment for local use + Binder (repo2docker)
└── README.md
```

---

## Requirements

A Python 3.11 scientific-geospatial stack, most easily installed with the bundled `environment.yml`
(see [Quick start](#quick-start)). Key libraries: `geopandas`, `rasterio`, `shapely`, `pyproj`,
`scipy`, `matplotlib`, `matplotlib-scalebar`, `contextily`, `mapclassify`, `jupyterlab`.

**Tested with:** Python 3.11, numpy 2.4, pandas 3.0, geopandas 1.1, rasterio 1.4, shapely 2.1,
scipy 1.17, matplotlib 3.10, contextily 1.7, mapclassify 2.10.

> **Internet note:** the maps use [`contextily`](https://contextily.readthedocs.io) to fetch
> OpenStreetMap / CartoDB basemap tiles at run time, so those cells need network access (Binder has it).
> Everything else runs fully offline against `example_data/`.

---

## Data

The notebooks ship with a complete example dataset under `example_data/` so they run immediately. Swap
in your own files (and update the path variables in each notebook's setup cell) to analyze a different
basin.

| Folder | Files | Source |
|---|---|---|
| `watershed/` | `Upper-Tennessee_huc04.geojson`, `UpperTennessee.json` | USGS Watershed Boundary Dataset. |
| `transposition_domains/` | `TD.AORC.0601.SLAM-SIG.{24,72}hr.2024.v1.*` shapefiles; `SLAM-SIG-GSL0-TD_valid.json`; `SLAM-SIG-GSL0-Intersection-TD_valid.json` | SLAM-SIG 2024 v1 transposition domains (Dewberry). |
| `storm_catalog/` | `max_precip_locations.geojson` | A 72-hr storm event catalog (wettest AORC pixel per storm). |
| `prism/annual/` | `prism_ppt_us_30s_2020_avg_30y.tif`, `prism_tdmean_us_30s_2020_avg_30y.tif` | [PRISM](https://prism.oregonstate.edu) 30-yr normals (1991–2020), CONUS. **Bundled copies are downsampled to ~5 km** (see note below). |
| `prism/dem/` | `PRISM_us_dem_800m_bil.*` | PRISM DEM, CONUS, **downsampled to ~5 km**. |

> **⚠️ PRISM resolution — read this for local use.** The bundled PRISM rasters have been **downsampled
> from their native 30-arcsec (~800 m) resolution to ~5 km** so the `three_stage_sop` notebook fits
> within [mybinder.org](https://mybinder.org)'s ~2 GB memory limit and the repository stays small. The
> methodology and the CONUS-extent maps are unchanged — only the raster detail is coarser, and the QA
> numbers shift slightly. **For local or production runs, use the native 800 m PRISM 30-yr normals**
> (free from [prism.oregonstate.edu](https://prism.oregonstate.edu)): replace the bundled files in
> `example_data/prism/annual/` and `example_data/prism/dem/` with the full-resolution versions, **keeping
> the bundled file names**, and the notebook picks them up automatically. The `storm_catalog_maps`
> notebook uses no PRISM data.

---

## Coordinate-reference-system conventions

- **EPSG:4269 / 4326** — geographic (PRISM clipping / vector I/O)
- **EPSG:5070** — CONUS Albers equal-area (all area / area-ratio calculations)
- **EPSG:3857** — Web Mercator (basemap-tile alignment)

---

## Attribution

PRISM Climate Group, Oregon State University. AORC v1.1 (NOAA). SLAM-SIG transposition domains. USGS Watershed Boundary Dataset.
