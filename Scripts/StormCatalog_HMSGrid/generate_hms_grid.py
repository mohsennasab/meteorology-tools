from __future__ import annotations

import argparse
import concurrent.futures
import csv
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from time import perf_counter

import numpy as np

try:
    from hecdss import DssPath, HecDss
    from hecdss.record_type import RecordType
except ImportError as exc:
    raise SystemExit(
        "This script needs the HEC DSS Python package. Install/use the Python "
        "environment that has `hecdss` available."
    ) from exc


# ---------------------------------------------------------------------------
# User-editable defaults
#
# These are the settings most users change between projects. They can also be
# overridden from a JSON config file or the command line. See README.md.
# ---------------------------------------------------------------------------
DEFAULT_INPUT_DIR = r"F:\MN_River\HMS_13\storm-catalog\0p475\72hr-events"
DEFAULT_OUTPUT_FILE = "LowerMN_storm_catalog.grid"
DEFAULT_A_PART = None
DEFAULT_B_PART = None
DEFAULT_C_PART = "PRECIPITATION"
DEFAULT_F_PART = "AORC"
DEFAULT_NAME_PREFIX = "Storm_"
DEFAULT_HMS_VERSION = "4.13"
DEFAULT_GRID_TYPE = "Precipitation"
DEFAULT_DESCRIPTION = ""
DEFAULT_DATA_SOURCE_TYPE = "Modifiable DSS"
DEFAULT_FILEPATH_SEPARATOR = "\\"
DEFAULT_UNDEFINED_THRESHOLD = -3.0e38
DEFAULT_LOG_FILE = None
DEFAULT_WORKERS = min(os.cpu_count() or 1, 6)
# Storm centers come from HEC-HMS's own code (HmsStormCenter.java) by default.
# "python" uses only the NumPy calculation in this file.
DEFAULT_CENTER_METHOD = "hms"
DEFAULT_HMS_DIR = r"C:\Program Files\HEC\HEC-HMS\4.13"
# javac from a JDK 17 or later. None searches JAVA_HOME and PATH.
DEFAULT_JAVAC = None
DEFAULT_CENTER_REPORT = None
# Cells within this accumulation (DSS data units) of the maximum count as tied.
DEFAULT_TIE_TOLERANCE = 0.05

CONFIG_KEYS = {
    "input_dir",
    "output",
    "a_part",
    "b_part",
    "c_part",
    "f_part",
    "name_prefix",
    "hms_version",
    "grid_type",
    "description",
    "data_source_type",
    "filepath_separator",
    "undefined_threshold",
    "log_file",
    "workers",
    "limit",
    "center_method",
    "hms_dir",
    "javac",
    "center_report",
    "tie_tolerance",
}


@dataclass(frozen=True)
class GridEntry:
    name: str
    filename: Path
    pathname: str
    storm_center_x: float
    storm_center_y: float
    python_center_x: float
    python_center_y: float
    python_max_total: float
    # Cell centers within the tie tolerance of the maximum (Python accumulation).
    python_near_max_centers: tuple[tuple[float, float], ...]
    hms_max_total: float | None = None
    hms_cells_at_max: int | None = None
    center_check: str = "python"


@dataclass(frozen=True)
class ProcessingSettings:
    c_part: str
    f_part: str
    a_part: str | None
    b_part: str | None
    name_prefix: str
    undefined_threshold: float
    tie_tolerance: float


@dataclass(frozen=True)
class ProcessResult:
    index: int
    total: int
    dss_file: Path
    entry: GridEntry | None
    error: str | None
    elapsed_seconds: float


def natural_key(path: Path) -> tuple:
    parts = re.split(r"(\d+)", str(path).lower())
    return tuple(int(part) if part.isdigit() else part for part in parts)


def dss_files_under(input_dir: Path) -> list[Path]:
    return sorted(input_dir.rglob("*.dss"), key=natural_key)


def select_grid_paths(
    catalog_items: list[DssPath],
    c_part: str,
    f_part: str,
    a_part: str | None,
    b_part: str | None,
) -> list[str]:
    selected: list[str] = []
    for item in catalog_items:
        if c_part and item.C.upper() != c_part.upper():
            continue
        if f_part and item.F.upper() != f_part.upper():
            continue
        if a_part and item.A.upper() != a_part.upper():
            continue
        if b_part and item.B.upper() != b_part.upper():
            continue
        selected.append(str(item))
    return selected


