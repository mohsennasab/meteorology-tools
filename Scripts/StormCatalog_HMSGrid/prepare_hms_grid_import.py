from __future__ import annotations

import argparse
import concurrent.futures
import csv
import logging
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from time import perf_counter


DEFAULT_C_PART = "PRECIPITATION"
DEFAULT_F_PART = "AORC"
DEFAULT_HMS_VERSION = "4.13"
DEFAULT_COPY_WORKERS = 4
DEFAULT_GRID_WORKERS = 6
FILENAME_PATTERN = re.compile(
    r"^\d{8}_\d+hr_[a-z0-9-]+_r\d{3}\.dss$"
)
TOKEN_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


@dataclass(frozen=True)
class StormFile:
    rank: int
    classification: str
    start_datetime: str
    duration_hours: int
    source_file: Path
    destination_file: Path
    source_size_bytes: int
    copy_status: str = "pending"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Copy and rename a ranked storm catalog, then build an HEC-HMS "
            ".grid file for the copied DSS files."
        )
    )
    parser.add_argument(
        "--catalog-dir",
        type=Path,
        required=True,
        help="Ranked StormHub event folder that contains one folder per rank.",
    )
    parser.add_argument(
        "--classified-csv",
        type=Path,
        required=True,
        help="Verified classification CSV with event_id and classification fields.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Folder for the flat DSS collection, mapping, logs, and HMS grid file.",
    )
    parser.add_argument(
        "--grid-filename",
        default=None,
        help=(
            "Name of the output .grid file. The default is based on the basin "
            "folder above the catalog."
        ),
    )
    parser.add_argument(
        "--tc-token",
        default="tc",
        help="Filename field for Tropical Cyclone storms. Default: tc",
    )
    parser.add_argument(
        "--nt-token",
        default="nt",
        help="Filename field for Non-Tropical storms. Default: nt",
    )
    parser.add_argument(
        "--a-part",
        default=None,
        help="Optional DSS A-part filter for grid generation, such as SHG1K.",
    )
    parser.add_argument(
        "--b-part",
        default=None,
        help="Optional DSS B-part filter for grid generation.",
    )
    parser.add_argument(
        "--c-part",
        default=DEFAULT_C_PART,
        help=f"DSS C-part filter. Default: {DEFAULT_C_PART}",
    )
    parser.add_argument(
        "--f-part",
        default=DEFAULT_F_PART,
        help=f"DSS F-part filter. Default: {DEFAULT_F_PART}",
    )
    parser.add_argument(
        "--hms-version",
        default=DEFAULT_HMS_VERSION,
        help=f"HEC-HMS version written to the grid file. Default: {DEFAULT_HMS_VERSION}",
    )
    parser.add_argument(
        "--copy-workers",
        type=int,
        default=DEFAULT_COPY_WORKERS,
        help=f"Number of parallel DSS copy workers. Default: {DEFAULT_COPY_WORKERS}",
    )
    parser.add_argument(
        "--grid-workers",
        type=int,
        default=DEFAULT_GRID_WORKERS,
        help=f"Number of parallel DSS grid readers. Default: {DEFAULT_GRID_WORKERS}",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process only the first N ranks for a test run.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace expected output files that already exist.",
    )
    parser.add_argument(
        "--skip-grid",
        action="store_true",
        help="Copy and rename the DSS files without creating the HMS grid file.",
    )
    return parser.parse_args(argv)


def resolved(path: Path) -> Path:
    return path.expanduser().resolve()


