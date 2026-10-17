# Storm Catalog DSS Export and HMS Grid

This tool prepares a ranked storm catalog for import into HEC-HMS. It copies each source DSS file into one flat folder, assigns a standard storm filename, creates an old-to-new filename table, and builds the HEC-HMS `.grid` file.

The source catalog is read only. The script does not rename, move, or edit any source DSS file.

## Files

- `prepare_hms_grid_import.py` runs the full workflow.
- `generate_hms_grid.py` calculates storm centers and writes the HEC-HMS grid manager file.
- `HmsStormCenter.java` computes storm centers with HEC-HMS's own code. `generate_hms_grid.py` compiles and runs it.
- `requirements.txt` lists the tested Python packages.

The grid generation code was brought into this repository from:

```text
G:\Python Package\DSS-Grid-Generator
```

## Required inputs

The user must provide:

1. A ranked StormHub catalog folder
2. A verified storm classification CSV
3. A new output folder

The ranked catalog must contain numbered rank folders. Each rank folder must contain one DSS file.

```text
72hr-events
  1
    20240925.dss
  2
    19951003.dss
  3
    20040906.dss
```

The verified classification CSV must contain these fields:

- `event_id`
- `start_datetime`
- `duration_hours`
- `classification`

`event_id` must match the rank folder number. `classification` must be `TC` or `NT`. Use the manually reviewed classification file, not the unchecked automatic classification output.

## DSS filename convention

SOP Job Aid 6 defines the storm filename fields in this order:

```text
event-start-date_storm-duration_storm-type_storm-rank
```

The script adds the `.dss` extension. Dates use `YYYYMMDD`, duration uses an hour value such as `72hr`, and ranks use three digits such as `r001`.

The default storm-type fields are:

- `tc` for Tropical Cyclone
- `nt` for Non-Tropical

Examples:

```text
20240925_72hr_tc_r001.dss
19770402_72hr_nt_r006.dss
```

Job Aid 6 also shows generic storm-type examples such as `st2`, `st3`, and `st4`. The December 2025 SOP requires TC and NT classification but does not assign these classes to specific `st#` codes. The script therefore uses `tc` and `nt` unless the project naming table provides an approved mapping.

To use approved `st#` fields, pass both values on the command line:

```powershell
--tc-token st2 --nt-token st3
```

Replace `st2` and `st3` with the approved project values.

## Install

Open PowerShell in this tool folder and use the Python environment that has HEC DSS support:

```powershell
python -m pip install -r .\requirements.txt
```

The tested requirements are `hecdss==0.1.29` and `numpy==2.3.2`.

Storm centers come from HEC-HMS by default, which also needs:

- **HEC-HMS 4.13** installed. The default folder is `C:\Program Files\HEC\HEC-HMS\4.13`; use `--hms-dir` for another location. The script uses the HMS `hms.jar`, the `lib` folder, the bundled `jre`, and the native DSS library in `bin`.
- **A JDK 17 or later** for `javac`. The HMS JRE cannot compile Java source. The script looks for `javac` in `JAVA_HOME`, then on `PATH`; use `--javac` to point to it directly. The compiled class is written to a temporary folder and removed after the run.

Use `--center-method python` to skip HEC-HMS and use the Python calculation only.

## Run the full workflow

```powershell
python .\prepare_hms_grid_import.py `
  --catalog-dir "C:\path\to\72hr-events" `
  --classified-csv "C:\path\to\classified_storms_verified.csv" `
  --output-dir "C:\path\to\hms-grid-import" `
  --a-part SHG1K `
  --b-part UPPER-TENNESSEE
```

The DSS pathname filters are optional. Supplying the expected A and B parts prevents an unrelated grid record from being selected. The default C part is `PRECIPITATION` and the default F part is `AORC`.

Use a short test before a full catalog run:

```powershell
python .\prepare_hms_grid_import.py `
  --catalog-dir "C:\path\to\72hr-events" `
  --classified-csv "C:\path\to\classified_storms_verified.csv" `
  --output-dir "C:\path\to\hms-grid-import-test" `
  --a-part SHG1K `
  --b-part UPPER-TENNESSEE `
  --limit 6
```

The script stops before copying if ranks are missing, classifications are duplicated, a rank contains more than one DSS file, or a generated name is not unique.

Existing DSS outputs are not replaced unless `--overwrite` is supplied. The script also refuses to run if the DSS output folder contains files outside the expected set.

## Outputs

The output folder has this structure:

```text
hms-grid-import
  dss
    20240925_72hr_tc_r001.dss
    ...
  basin-name_storm-catalog.grid
  dss_name_mapping.csv
  hms_grid_import.log
  hms_grid_generation.log
  storm_center_verification.csv
