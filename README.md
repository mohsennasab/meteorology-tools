# FFRD Meteorology Tools

The goal of this repository is to create consistency among teams that are reproducing the FFRD meteorology procedure. It provides a common set of notebooks, scripts, review products, and file conventions for transposition-domain selection, storm-catalog review, storm classification, seasonality, and HEC-HMS data preparation.

These are living tools. They should not be treated as final tools or as a replacement for the current FFRD SOP, project decisions, or meteorologist review. Methods and file requirements may change as the SOP and team practices are updated. Record the repository commit used for each project run.

## What this repository covers

The repository supports these parts of the workflow:

- selection and review of a SLAM-SIG transposition domain
- NOAA Atlas 14 watershed statistics and HEC-HMS bias-grid preparation
- checks between a StormHub ranked CSV and event JSON files
- storm-catalog maps and precipitation quality-control plots
- AORC storm mass curves
- IBTrACS track screening
- automatic TC and NT classification followed by expert review
- seasonality distributions based on verified classifications
- DSS copying, standard naming, and HEC-HMS `.grid` file generation

StormHub catalog creation is an input to this repository. It is not performed by one of the notebooks or scripts listed here. HEC-HMS review and import are also completed outside this repository.

## Before starting

1. Obtain the current FFRD meteorology SOP and project naming decisions.
2. Create a basin-specific working folder for source data, intermediate files, results, and review records.
3. Keep original watershed, transposition-domain, StormHub, PRISM, Atlas 14, AORC, and IBTrACS data unchanged.
4. Choose the devcontainer or local Conda environment described below.
5. Update the input and output paths near the top of each notebook or script.
6. Run a small test when the tool provides a limit or test option.
7. Save the executed notebook, logs, tables, figures, and reviewer decisions with the project results.

Most basin-specific data are not stored in Git. The `inputs/` folders show the expected data groups.

## Recommended process

The following order shows the full workflow and its review points. NOAA Atlas 14 work can proceed after the selected transposition domain is approved. It does not need to wait for the StormHub catalog.

### 1. Select the transposition domain

Run [mtools_01_domain_selection.ipynb](notebooks/mtools_01_domain_selection.ipynb).

Provide:

- watershed polygon
- 24-hour and 72-hour SLAM-SIG domain files
- PRISM annual precipitation raster
- PRISM mean dewpoint raster
- PRISM elevation raster
- output folder

The notebook:

1. Calculates the area ratio for candidate GSL domains.
2. Removes candidates with an area ratio at or below 5.
3. Compares the remaining domains with the watershed PRISM precipitation, dewpoint, and elevation ranges.
4. Calculates the Tcap screening value.
5. Performs statistical checks for the selected domain.
6. Saves the selected domain in EPSG:5070.

Main products include:

- `stage1_area_ratio.csv`
- `stage1_survivors_map.png`
- `stage2_envelope_match.csv`
- `stage2_envelope_coverage.png`
- `stage2_tcap.csv`
- `stage2_gsl_vs_similarity.png`
- `stage3_climate_stats.csv`
- `stage3_distributional_comparison.csv`
- `SEL_SLAM-SIG-GSL###_EPSG5070.geojson`
- PRISM review maps

The notebook contains an example `selected_gsl` value. A meteorologist must review the Stage 1 and Stage 2 results and set the project value before saving the selected domain.

### 2. Prepare NOAA Atlas 14 products

Use the two tools in [Scripts/Atlas14_Pipeline](Scripts/Atlas14_Pipeline/README.md) in this order.

First, run:

```powershell
python Scripts/Atlas14_Pipeline/atlas14_watershed_frequency_summary.py `
  --watershed-path "C:\path\to\watershed.geojson" `
  --atlas14-data-dir "C:\path\to\atlas14-cache" `
  --output-dir "C:\path\to\atlas14-results"
```

This tool downloads or reuses Atlas 14 grids, mosaics the selected NOAA volumes, and calculates watershed mean, minimum, and maximum precipitation-frequency values. It delivers the watershed statistics CSV, frequency-curve PNG, downloaded source files, and full Atlas 14 mosaics.

The watershed summary is not the HEC-HMS bias grid.

Second, run:

```powershell
python Scripts/Atlas14_Pipeline/atlas14_hms_bias_grid.py `
  --source-raster "C:\path\to\mosaic_100yr03da.tif" `
  --transposition-domain "C:\path\to\selected-domain.geojson" `
  --watershed-path "C:\path\to\watershed.geojson" `
  --output-dir "C:\path\to\hms-bias-grid"