def setup_logger(log_file: Path) -> logging.Logger:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("hms_grid_import_preparation")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.propagate = False
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

    file_handler = logging.FileHandler(log_file, mode="w", encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    return logger


def validate_token(token: str, option_name: str) -> str:
    value = token.strip().lower()
    if not TOKEN_PATTERN.fullmatch(value):
        raise ValueError(
            f"{option_name} must contain lowercase letters, numbers, or "
            f"single hyphens: {token}"
        )
    return value


def parse_start_date(value: str, rank: int) -> str:
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        return datetime.fromisoformat(text).strftime("%Y%m%d")
    except ValueError as exc:
        raise ValueError(
            f"Rank {rank} has an invalid start_datetime value: {value}"
        ) from exc


def parse_duration(value: str, rank: int) -> int:
    try:
        duration = float(value)
    except ValueError as exc:
        raise ValueError(
            f"Rank {rank} has an invalid duration_hours value: {value}"
        ) from exc
    rounded = round(duration)
    if duration <= 0 or abs(duration - rounded) > 1.0e-6:
        raise ValueError(
            f"Rank {rank} duration_hours must be a positive whole number: {value}"
        )
    return int(rounded)


def classification_rows(classified_csv: Path) -> dict[int, dict[str, str]]:
    required = {"event_id", "start_datetime", "duration_hours", "classification"}
    with classified_csv.open("r", encoding="utf-8-sig", newline="") as csv_file:
        reader = csv.DictReader(csv_file)
        fields = set(reader.fieldnames or [])
        missing = sorted(required - fields)
        if missing:
            raise ValueError(
                f"Classification CSV is missing fields: {', '.join(missing)}"
            )

        rows: dict[int, dict[str, str]] = {}
        for line_number, row in enumerate(reader, start=2):
            event_text = (row.get("event_id") or "").strip()
            try:
                rank = int(event_text)
            except ValueError as exc:
                raise ValueError(
                    f"Invalid event_id on CSV line {line_number}: {event_text}"
                ) from exc
            if rank <= 0:
                raise ValueError(
                    f"event_id must be positive on CSV line {line_number}: {rank}"
                )
            if rank in rows:
                raise ValueError(f"Duplicate event_id in classification CSV: {rank}")
            rows[rank] = row
    return rows


def dss_files_in_rank_folder(rank_folder: Path) -> list[Path]:
    return sorted(
        (
            path
            for path in rank_folder.iterdir()
            if path.is_file() and path.suffix.lower() == ".dss"
        ),
        key=lambda path: path.name.lower(),
    )


def build_inventory(
    catalog_dir: Path,
    classified_csv: Path,
    dss_output_dir: Path,
    tc_token: str,
    nt_token: str,
    limit: int | None,
) -> list[StormFile]:
    rows = classification_rows(classified_csv)
    rank_folders: dict[int, Path] = {}

    for folder in catalog_dir.iterdir():
        if not folder.is_dir():
            continue
        try:
            rank = int(folder.name)
        except ValueError:
            continue
        if rank <= 0:
            continue
        if rank in rank_folders:
            raise ValueError(f"Duplicate rank folder: {rank}")
        rank_folders[rank] = folder

    catalog_ranks = set(rank_folders)
    classified_ranks = set(rows)
    if catalog_ranks != classified_ranks:
        missing_classifications = sorted(catalog_ranks - classified_ranks)
        missing_rank_folders = sorted(classified_ranks - catalog_ranks)
        details: list[str] = []
        if missing_classifications:
            details.append(
                "catalog ranks without classifications: "
                + ", ".join(str(rank) for rank in missing_classifications[:20])
            )
        if missing_rank_folders:
            details.append(
                "classification ranks without catalog folders: "
                + ", ".join(str(rank) for rank in missing_rank_folders[:20])
            )
        raise ValueError("Catalog and classification ranks differ. " + ". ".join(details))

    ranks = sorted(catalog_ranks)
    if limit is not None:
        if limit <= 0:
            raise ValueError("--limit must be greater than zero")
        ranks = ranks[:limit]

    inventory: list[StormFile] = []
    destination_names: set[str] = set()
    token_by_class = {"TC": tc_token, "NT": nt_token}

    for rank in ranks:
        row = rows[rank]
        classification = (row.get("classification") or "").strip().upper()
        if classification not in token_by_class:
            raise ValueError(
                f"Rank {rank} classification must be TC or NT: {classification}"
            )

        source_files = dss_files_in_rank_folder(rank_folders[rank])
        if len(source_files) != 1:
            raise ValueError(
                f"Rank {rank} must contain one DSS file, found {len(source_files)}"
            )

        start_date = parse_start_date(row.get("start_datetime") or "", rank)
        duration = parse_duration(row.get("duration_hours") or "", rank)
        storm_token = token_by_class[classification]
        new_name = (
            f"{start_date}_{duration}hr_{storm_token}_r{rank:03d}.dss"
        ).lower()
        if not FILENAME_PATTERN.fullmatch(new_name):
            raise ValueError(f"Generated filename does not meet the naming rules: {new_name}")
        if new_name in destination_names:
            raise ValueError(f"Generated filename is not unique: {new_name}")
        destination_names.add(new_name)

        source_file = source_files[0].resolve()
        inventory.append(
            StormFile(
                rank=rank,
                classification=classification,
                start_datetime=row.get("start_datetime") or "",
                duration_hours=duration,
                source_file=source_file,
                destination_file=(dss_output_dir / new_name).resolve(),
                source_size_bytes=source_file.stat().st_size,
            )
        )

    return inventory


def validate_output_state(
    inventory: list[StormFile],
    dss_output_dir: Path,
    overwrite: bool,
) -> None:
    expected = {storm.destination_file.name.lower() for storm in inventory}
    if dss_output_dir.exists():
        existing_dss = {
            path.name.lower()
            for path in dss_output_dir.iterdir()
            if path.is_file() and path.suffix.lower() == ".dss"
        }
        unexpected = sorted(existing_dss - expected)
        if unexpected:
            raise FileExistsError(
                "The DSS output folder contains files outside this run: "
                + ", ".join(unexpected[:20])
            )
        collisions = sorted(existing_dss & expected)
        if collisions and not overwrite:
            raise FileExistsError(
                "Expected DSS outputs already exist. Use --overwrite to replace them."
            )


def copy_one(storm: StormFile) -> StormFile:
    shutil.copy2(storm.source_file, storm.destination_file)
    destination_size = storm.destination_file.stat().st_size
    if destination_size != storm.source_size_bytes:
        raise OSError(
            f"Copied file size differs for rank {storm.rank}: "
            f"{storm.source_size_bytes} != {destination_size}"
        )
    return replace(storm, copy_status="copied")


def copy_inventory(
    inventory: list[StormFile],
    dss_output_dir: Path,
    copy_workers: int,
    logger: logging.Logger,
) -> list[StormFile]:
    dss_output_dir.mkdir(parents=True, exist_ok=True)
    worker_count = max(1, min(copy_workers, len(inventory)))
    copied_by_rank: dict[int, StormFile] = {}
    logger.info("Copy workers: %s", worker_count)

    with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
        future_to_storm = {
            executor.submit(copy_one, storm): storm for storm in inventory
        }
        for completed, future in enumerate(
            concurrent.futures.as_completed(future_to_storm),
            start=1,
        ):
            storm = future_to_storm[future]
            copied = future.result()
            copied_by_rank[copied.rank] = copied
            logger.info(
                "[%s/%s] Copied rank %s to %s",
                completed,
                len(inventory),
                copied.rank,
                copied.destination_file.name,
            )

    return [copied_by_rank[storm.rank] for storm in inventory]


def write_mapping(
    mapping_file: Path,
    inventory: list[StormFile],
    catalog_dir: Path,
    output_dir: Path,
) -> None:
    mapping_file.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "rank",
        "classification",
        "start_datetime",
        "duration_hours",
        "old_filename",
        "old_relative_path",
        "old_full_path",
        "new_filename",
        "new_relative_path",
        "new_full_path",
        "source_size_bytes",
        "copy_status",
    ]
    with mapping_file.open("w", encoding="utf-8-sig", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fields)
        writer.writeheader()
        for storm in inventory:
            writer.writerow(
                {
                    "rank": storm.rank,
                    "classification": storm.classification,
                    "start_datetime": storm.start_datetime,
                    "duration_hours": storm.duration_hours,
                    "old_filename": storm.source_file.name,
                    "old_relative_path": storm.source_file.relative_to(catalog_dir),
                    "old_full_path": storm.source_file,
                    "new_filename": storm.destination_file.name,
                    "new_relative_path": storm.destination_file.relative_to(output_dir),
                    "new_full_path": storm.destination_file,
                    "source_size_bytes": storm.source_size_bytes,
                    "copy_status": storm.copy_status,
                }
            )