def hms_grid_pathname(first_grid_path: str) -> str:
    path = DssPath(first_grid_path)
    return f"/{path.A}/{path.B}/{path.C}///{path.F}/"


def wildcard_pathname(hms_pathname: str) -> str:
    """HMS pathname with blank D and E parts -> Vortex pattern with * D and E parts."""
    parts = hms_pathname.split("/")
    parts[4], parts[5] = "*", "*"
    return "/".join(parts)


def event_name(dss_file: Path, prefix: str) -> str:
    return f"{prefix}{dss_file.stem}"


def storm_center_from_accumulation(
    dss: HecDss,
    grid_paths: list[str],
    undefined_threshold: float,
    tie_tolerance: float,
) -> tuple[float, float, float, tuple[tuple[float, float], ...]]:
    """Python storm center: center of the largest-total cell over the whole grid.

    Returns (x, y, max_total, centers of all cells within tie_tolerance of the
    maximum). With HMS centers this is the cross-check."""
    accumulation: np.ndarray | None = None
    first_grid = None

    for grid_path in grid_paths:
        grid = dss.get(grid_path)
        if grid is None:
            raise RuntimeError(f"Could not read grid record: {grid_path}")

        values = np.array(grid.data, dtype=float)
        values[values <= undefined_threshold] = np.nan

        if accumulation is None:
            first_grid = grid
            accumulation = np.nan_to_num(values, nan=0.0)
        else:
            if values.shape != accumulation.shape:
                raise RuntimeError(
                    f"Grid shape changed in {grid_path}: {values.shape} != {accumulation.shape}"
                )
            accumulation += np.nan_to_num(values, nan=0.0)

    if accumulation is None or first_grid is None:
        raise RuntimeError("No grid records were available for storm-center calculation.")

    if not np.isfinite(accumulation).any():
        raise RuntimeError("The accumulated grid does not contain any finite values.")

    row, col = np.unravel_index(np.nanargmax(accumulation), accumulation.shape)
    max_total = float(accumulation[row, col])

    # HMS reports storm centers at SHG cell centers. For these DSS grids the
    # row/column origin is the lower-left cell stored in the grid metadata.
    def cell_center(r: int, c: int) -> tuple[float, float]:
        x = (
            first_grid.xCoordOfGridCellZero
            + (first_grid.lowerLeftCellX + int(c) + 0.5) * first_grid.cellSize
        )
        y = (
            first_grid.yCoordOfGridCellZero
            + (first_grid.lowerLeftCellY + int(r) + 0.5) * first_grid.cellSize
        )
        return float(x), float(y)

    x, y = cell_center(row, col)
    near_max = np.argwhere(accumulation >= max_total - tie_tolerance)
    near_centers = tuple(cell_center(r, c) for r, c in near_max)
    return x, y, max_total, near_centers


def read_grid_entry(
    dss_file: Path,
    c_part: str,
    f_part: str,
    a_part: str | None,
    b_part: str | None,
    name_prefix: str,
    undefined_threshold: float,
    tie_tolerance: float,
) -> GridEntry:
    with HecDss(str(dss_file)) as dss:
        catalog = dss.get_catalog()
        grid_items = [
            item
            for item in catalog.items
            if catalog.get_record_type(str(item)) == RecordType.Grid
        ]
        grid_paths = select_grid_paths(grid_items, c_part, f_part, a_part, b_part)

        if not grid_paths:
            raise RuntimeError(
                f"No matching grid records found. Filters: A={a_part}, B={b_part}, "
                f"C={c_part}, F={f_part}"
            )

        center_x, center_y, max_total, near_centers = storm_center_from_accumulation(
            dss,
            grid_paths,
            undefined_threshold,
            tie_tolerance,
        )
        return GridEntry(
            name=event_name(dss_file, name_prefix),
            filename=dss_file,
            pathname=hms_grid_pathname(grid_paths[0]),
            storm_center_x=center_x,
            storm_center_y=center_y,
            python_center_x=center_x,
            python_center_y=center_y,
            python_max_total=max_total,
            python_near_max_centers=near_centers,
        )