```

This tool creates the 72-hour, 100-year Atlas 14 precipitation field across the complete transposition domain. It delivers:

- `atlas14_72hr_100yr_hms_bias_grid.tif`
- `atlas14_72hr_100yr_hms_bias_grid.png`
- `atlas14_72hr_100yr_hms_bias_grid.json`

The GeoTIFF is the HEC-HMS input. The 600 dpi PNG is the review map. The JSON file records inputs, hashes, projection, grid shape, pixel size, and precipitation statistics.

### 3. Build the ranked storm catalog in StormHub

Complete the StormHub run with the approved watershed, transposition domain, duration, period of record, and project settings. This step is outside the notebooks in this repository.

The following products are used by later tools:

- a duration folder such as `72hr-events`
- numbered rank folders
- one event JSON in each rank folder
- one event DSS file in each rank folder
- `ranked-storms.csv`
- `max_precip_locations.geojson`
- the geometrically valid transposition-domain file

Do not continue to catalog review until the StormHub run has finished and the ranked files are stable.

### 4. Check the ranked CSV against event JSON files

Run [mtools_02_storm_catalog_consistency.ipynb](notebooks/mtools_02_storm_catalog_consistency.ipynb).

Set:

- `EVENTS_DIR`
- `OUTPUT_DIR`
- CSV filename if it differs from `ranked-storms.csv`
- numeric comparison tolerance

The notebook compares storm date, mean precipitation, minimum precipitation, and maximum precipitation for each rank. It also checks for missing rank folders, missing JSON files, unreadable JSON files, duplicate ranks, and rank folders without CSV rows.

The output is:

```text
ranked_storm_json_consistency.xlsx
```

The workbook contains summary, field-summary, mismatch, full-comparison, and missing-folder sheets. Resolve or document each mismatch before using the catalog in later steps.

### 5. Review storm-catalog maps and precipitation distributions

Run [mtools_03_storm_catalog_maps.ipynb](notebooks/mtools_03_storm_catalog_maps.ipynb).

Provide:

- watershed polygon
- 24-hour and 72-hour SLAM-SIG files
- selected transposition domain from Step 1
- valid transposition domain from StormHub
- `max_precip_locations.geojson`
- CONUS boundary
- output folder

The notebook checks area ratio and Tcap, flags large maximum grid-cell precipitation values, maps storm locations and density, reviews storm timing, and evaluates the maximum-to-mean precipitation ratio. It also identifies storms outside the IQR review fences and compares maximum precipitation by season and decade.

The notebook saves all plots as 600 dpi PNG files. Main products include:

- `selected_domain_map.png`
- `cataloged_storms_per_year.png`
- `maximum_precipitation_locations.png`
- `maximum_precipitation_locations_by_season.png`
- `storm_location_density.png`
- `storms_calendar.png`
- `section6_precipitation_distribution_review.png`
- `section6_ratio_review.csv`
- `section6_maximum_precipitation_by_season_and_decade.png`

Review flagged storms against event maps, source grids, nearby observations, and the [QPE artifact guide](documents/SpatialQPEArtifacts.md). A flag identifies a storm for review. It does not prove the storm is invalid.

### 6. Generate AORC mass curves

Use [Scripts/StormCatalog_MassCurve](Scripts/StormCatalog_MassCurve/README.md).

Edit the `USER INPUTS` block in `mass_curve.py`. Set the catalog root, base watershed, duration folder, output folder, worker count, and plot resolution.

For a review run that must not change the source catalog, use:

```python
OUTPUT_DIR = Path(r"C:\path\to\mass-curve-results")
REGISTER_ASSETS = False
```

Then run:

```powershell
python Scripts/StormCatalog_MassCurve/mass_curve.py
```

The script retrieves hourly AORC precipitation and creates one mass-curve PNG per rank. Each plot compares cumulative and hourly precipitation at the maximum AORC grid cell with the transposed-watershed mean. The output folder also contains `mass_curve_run.log`, which records the catalog and destination paths.

Use the curves to review accumulation timing, short high-intensity periods, missing hours, unexpected decreases, repeated patterns, and large differences between the maximum grid cell and watershed mean.

### 7. Screen the transposition domain against IBTrACS

Run [mtools_04_ibtracs_screening.ipynb](notebooks/mtools_04_ibtracs_screening.ipynb).

Provide:

- watershed polygon
- valid transposition domain from StormHub
- IBTrACS since-1980 lines shapefile
- output folder

The notebook can download the current configured IBTrACS files when local copies are missing. It maps complete storm tracks within the review window and classifies the displayed lines by USA Saffir-Simpson Hurricane Wind Scale category.

The main output is:

```text
ibtracs_usa_sshs_tracks.png
```

The map is saved at 600 dpi. This notebook is a domain-level track screen. The TC and NT classification in Step 8 performs the event-level catalog comparison.

### 8. Classify storms and complete expert review

Use [Scripts/StormClassification_Seasonality](Scripts/StormClassification_Seasonality/README.md).

Run the files in this order:

1. `build_storm_catalog.py`
2. `classify_storms.py`
3. `qc_storm_typing.py`
4. expert manual review

The catalog builder reads the ranked event JSON files and creates `storm_catalog.csv`.

The classifier compares event timing and maximum-precipitation location with the full IBTrACS archive. It creates:

- `classified_storms.csv`
- `classification_summary.txt`
- `classification_metadata.json`
- `classification_log.txt`

The QC tool checks the classifications and creates:

- `qc_report.csv`
- `qc_summary.txt`
- TC and borderline NT review maps

A qualified reviewer must inspect the classifications and maps. Record changes in a copy of `manual_review_log_template.csv`. Save the completed classification as:

```text
classified_storms_verified.csv
```

Do not use the automatic classification as the final seasonality input.

### 9. Calculate seasonality from verified classifications

After the expert review is complete, run `seasonality.py` from [Scripts/StormClassification_Seasonality](Scripts/StormClassification_Seasonality/README.md).

Example:

```powershell
python Scripts/StormClassification_Seasonality/seasonality.py `
  --input "C:\path\to\classified_storms_verified.csv" `
  --output-dir "C:\path\to\seasonality-results" `
  --padding 7
```