```

`dss` contains every renamed storm DSS file in one folder.

`dss_name_mapping.csv` records the rank, classification, old filename, old path, new filename, new path, file size, and copy status.

`hms_grid_import.log` records the source catalog, verified classification file, destination DSS folder, file counts, byte totals, filename fields, and elapsed time.

`hms_grid_generation.log` records the grid record processing, the Python storm center for every DSS file, the HEC-HMS run, and every tie or mismatch between the two.

The `.grid` file contains one HEC-HMS Grid block per copied DSS file. Each block includes the grid name, storm center X and Y, absolute DSS filename, and DSS pathname.

`storm_center_verification.csv` has one row per storm:

| Field | Content |
|---|---|
| `rank`, `classification`, `dss_filename`, `grid_name` | storm identity |
| `storm_center_x`, `storm_center_y` | the value written to the `.grid` file |
| `center_source` | `HEC-HMS` or `Python` |
| `hms_max_total`, `hms_cells_at_max` | maximum total and number of cells with that exact value in HEC-HMS |
| `python_center_x`, `python_center_y`, `python_max_total` | the Python cross-check |
| `python_cells_near_max` | cells within `--tie-tolerance` of the maximum (default 0.05, in DSS units) |
| `center_check` | `match`, `tie`, `mismatch`, or `python` |

The script confirms that every `.grid` center equals the value in this file.

## Storm center method

The storm center is the center of the SHG cell with the largest 72-hour total anywhere in the DSS grid, which covers the transposition domain. This follows Job Aid 3 ("the center of the pixel where the maximum total accumulation of rainfall occurred") and is the same value as the HEC-HMS **Compute Storm Center** button.

It is not the same point as StormHub's `aorc:max_precip_location`, which is the largest cell inside the transposed watershed. The two differ when the wettest cell of the storm lies outside the watershed.

### HEC-HMS centers (default)

`HmsStormCenter.java` runs the HEC-HMS 4.13 storm center routine (`hms.model.met.e.K`) with the libraries installed with HEC-HMS:

1. Read the total precipitation for the full record with the Vortex `TemporalDataReader` that HEC-HMS uses for gridsets.
2. Keep the first cell with the largest total (strict `>` from `Float.MIN_VALUE`).
3. Convert that cell to its center with the HEC-HMS grid definition: `originX + (col + 0.5) dx`, `originY + (row + 0.5) dy`.

These coordinates are written to the `.grid` file.

### Python cross-check

For each DSS file the Python code also:

1. Selects records that match the A, B, C, and F pathname filters.
2. Adds the precipitation grids through the event period.
3. Finds the cell with the largest accumulated precipitation.
4. Converts the row and column to the center of the SHG cell using the DSS grid metadata (row 0 is the south edge).

Each storm is then marked:

- `match`: HEC-HMS and Python select the same cell.
- `tie`: the HEC-HMS cell is one of the Python cells within `--tie-tolerance` of the maximum. AORC totals are stored to 0.1 mm, so two cells can have the same total. HEC-HMS keeps the first one in its own order, so the `.grid` value is the cell HEC-HMS would compute.
- `mismatch`: the HEC-HMS cell is not among the Python maximum cells. The `.grid` file is still written, the run ends with an error, and the storm must be investigated.

If HEC-HMS cannot be run, no `.grid` file is written.

The HMS pathname removes the dated D and E parts. For Upper Tennessee, the expected pathname is:

```text
/SHG1K/UPPER-TENNESSEE/PRECIPITATION///AORC/
```

### Optional spot check in HEC-HMS

The HEC-HMS centers do not need a manual check. To see one in the interface, import a DSS file listed in `storm_center_verification.csv` as a precipitation gridset, press **Compute** next to Storm Center X and Y, and compare with `storm_center_x` and `storm_center_y`.

The `.grid` file stores absolute DSS paths. If the DSS folder is moved or renamed, regenerate the `.grid` file so every `Filename` value points to the new location.

## SOP references

The naming and delivery rules were taken from:

- `06. Standard Naming Conventions`, Job Aid 6
- `03. Meteorologic Data Processes`, Job Aid 3

These files are in:

```text
C:\OneDrive\OneDrive - AECOM\FFRD\Validation Basin\SOP Dec 2025\SOP-Dec2025\markdown
```

Job Aid 6 supplies the filename field order and formatting. Job Aid 3 supplies the TC and NT classification requirement and the delivery requirement for one DSS file per storm with a complete HEC-HMS grid file.