def process_dss_file(
    index: int,
    total: int,
    dss_file: Path,
    settings: ProcessingSettings,
) -> ProcessResult:
    start = perf_counter()
    try:
        try:
            HecDss.set_global_debug_level(1)
        except Exception:
            pass

        entry = read_grid_entry(
            dss_file=dss_file,
            c_part=settings.c_part,
            f_part=settings.f_part,
            a_part=settings.a_part,
            b_part=settings.b_part,
            name_prefix=settings.name_prefix,
            undefined_threshold=settings.undefined_threshold,
            tie_tolerance=settings.tie_tolerance,
        )
        return ProcessResult(
            index=index,
            total=total,
            dss_file=dss_file,
            entry=entry,
            error=None,
            elapsed_seconds=perf_counter() - start,
        )
    except Exception as exc:
        return ProcessResult(
            index=index,
            total=total,
            dss_file=dss_file,
            entry=None,
            error=str(exc),
            elapsed_seconds=perf_counter() - start,
        )


def find_javac(javac: str | None) -> Path:
    """javac from the option, JAVA_HOME, or PATH. HEC-HMS ships a JRE without javac."""
    candidates: list[str] = []
    if javac:
        candidates.append(javac)
    java_home = os.environ.get("JAVA_HOME")
    if java_home:
        candidates.append(str(Path(java_home) / "bin" / "javac"))
    candidates.append("javac")
    for candidate in candidates:
        found = shutil.which(candidate)
        if found:
            return Path(found)
    raise RuntimeError(
        "javac was not found. Install a JDK 17 or later, or pass --javac. "
        "HEC-HMS's bundled JRE cannot compile HmsStormCenter.java."
    )


def hms_java_paths(hms_dir: Path) -> tuple[Path, str, Path]:
    """(java executable, HMS classpath, native library folder) for an HEC-HMS install."""
    java = hms_dir / "jre" / "bin" / ("java.exe" if os.name == "nt" else "java")
    hms_jar = hms_dir / "hms.jar"
    native = hms_dir / "bin"
    missing = [str(p) for p in (java, hms_jar, hms_dir / "lib", native) if not p.exists()]
    if missing:
        raise RuntimeError(f"HEC-HMS installation not found or incomplete: {', '.join(missing)}")
    return java, f"{hms_jar}{os.pathsep}{hms_dir / 'lib' / '*'}", native


def hms_storm_centers(
    entries: list[GridEntry],
    hms_dir: Path,
    javac: str | None,
    logger: logging.Logger,
) -> dict[str, dict]:
    """Storm centers from HEC-HMS's own code (HmsStormCenter.java), keyed by DSS path."""
    java, hms_classpath, native = hms_java_paths(hms_dir)
    javac_exe = find_javac(javac)
    source = Path(__file__).with_name("HmsStormCenter.java")
    if not source.is_file():
        raise RuntimeError(f"HmsStormCenter.java not found next to this script: {source}")

    by_pathname: dict[str, list[Path]] = {}
    for entry in entries:
        by_pathname.setdefault(wildcard_pathname(entry.pathname), []).append(entry.filename)

    results: dict[str, dict] = {}
    with tempfile.TemporaryDirectory(prefix="hms_storm_center_") as tmp:
        tmp_dir = Path(tmp)
        classes = tmp_dir / "classes"
        compile_cmd = [str(javac_exe), "--release", "17", "-cp", hms_classpath, "-d", str(classes), str(source)]
        compiled = subprocess.run(compile_cmd, capture_output=True, text=True)
        if compiled.returncode != 0:
            raise RuntimeError(f"Compiling HmsStormCenter.java failed:\n{compiled.stderr}")
        logger.info("HEC-HMS storm centers: %s with %s", hms_dir, javac_exe)

        env = os.environ.copy()
        env["PATH"] = f"{native}{os.pathsep}{env.get('PATH', '')}"
        for index, (pathname, files) in enumerate(by_pathname.items(), start=1):
            list_file = tmp_dir / f"files_{index}.txt"
            list_file.write_text("\n".join(str(f) for f in files), encoding="utf-8")
            out_csv = tmp_dir / f"centers_{index}.csv"
            run_cmd = [
                str(java),
                f"-Djava.library.path={native}",
                f"-Dout={out_csv}",
                "-cp",
                f"{classes}{os.pathsep}{hms_classpath}",
                "HmsStormCenter",
                pathname,
                f"--list={list_file}",
            ]
            logger.info("Running HEC-HMS storm center for %s DSS file(s), pathname %s", len(files), pathname)
            completed = subprocess.run(
                run_cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True
            )
            if completed.returncode != 0 or not out_csv.is_file():
                raise RuntimeError(
                    f"HmsStormCenter failed with exit code {completed.returncode}:\n{completed.stderr[-2000:]}"
                )
            with out_csv.open(encoding="utf-8") as handle:
                for row in csv.DictReader(handle):
                    results[os.path.normcase(str(Path(row["dss"]).resolve()))] = {
                        "x": float(row["hms_x"]),
                        "y": float(row["hms_y"]),
                        "max_total": float(row["hms_max_total"]),
                        "cells_at_max": int(row["n_cells_at_max"]),
                    }
    return results