The tool creates separate daily counts and cumulative distributions for verified TC events, verified NT events, and all events. It also writes summary tables, verification information, and seven PNG plots.

The default seven-day padding creates a 15-day calendar window around each storm start date. Record any change to the padding value in the project methods.

### 10. Copy and rename DSS files and build the HEC-HMS grid file

Use [Scripts/StormCatalog_HMSGrid](Scripts/StormCatalog_HMSGrid/README.md) after `classified_storms_verified.csv` is complete.

Run:

```powershell
python Scripts/StormCatalog_HMSGrid/prepare_hms_grid_import.py `
  --catalog-dir "C:\path\to\72hr-events" `
  --classified-csv "C:\path\to\classified_storms_verified.csv" `
  --output-dir "C:\path\to\hms-grid-import" `
  --a-part SHG1K `
  --b-part BASIN-NAME
```

The tool:

1. Confirms that catalog ranks and verified classifications match.
2. Copies each source DSS file without changing the source catalog.
3. Applies the SOP filename field order.
4. Places every renamed DSS file in one folder.
5. Creates the HEC-HMS `.grid` file.
6. Writes mapping, processing, and storm-center review files.

Outputs include:

- flat `dss` folder
- `dss_name_mapping.csv`
- basin storm-catalog `.grid` file
- `hms_grid_import.log`
- `hms_grid_generation.log`
- `storm_center_verification.csv`

The default storm-type fields are `tc` and `nt`. Change them only when the project has an approved mapping to another storm-type code.

Before bulk import, manually import the storms listed in `storm_center_verification.csv` into HEC-HMS. Confirm that the HEC-HMS storm-center X and Y coordinates match the generated values.

The `.grid` file stores absolute DSS paths. Regenerate it if the DSS folder is moved.

### 11. Complete the project review record

Before delivery:

1. Confirm that every notebook and script finished without an unresolved error.
2. Confirm that catalog CSV and JSON mismatches are resolved or documented.
3. Confirm that precipitation and mass-curve review flags were evaluated.
4. Confirm that TC and NT classifications received expert review.
5. Confirm that HEC-HMS storm-center coordinates were checked.
6. Save the executed notebooks, source paths, logs, output tables, figures, manual review records, SOP version, and repository commit.

## Notebook use

The notebooks use setup cells near the top for input paths, output folders, basin names, plot settings, and review thresholds.

For each notebook:

1. Copy or save the notebook with the basin results when the project requires an executed record.
2. Edit the path and settings cell.
3. Restart the kernel.
4. Run all cells from the top.
5. Read the printed input checks and summaries.
6. Confirm that the expected output files were written.
7. Save the notebook with its outputs.

The notebook order is:

```text
mtools_01_domain_selection.ipynb
StormHub catalog run
mtools_02_storm_catalog_consistency.ipynb
mtools_03_storm_catalog_maps.ipynb
mtools_04_ibtracs_screening.ipynb
```

## Environment setup

### VS Code devcontainer

The devcontainer provides the `mtools-base` environment and matches the `/workspaces/meteorology-tools/` paths used by the notebooks.

Requirements:

- Git
- Docker Desktop or another Docker-compatible runtime
- VS Code
- VS Code Dev Containers extension

Clone and open the repository:

```bash
git clone https://github.com/mohsennasab/meteorology-tools.git
cd meteorology-tools
code .
```

Choose `Dev Containers: Reopen in Container`, then select:

```text
/opt/conda/envs/mtools-base/bin/python
```

as the Python interpreter and notebook kernel.

### Local Conda

For a local run:

```bash
conda env create -f environment.yml
conda activate meteorology-tools
jupyter lab
```

Replace `/workspaces/meteorology-tools/` paths with local workstation paths.

The mass-curve workflow may require the project StormHub environment. The HMS grid workflow uses the versions in its own `requirements.txt`. Check each script-folder README before installing or changing packages.

The consistency workbook requires `openpyxl`. Install it in the selected environment if the notebook reports that the package is missing:

```bash
python -m pip install openpyxl
```

## Input data

Expected project inputs include:

- watershed polygon
- 24-hour and 72-hour SLAM-SIG domain files
- PRISM precipitation, dewpoint, and elevation rasters
- CONUS boundary
- StormHub ranked catalog
- AORC data access
- NOAA Atlas 14 grids
- IBTrACS track files
- verified TC and NT classification CSV

The template folders are:

```text
inputs/watershed/
inputs/transposition_domains/
inputs/storm_catalog/
inputs/conus/
inputs/prism/
inputs/IBTrACS_Lines/
```

Keep project data outside Git unless the project has approved it for repository storage.

## Network and external access

Some workflows require:

- NOAA HDSC access for Atlas 14 downloads
- NOAA or NCEI access for IBTrACS downloads
- AORC access on S3 for mass curves
- OpenStreetMap tile access for optional basemaps

The geospatial calculations can still run without an online basemap when the local analytical inputs are available. Use approved network settings and local copies when a service is blocked.

## Repository layout

```text
meteorology-tools/
  .devcontainer/
  documents/
    SpatialQPEArtifacts.md
  inputs/
  notebooks/
    mtools_01_domain_selection.ipynb
    mtools_02_storm_catalog_consistency.ipynb
    mtools_03_storm_catalog_maps.ipynb
    mtools_04_ibtracs_screening.ipynb
  outputs/
  Scripts/
    Atlas14_Pipeline/
    StormCatalog_MassCurve/
    StormClassification_Seasonality/
    StormCatalog_HMSGrid/
  environment.yml
  README.md
```

## Main review points

The tools produce screening information and repeatable outputs. These decisions still require review:

- selection of the final transposition domain
- acceptance or correction of ranked catalog mismatches
- disposition of QPE and mass-curve anomalies
- verification of TC and NT classifications
- HEC-HMS storm-center coordinate check
- acceptance of the Atlas 14 HEC-HMS bias grid

## Data sources

The workflows use data from PRISM Climate Group, NOAA AORC, NOAA Atlas 14, NOAA NCEI IBTrACS, the USGS Watershed Boundary Dataset, Census boundary files, and SLAM-SIG transposition domains.
