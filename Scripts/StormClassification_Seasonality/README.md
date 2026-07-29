# Storm Classification and Seasonality

This tool classifies ranked StormHub events as tropical cyclone (`TC`) or non-tropical (`NT`), prepares maps for quality control, and creates seasonality distributions after an expert has verified the classifications.

The required order is:

1. Build a working catalog from the StormHub item JSON files.
2. Run the automatic TC and NT classification.
3. Run the QC checks and create the review maps.
4. Have a qualified reviewer inspect the classifications and edit `classified_storms.csv`.
5. Save the completed review as `classified_storms_verified.csv`.
6. Run seasonality with the verified CSV.

Do not run seasonality from an unreviewed automatic classification.

## Files

| File | Purpose |
|---|---|
| `build_storm_catalog.py` | Reads ranked StormHub item JSON files and creates one catalog CSV. |
| `classify_storms.py` | Applies the automatic TC and NT classification. |
| `qc_storm_typing.py` | Runs consistency checks and creates maps for TC and borderline NT events. |
| `seasonality.py` | Creates daily occurrence counts, cumulative distributions, summaries, and plots. |
| `plot_style.py` | Applies the shared Arial plot style and 300 dpi output setting. |
| `manual_review_log_template.csv` | Provides a place to record expert classification decisions. |
| `assets/basemap/` | Contains optional land and state layers used by the QC maps. |

## Environment

Python 3.10 or later is recommended. From this tool folder:

```powershell
python -m pip install -r requirements.txt
```

The catalog builder and classifier can run with the Python standard library. The QC maps and seasonality plots require Matplotlib and NumPy. The scripts use GeoPandas or `dbfread` when either package is available. A built-in DBF reader is used otherwise.

All plots use Arial when it is installed. The shared style lists Helvetica, Liberation Sans, and DejaVu Sans as fallbacks. Figures use normal title weights and are saved at 300 dpi.

## Required Inputs

### StormHub event catalog

Use the duration folder that contains the ranked event folders and `ranked-storms.csv`. For example:

```text
72hr-events/
  1/1.json
  2/2.json
  ...
  ranked-storms.csv
```

The item JSON files are the primary source. They provide the event start and end times, precipitation statistics, maximum-precipitation location, and transposed watershed centroid.

If an event folder has no item JSON, `build_storm_catalog.py` can use the matching row in `ranked-storms.csv`. The output marks that row as a fallback so it can receive separate review. Use `--no-fallback` if fallback rows must be excluded.

### IBTrACS tracks

Use the full NOAA IBTrACS v04r01 since-1980 lines shapefile. Keep the `.dbf`, `.shx`, and other shapefile components beside the `.shp`. The large IBTrACS files are not stored in this repository.

The classifier applies its own distance test. Do not clip the IBTrACS tracks to the transposition domain.

### QC reference layers

The QC maps can show:

- the selected transposition domain
- the watershed boundary
- the optional land and state basemap layers in `assets/basemap`

The domain and watershed are map references. They do not control the automatic TC and NT classification.

Provide the domain and watershed in EPSG:4326 longitude and latitude coordinates. The unchanged QC code plots the GeoJSON coordinate arrays directly and does not reproject them. Convert projected files, such as EPSG:5070 outputs, to a separate EPSG:4326 GeoJSON before running QC. Keep the original projected file unchanged.

## Run the Workflow

Run these commands from `meteorology-tools\Scripts\StormClassification_Seasonality`. Replace the example paths with the project paths.

### Step 1: Build the working storm catalog

```powershell
python build_storm_catalog.py `
  --events-dir "C:\project\StormHub\basin\72hr-events" `
  --output "C:\project\results\Storm_Classification_Seasonality\storm_catalog.csv"
```

Output:

```text
storm_catalog.csv
```

The script copies one row per ranked event. It does not aggregate, round, or resample the STAC values. It calculates `duration_hours` from each item's actual start and end times.

### Step 2: Run the automatic classification

```powershell
python classify_storms.py `
  --catalog "C:\project\results\Storm_Classification_Seasonality\storm_catalog.csv" `
  --ibtracs "C:\project\IBTrACS\IBTrACS_since1980_list_v4r01.shp" `
  --output-dir "C:\project\results\Storm_Classification_Seasonality"
```

Outputs:

```text
classified_storms.csv
classification_summary.txt
classification_metadata.json
classification_log.txt
```

Preserve the automatic result before manual editing:

```powershell
Copy-Item `
  "C:\project\results\Storm_Classification_Seasonality\classified_storms.csv" `
  "C:\project\results\Storm_Classification_Seasonality\classified_storms_automatic.csv"
```

Do not rerun `classify_storms.py` after manual review begins. It will replace the working classification.

### Step 3: Run QC and create the maps

```powershell
python qc_storm_typing.py `
  --classified "C:\project\results\Storm_Classification_Seasonality\classified_storms.csv" `
  --ibtracs "C:\project\IBTrACS\IBTrACS_since1980_list_v4r01.shp" `
  --domain "C:\project\results\selected_transposition_domain.geojson" `
  --watershed "C:\project\inputs\watershed.geojson" `
  --basemap-dir "assets\basemap" `
  --output-dir "C:\project\results\Storm_Classification_Seasonality\QC"
```

Outputs:

```text
QC/
  qc_report.csv
  qc_summary.txt
  maps/
    RANK###_TC_*.png
    RANK###_BORDERLINE_NT_*.png