def apply_hms_centers(
    entries: list[GridEntry],
    centers: dict[str, dict],
) -> list[GridEntry]:
    """Use the HEC-HMS centers and classify each against the Python cross-check.

    match: same cell. tie: the HMS cell is one of the Python cells within the tie
    tolerance of the maximum (equal totals; HEC-HMS keeps the first such cell in
    its own order). mismatch: anything else."""
    updated: list[GridEntry] = []
    for entry in entries:
        key = os.path.normcase(str(Path(entry.filename).resolve()))
        if key not in centers:
            raise RuntimeError(f"HEC-HMS returned no storm center for {entry.filename}")
        hms = centers[key]
        same = abs(hms["x"] - entry.python_center_x) < 0.05 and abs(hms["y"] - entry.python_center_y) < 0.05
        tied = any(
            abs(hms["x"] - x) < 0.05 and abs(hms["y"] - y) < 0.05 for x, y in entry.python_near_max_centers
        )
        updated.append(
            replace(
                entry,
                storm_center_x=hms["x"],
                storm_center_y=hms["y"],
                hms_max_total=hms["max_total"],
                hms_cells_at_max=hms["cells_at_max"],
                center_check="match" if same else ("tie" if tied else "mismatch"),
            )
        )
    return updated


def write_center_report(report_file: Path, entries: list[GridEntry]) -> None:
    fields = [
        "grid_name",
        "dss_file",
        "storm_center_x",
        "storm_center_y",
        "center_source",
        "hms_max_total",
        "hms_cells_at_max",
        "python_center_x",
        "python_center_y",
        "python_max_total",
        "python_cells_near_max",
        "center_check",
    ]
    report_file.parent.mkdir(parents=True, exist_ok=True)
    with report_file.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for entry in entries:
            writer.writerow(
                {
                    "grid_name": entry.name,
                    "dss_file": entry.filename,
                    "storm_center_x": f"{entry.storm_center_x:.1f}",
                    "storm_center_y": f"{entry.storm_center_y:.1f}",
                    "center_source": "HEC-HMS" if entry.hms_max_total is not None else "Python",
                    "hms_max_total": "" if entry.hms_max_total is None else f"{entry.hms_max_total:.4f}",
                    "hms_cells_at_max": "" if entry.hms_cells_at_max is None else entry.hms_cells_at_max,
                    "python_center_x": f"{entry.python_center_x:.1f}",
                    "python_center_y": f"{entry.python_center_y:.1f}",
                    "python_max_total": f"{entry.python_max_total:.4f}",
                    "python_cells_near_max": len(entry.python_near_max_centers),
                    "center_check": entry.center_check,
                }
            )


def default_center_report(output_file: Path) -> Path:
    return output_file.with_name(f"{output_file.stem}_storm_centers.csv")


def render_grid_file(
    entries: list[GridEntry],
    modified_at: datetime,
    hms_version: str,
    grid_type: str,
    description: str,
    data_source_type: str,
    filepath_separator: str,
) -> str:
    date_text = f"{modified_at.day} {modified_at.strftime('%B %Y')}"
    time_text = modified_at.strftime("%H:%M:%S")

    lines = [
        "Grid Manager: ",
        "     Grid Manager: ",
        f"     Version: {hms_version}",
        f"     Filepath Separator: {filepath_separator}",
        "End: ",
        "",
    ]

    for entry in entries:
        lines.extend(
            [
                f"Grid: {entry.name}",
                f"     Grid: {entry.name}",
                f"     Grid Type: {grid_type}",
                f"     Description: {description}",
                f"     Last Modified Date: {date_text}",
                f"     Last Modified Time: {time_text}",
                f"     Storm Center X: {entry.storm_center_x:.1f}",
                f"     Storm Center Y: {entry.storm_center_y:.1f}",
                f"     Data Source Type: {data_source_type}",
                f"     Filename: {entry.filename}",
                f"     Pathname: {entry.pathname}",
                "End: ",
                "",
            ]
        )

    return "\n".join(lines)