def run_grid_generator(
    script_file: Path,
    dss_output_dir: Path,
    grid_file: Path,
    grid_log_file: Path,
    args: argparse.Namespace,
    logger: logging.Logger,
) -> None:
    command = [
        sys.executable,
        str(script_file),
        "--input-dir",
        str(dss_output_dir),
        "--output",
        str(grid_file),
        "--log-file",
        str(grid_log_file),
        "--name-prefix",
        "",
        "--c-part",
        args.c_part,
        "--f-part",
        args.f_part,
        "--hms-version",
        args.hms_version,
        "--workers",
        str(max(1, args.grid_workers)),
    ]
    if args.a_part:
        command.extend(["--a-part", args.a_part])
    if args.b_part:
        command.extend(["--b-part", args.b_part])

    logger.info("Starting HMS grid generation")
    logger.info("Grid generator: %s", script_file)
    logger.info("Grid output: %s", grid_file)
    completed = subprocess.run(command, check=False)
    if completed.returncode != 0:
        raise RuntimeError(
            f"HMS grid generation failed with exit code {completed.returncode}. "
            f"See {grid_log_file}"
        )


def read_grid_centers(grid_file: Path) -> dict[str, tuple[str, str]]:
    centers: dict[str, tuple[str, str]] = {}
    current_name: str | None = None
    center_x: str | None = None
    center_y: str | None = None

    for raw_line in grid_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if raw_line.startswith("Grid: "):
            current_name = line.removeprefix("Grid: ").strip()
            center_x = None
            center_y = None
        elif line.startswith("Storm Center X:"):
            center_x = line.split(":", 1)[1].strip()
        elif line.startswith("Storm Center Y:"):
            center_y = line.split(":", 1)[1].strip()
        elif line == "End:" and current_name and center_x and center_y:
            centers[current_name] = (center_x, center_y)
            current_name = None
    return centers