```

The QC script checks totals, event windows, event IDs, matched IBTrACS IDs, and duplicate dates. It creates a map for every automatic TC event and every borderline NT event.

Review the flags in this order:

1. `multi_tc`: More than one tropical cyclone passed within range. Determine which storm caused the event.
2. `padding_only`: The match depends on the one-day time allowance. Confirm the rainfall timing against the track.
3. `borderline_nt`: A tropical cyclone nearly met the temporal rule. Confirm that the event is non-tropical.
4. `fallback_source`: The event was built from `ranked-storms.csv` because an item JSON was missing. Verify the date and precipitation information.
5. `distant_track`: This should be empty when the classifier and QC use the same 500 km radius. Any entry indicates inconsistent settings or data.

### Step 4: Complete the expert review

The automatic output is a screening result. A qualified reviewer must inspect every ranked event before seasonality is calculated. Start with the flagged events, then review the remaining rows.

Use the StormHub event thumbnail, the QC map, the event dates, the IBTrACS track, and other project meteorology information. Edit the `classification` field in `classified_storms.csv` using only:

```text
TC
NT
```

Keep `event_id`, event dates, and precipitation values unchanged unless a separate source-data correction has been approved. Record changes in a copy of `manual_review_log_template.csv`. Include the rank, automatic classification, verified classification, reason, evidence, reviewer, and review date.

If a classification changes, review the matched TC fields for consistency:

- For a change from `TC` to `NT`, clear TC match fields that no longer apply or document why they were retained.
- For a change from `NT` to `TC`, record the supporting storm name and IBTrACS ID when available.

After every row has been reviewed:

1. Save the working file.
2. Copy it to `classified_storms_verified.csv`.
3. Confirm that every `classification` value is `TC` or `NT`.
4. Rerun QC with `classified_storms_verified.csv`.
5. Resolve or document any new QC findings.

The reviewed file is the approved seasonality input.

### Step 5: Run seasonality after verification

The default run applies seven days of padding before and after each storm start date:

```powershell
python seasonality.py `
  --input "C:\project\results\Storm_Classification_Seasonality\classified_storms_verified.csv" `
  --output-dir "C:\project\results\Storm_Classification_Seasonality\Seasonality_7Day" `
  --padding 7
```

For an unpadded distribution with one count on each storm start date:

```powershell
python seasonality.py `
  --input "C:\project\results\Storm_Classification_Seasonality\classified_storms_verified.csv" `
  --output-dir "C:\project\results\Storm_Classification_Seasonality\Seasonality_NoPadding" `
  --padding 0
```

Each seasonality folder contains count tables, cumulative distribution tables, a verification summary, and seven PNG plots.

## Classification Method

An event is automatically classified as `TC` when at least one IBTrACS track fix meets both conditions:

1. The fix is within 500 km of the event's maximum-precipitation location.
2. The fix occurs within the event window plus one day before and one day after.

The event is classified as `NT` when no track satisfies both conditions.

The 500 km distance and one-day time allowance follow tropical-cyclone rainfall attribution methods used by Khouakhi, Villarini, and Vecchi (2017) and Kunkel et al. (2010). The distance is calculated directly from each event's maximum-precipitation location to the full IBTrACS track.

All IBTrACS lifecycle phases are treated as tropical for classification. This includes tropical, subtropical, extratropical transition, post-tropical, and remnant phases when the system exists in IBTrACS. This choice retains rainfall associated with tropical moisture after a cyclone changes structure.

If more than one storm meets the rule, the script records the storm with the greatest peak wind near the event as the primary match. Other qualifying storms are listed in `other_tc_matches` for expert review.

Spur tracks are removed because they are alternate positions that can duplicate a storm.

## Seasonality Method

Seasonality is based on the storm start date. Separate daily occurrence counts and cumulative distributions are created for:

- verified TC events
- verified NT events
- all verified events

With the default seven-day padding, each storm contributes to 15 calendar days. The script uses calendar dates rather than direct day-of-year arithmetic. This preserves dates that cross a year boundary and retains day 366 in leap years.

The padding value is a project decision. Seven days follows the USACE `CatalogSeasonalityDistributions.py` convention. A value of zero produces one count per storm and is useful for checking the unsmoothed distribution.

The summary file verifies the expected count totals and confirms that each cumulative distribution ends at 1.0.

## Settings That Must Stay Consistent

Use the same values in classification and QC:

| Setting | Classification | QC | Default |
|---|---|---|---:|
| Temporal allowance | `--window` | `--window` | 1 day |
| Matching radius | `--radius` | `--distance-km` | 500 km |
| IBTrACS shapefile | `--ibtracs` | `--ibtracs` | Same full archive |

Changing these settings changes the method and may change classifications. Record any change in the project methods documentation.

## References

- Khouakhi, A., G. Villarini, and G. A. Vecchi. 2017. Contribution of tropical cyclones to rainfall at the global scale. *Journal of Climate*, 30, 359-372. https://doi.org/10.1175/JCLI-D-16-0298.1
- Kunkel, K. E., D. R. Easterling, D. A. R. Kristovich, B. Gleason, L. Stoecker, and R. Smith. 2010. Recent increases in U.S. heavy precipitation associated with tropical cyclones. *Geophysical Research Letters*, 37, L24706. https://doi.org/10.1029/2010GL045164
- Knapp, K. R., M. C. Kruk, D. H. Levinson, H. J. Diamond, and C. J. Neumann. 2010. The International Best Track Archive for Climate Stewardship. *Bulletin of the American Meteorological Society*, 91, 363-376. https://doi.org/10.1175/2009BAMS2755.1
- FFRD Meteorology SOP, Section 1.5 and Volume II, Section 5.6.2.
- USACE `CatalogSeasonalityDistributions.py`, used for the seven-day seasonality padding convention.

## Code Integrity

The Python files in this folder were copied from:

```text
C:\OneDrive\OneDrive - AECOM\FFRD\Validation Basin\updated_analysis_July2026\storm-class-seasonality
```

The analytical code was not changed when it was added to `meteorology-tools`.