def default_options() -> dict:
    return {
        "input_dir": DEFAULT_INPUT_DIR,
        "output": DEFAULT_OUTPUT_FILE,
        "a_part": DEFAULT_A_PART,
        "b_part": DEFAULT_B_PART,
        "c_part": DEFAULT_C_PART,
        "f_part": DEFAULT_F_PART,
        "name_prefix": DEFAULT_NAME_PREFIX,
        "hms_version": DEFAULT_HMS_VERSION,
        "grid_type": DEFAULT_GRID_TYPE,
        "description": DEFAULT_DESCRIPTION,
        "data_source_type": DEFAULT_DATA_SOURCE_TYPE,
        "filepath_separator": DEFAULT_FILEPATH_SEPARATOR,
        "undefined_threshold": DEFAULT_UNDEFINED_THRESHOLD,
        "log_file": DEFAULT_LOG_FILE,
        "workers": DEFAULT_WORKERS,
        "limit": None,
        "center_method": DEFAULT_CENTER_METHOD,
        "hms_dir": DEFAULT_HMS_DIR,
        "javac": DEFAULT_JAVAC,
        "center_report": DEFAULT_CENTER_REPORT,
        "tie_tolerance": DEFAULT_TIE_TOLERANCE,
    }


def load_config(config_path: Path | None) -> dict:
    if config_path is None:
        return {}

    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"Config file not found: {config_path}")
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Config file is not valid JSON: {config_path}\n{exc}") from exc

    if not isinstance(config, dict):
        raise SystemExit(f"Config file must contain a JSON object: {config_path}")

    unknown_keys = sorted(set(config) - CONFIG_KEYS)
    if unknown_keys:
        raise SystemExit(
            f"Unknown config setting(s) in {config_path}: {', '.join(unknown_keys)}"
        )

    return config


def default_log_file(output_file: Path) -> Path:
    return output_file.with_suffix(".log")


def setup_logger(log_file: Path) -> logging.Logger:
    log_file.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("hms_grid_generator")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.propagate = False

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    file_handler = logging.FileHandler(log_file, mode="w", encoding="utf-8")
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    return logger