def representative_storms(inventory: list[StormFile]) -> list[tuple[StormFile, str]]:
    selected: dict[int, tuple[StormFile, str]] = {}
    for classification in ("TC", "NT"):
        storms = [storm for storm in inventory if storm.classification == classification]
        if not storms:
            continue
        if len(storms) == 1:
            storm = storms[0]
            selected[storm.rank] = (storm, f"only {classification} storm")
            continue
        indices = [
            (0, f"highest-ranked {classification} storm"),
            (len(storms) // 2, f"middle-ranked {classification} storm"),
            (len(storms) - 1, f"lowest-ranked {classification} storm"),
        ]
        for index, reason in indices:
            storm = storms[index]
            selected[storm.rank] = (storm, reason)
    return [selected[rank] for rank in sorted(selected)]


def write_verification_file(
    verification_file: Path,
    inventory: list[StormFile],
    grid_file: Path,
) -> None:
    centers = read_grid_centers(grid_file)
    fields = [
        "rank",
        "classification",
        "selection_reason",
        "grid_name",
        "dss_filename",
        "generated_storm_center_x",
        "generated_storm_center_y",
        "hms_storm_center_x",
        "hms_storm_center_y",
        "verification_status",
        "notes",
    ]
    with verification_file.open("w", encoding="utf-8-sig", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fields)
        writer.writeheader()
        for storm, reason in representative_storms(inventory):
            grid_name = storm.destination_file.stem
            center_x, center_y = centers.get(grid_name, ("", ""))
            writer.writerow(
                {
                    "rank": storm.rank,
                    "classification": storm.classification,
                    "selection_reason": reason,
                    "grid_name": grid_name,
                    "dss_filename": storm.destination_file.name,
                    "generated_storm_center_x": center_x,
                    "generated_storm_center_y": center_y,
                    "hms_storm_center_x": "",
                    "hms_storm_center_y": "",
                    "verification_status": "pending",
                    "notes": "",
                }
            )


def default_grid_filename(catalog_dir: Path) -> str:
    basin_name = catalog_dir.parent.name.strip().lower()
    safe_name = re.sub(r"[^a-z0-9]+", "-", basin_name).strip("-")
    if not safe_name:
        safe_name = "storm"
    return f"{safe_name}_storm-catalog.grid"


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    catalog_dir = resolved(args.catalog_dir)
    classified_csv = resolved(args.classified_csv)
    output_dir = resolved(args.output_dir)
    dss_output_dir = output_dir / "dss"
    mapping_file = output_dir / "dss_name_mapping.csv"
    log_file = output_dir / "hms_grid_import.log"
    grid_log_file = output_dir / "hms_grid_generation.log"
    verification_file = output_dir / "storm_center_verification.csv"
    script_file = Path(__file__).with_name("generate_hms_grid.py").resolve()

    grid_name = args.grid_filename or default_grid_filename(catalog_dir)
    if Path(grid_name).name != grid_name or not grid_name.lower().endswith(".grid"):
        print("--grid-filename must be a filename with the .grid extension", file=sys.stderr)
        return 2
    grid_file = output_dir / grid_name

    if not catalog_dir.is_dir():
        print(f"Catalog folder does not exist: {catalog_dir}", file=sys.stderr)
        return 2
    if not classified_csv.is_file():
        print(f"Classification CSV does not exist: {classified_csv}", file=sys.stderr)
        return 2
    if not script_file.is_file():
        print(f"Grid generator does not exist: {script_file}", file=sys.stderr)
        return 2

    output_dir.mkdir(parents=True, exist_ok=True)
    logger = setup_logger(log_file)
    start_time = perf_counter()
    logger.info("Starting HMS grid import preparation")
    logger.info("Storm catalog used: %s", catalog_dir)
    logger.info("Verified classification CSV: %s", classified_csv)
    logger.info("New DSS files saved in: %s", dss_output_dir)
    logger.info("Output folder: %s", output_dir)

    try:
        tc_token = validate_token(args.tc_token, "--tc-token")
        nt_token = validate_token(args.nt_token, "--nt-token")
        if tc_token == nt_token:
            raise ValueError("--tc-token and --nt-token must be different")

        inventory = build_inventory(
            catalog_dir=catalog_dir,
            classified_csv=classified_csv,
            dss_output_dir=dss_output_dir,
            tc_token=tc_token,
            nt_token=nt_token,
            limit=args.limit,
        )
        validate_output_state(
            inventory=inventory,
            dss_output_dir=dss_output_dir,
            overwrite=args.overwrite,
        )
        for output_file in (mapping_file, grid_file, grid_log_file, verification_file):
            if output_file.exists() and not args.overwrite:
                raise FileExistsError(
                    f"Output already exists. Use --overwrite to replace it: {output_file}"
                )

        source_bytes = sum(storm.source_size_bytes for storm in inventory)
        logger.info("Storms validated: %s", len(inventory))
        logger.info("Source DSS bytes: %s", source_bytes)
        logger.info("Tropical Cyclone filename token: %s", tc_token)
        logger.info("Non-Tropical filename token: %s", nt_token)

        copied = copy_inventory(
            inventory=inventory,
            dss_output_dir=dss_output_dir,
            copy_workers=max(1, args.copy_workers),
            logger=logger,
        )
        write_mapping(
            mapping_file=mapping_file,
            inventory=copied,
            catalog_dir=catalog_dir,
            output_dir=output_dir,
        )
        logger.info("Name mapping: %s", mapping_file)

        if args.skip_grid:
            logger.info("Grid generation skipped by request")
        else:
            run_grid_generator(
                script_file=script_file,
                dss_output_dir=dss_output_dir,
                grid_file=grid_file,
                grid_log_file=grid_log_file,
                args=args,
                logger=logger,
            )
            write_verification_file(
                verification_file=verification_file,
                inventory=copied,
                grid_file=grid_file,
            )
            logger.info("Storm center verification file: %s", verification_file)

        copied_bytes = sum(storm.destination_file.stat().st_size for storm in copied)
        if copied_bytes != source_bytes:
            raise RuntimeError(
                f"Copied DSS byte total differs: {copied_bytes} != {source_bytes}"
            )
        logger.info("Copied DSS files: %s", len(copied))
        logger.info("Copied DSS bytes: %s", copied_bytes)
        logger.info("Elapsed time: %.1f seconds", perf_counter() - start_time)
        logger.info("Completed without errors")
        return 0
    except Exception:
        logger.exception("Preparation failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