def parse_args(argv: list[str]) -> argparse.Namespace:
    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Optional JSON config file with default settings.",
    )
    known_args, _ = pre_parser.parse_known_args(argv)
    options = default_options()
    options.update(load_config(known_args.config))

    parser = argparse.ArgumentParser(
        description=(
            "Generate an HEC-HMS .grid file from a folder tree of DSS gridded "
            "precipitation events."
        ),
        parents=[pre_parser],
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=Path(options["input_dir"]),
        help=f"Folder containing event subfolders. Default: {options['input_dir']}",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(options["output"]),
        help=f"Output .grid file. Default: {options['output']}",
    )
    parser.add_argument(
        "--a-part",
        default=options["a_part"],
        help="Optional DSS A-part filter, for example SHG1K.",
    )
    parser.add_argument(
        "--b-part",
        default=options["b_part"],
        help="Optional DSS B-part filter, for example MN_RIVER_0P475.",
    )
    parser.add_argument(
        "--c-part",
        default=options["c_part"],
        help=f"DSS C-part filter. Default: {options['c_part']}",
    )
    parser.add_argument(
        "--f-part",
        default=options["f_part"],
        help=f"DSS F-part filter. Default: {options['f_part']}",
    )
    parser.add_argument(
        "--name-prefix",
        default=options["name_prefix"],
        help=f"Prefix for HMS Grid names. Default: {options['name_prefix']}",
    )
    parser.add_argument(
        "--hms-version",
        default=options["hms_version"],
        help=f"HMS .grid manager Version value. Default: {options['hms_version']}",
    )
    parser.add_argument(
        "--grid-type",
        default=options["grid_type"],
        help=f"HMS Grid Type value. Default: {options['grid_type']}",
    )
    parser.add_argument(
        "--description",
        default=options["description"],
        help="HMS Description value for each Grid block.",
    )
    parser.add_argument(
        "--data-source-type",
        default=options["data_source_type"],
        help=f"HMS Data Source Type value. Default: {options['data_source_type']}",
    )
    parser.add_argument(
        "--filepath-separator",
        default=options["filepath_separator"],
        help=r"HMS Filepath Separator value. Default: \ ",
    )
    parser.add_argument(
        "--undefined-threshold",
        type=float,
        default=float(options["undefined_threshold"]),
        help=(
            "Grid values at or below this threshold are treated as missing. "
            f"Default: {options['undefined_threshold']}"
        ),
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        default=Path(options["log_file"]) if options["log_file"] else None,
        help="Progress log file. Default: output filename with .log extension.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=int(options["workers"]),
        help=(
            "Number of DSS files to process in parallel. Use 1 for serial. "
            f"Default: {options['workers']}"
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=options["limit"],
        help="Process only the first N DSS files. This is useful for checking settings.",
    )
    parser.add_argument(
        "--center-method",
        choices=("hms", "python"),
        default=options["center_method"],
        help=(
            "Storm center source. hms: HEC-HMS's own code through HmsStormCenter.java, "
            "with the Python calculation as a cross-check. python: Python only. "
            f"Default: {options['center_method']}"
        ),
    )
    parser.add_argument(
        "--hms-dir",
        type=Path,
        default=Path(options["hms_dir"]),
        help=f"HEC-HMS installation folder. Default: {options['hms_dir']}",
    )
    parser.add_argument(
        "--javac",
        default=options["javac"],
        help="javac from a JDK 17 or later. Default: JAVA_HOME, then PATH.",
    )
    parser.add_argument(
        "--center-report",
        type=Path,
        default=Path(options["center_report"]) if options["center_report"] else None,
        help="Storm center CSV. Default: output filename with _storm_centers.csv.",
    )
    parser.add_argument(
        "--tie-tolerance",
        type=float,
        default=float(options["tie_tolerance"]),
        help=(
            "Cells within this accumulation of the maximum count as tied in the "
            f"cross-check, in DSS data units. Default: {options['tie_tolerance']}"
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])

    if not args.input_dir.exists():
        print(f"Input folder does not exist: {args.input_dir}", file=sys.stderr)
        return 2

    log_file = args.log_file or default_log_file(args.output)
    logger = setup_logger(log_file)
    logger.info("Starting HMS .grid generation")
    logger.info("Input folder: %s", args.input_dir)
    logger.info("Output file: %s", args.output)
    logger.info("Log file: %s", log_file)

    try:
        HecDss.set_global_debug_level(1)
    except Exception:
        pass

    dss_files = dss_files_under(args.input_dir)
    if args.limit is not None:
        dss_files = dss_files[: args.limit]

    if not dss_files:
        logger.error("No DSS files found under: %s", args.input_dir)
        return 2

    total_files = len(dss_files)
    worker_count = max(1, min(int(args.workers), total_files))
    settings = ProcessingSettings(
        c_part=args.c_part,
        f_part=args.f_part,
        a_part=args.a_part,
        b_part=args.b_part,
        name_prefix=args.name_prefix,
        undefined_threshold=args.undefined_threshold,
        tie_tolerance=args.tie_tolerance,
    )

    logger.info("Storm center method: %s", args.center_method)
    logger.info("DSS files found: %s", total_files)
    logger.info("Worker processes: %s", worker_count)

    entries_by_index: dict[int, GridEntry] = {}
    failures: list[tuple[Path, str]] = []
    start_time = perf_counter()

    if worker_count == 1:
        for index, dss_file in enumerate(dss_files, start=1):
            logger.info("[%s/%s] Reading %s", index, total_files, dss_file)
            result = process_dss_file(index, total_files, dss_file, settings)
            if result.entry is None:
                failures.append((result.dss_file, result.error or "Unknown error"))
                logger.error(
                    "[%s/%s] Skipped %s after %.1fs: %s",
                    result.index,
                    result.total,
                    result.dss_file,
                    result.elapsed_seconds,
                    result.error,
                )
            else:
                entries_by_index[result.index] = result.entry
                logger.info(
                    "[%s/%s] Done %s in %.1fs. Python center=(%.1f, %.1f)",
                    result.index,
                    result.total,
                    result.dss_file,
                    result.elapsed_seconds,
                    result.entry.storm_center_x,
                    result.entry.storm_center_y,
                )
    else:
        with concurrent.futures.ProcessPoolExecutor(max_workers=worker_count) as executor:
            futures = {
                executor.submit(process_dss_file, index, total_files, dss_file, settings): (
                    index,
                    dss_file,
                )
                for index, dss_file in enumerate(dss_files, start=1)
            }
            for future in concurrent.futures.as_completed(futures):
                index, dss_file = futures[future]
                logger.info("[%s/%s] Finished worker for %s", index, total_files, dss_file)
                try:
                    result = future.result()
                except Exception as exc:
                    failures.append((dss_file, str(exc)))
                    logger.error(
                        "[%s/%s] Skipped %s: %s",
                        index,
                        total_files,
                        dss_file,
                        exc,
                    )
                    continue

                if result.entry is None:
                    failures.append((result.dss_file, result.error or "Unknown error"))
                    logger.error(
                        "[%s/%s] Skipped %s after %.1fs: %s",
                        result.index,
                        result.total,
                        result.dss_file,
                        result.elapsed_seconds,
                        result.error,
                    )
                else:
                    entries_by_index[result.index] = result.entry
                    logger.info(
                        "[%s/%s] Done %s in %.1fs. Python center=(%.1f, %.1f)",
                        result.index,
                        result.total,
                        result.dss_file,
                        result.elapsed_seconds,
                        result.entry.storm_center_x,
                        result.entry.storm_center_y,
                    )

    entries = [entries_by_index[index] for index in sorted(entries_by_index)]

    if not entries:
        logger.error("No .grid entries were created.")
        return 1

    if args.center_method == "hms":
        try:
            centers = hms_storm_centers(entries, args.hms_dir, args.javac, logger)
            entries = apply_hms_centers(entries, centers)
        except Exception as exc:
            logger.error("HEC-HMS storm center calculation failed: %s", exc)
            logger.error("No .grid file was written. Use --center-method python to skip HEC-HMS.")
            return 1
        checks = {status: [e for e in entries if e.center_check == status] for status in ("match", "tie", "mismatch")}
        logger.info(
            "HEC-HMS vs Python storm centers: %s match, %s tie, %s mismatch",
            len(checks["match"]),
            len(checks["tie"]),
            len(checks["mismatch"]),
        )
        for entry in checks["tie"]:
            logger.info(
                "  tie %s: HEC-HMS (%.1f, %.1f), Python (%.1f, %.1f), %s cells within %.3g of the maximum",
                entry.name,
                entry.storm_center_x,
                entry.storm_center_y,
                entry.python_center_x,
                entry.python_center_y,
                len(entry.python_near_max_centers),
                args.tie_tolerance,
            )
        for entry in checks["mismatch"]:
            logger.error(
                "  mismatch %s: HEC-HMS (%.1f, %.1f), Python (%.1f, %.1f)",
                entry.name,
                entry.storm_center_x,
                entry.storm_center_y,
                entry.python_center_x,
                entry.python_center_y,
            )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    output_text = render_grid_file(
        entries,
        datetime.now(),
        hms_version=args.hms_version,
        grid_type=args.grid_type,
        description=args.description,
        data_source_type=args.data_source_type,
        filepath_separator=args.filepath_separator,
    )
    args.output.write_text(output_text, encoding="utf-8")
    center_report = args.center_report or default_center_report(args.output)
    write_center_report(center_report, entries)

    elapsed = perf_counter() - start_time
    logger.info("Wrote %s grid entries to %s", len(entries), args.output)
    logger.info("Storm center report: %s", center_report)
    logger.info("Elapsed time: %.1fs", elapsed)
    if any(entry.center_check == "mismatch" for entry in entries):
        logger.error("HEC-HMS and Python storm centers disagree beyond ties. Review the report.")
        return 1
    if failures:
        logger.error("Skipped %s DSS file(s):", len(failures))
        for path, reason in failures:
            logger.error("  %s: %s", path, reason)
        return 1

    logger.info("Completed without skipped DSS files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
