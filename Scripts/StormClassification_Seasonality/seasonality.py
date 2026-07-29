#!/usr/bin/env python3
"""
Step 3: Seasonality distributions for the classified storm catalog.

For each storm type (TC, NT, and ALL combined) this script counts storm
occurrences by day of year (DOY) and builds the cumulative distribution
that feeds seasonal sampling in stochastic storm transposition.

Padding: each storm can contribute counts to a window of +/- N days around
its start date. The default is 7 days (a 15-day window), which matches the
USACE CatalogSeasonalityDistributions.py convention. Set PADDING_DAYS = 0
(or pass --padding 0) for the strict one-count-per-storm version, where the
sum of counts equals the number of storms exactly.

The padded window is generated from real calendar dates, not DOY
arithmetic. That keeps year boundaries and leap years honest: a January 3
storm padded by 7 days reaches back into late December of the previous
year, and December 31 of a leap year lands on DOY 366. The result is
numerically identical to the USACE script for the same storms and padding.

Counts are keyed to each storm's START date, per the FFRD SOP, which asks
for "the daily frequency of occurrence for each storm type based on the
start date of the events in the storm catalog."

Input  : output/classified_storms.csv   (from classify_storms.py)
Outputs: DOY count and cumulative CSVs for TC, NT, ALL
         plots (histograms, CDF overlay, month-year heatmap, stripes)
         seasonality_summary.txt with verification checks

Usage:
    python seasonality.py
    python seasonality.py --padding 0
    python seasonality.py --padding 10 --output-dir output/seasonality_10day

Author: Mohsen Tahmasebi Nasab
AECOM FFRD Upper Tennessee Validation Basin Study
"""

import argparse
import csv
import sys
from collections import Counter, OrderedDict
from datetime import datetime, timedelta
from pathlib import Path

# ===========================================================================
# USER SETTINGS
# Edit these for your project, or override them on the command line.
# ===========================================================================

# Classified storm catalog from step 2.
CLASSIFIED_CSV = r"output\classified_storms.csv"

# Output directory for the seasonality tables and plots.
OUTPUT_DIR = r"output\seasonality"

# Days of padding on each side of every storm's start date.
# 7 gives a 15-day window per storm (the USACE default).
# 0 disables padding: one count per storm on its exact start DOY.
PADDING_DAYS = 7

# ===========================================================================
# END OF USER SETTINGS
# ===========================================================================


MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
MONTH_START_DOY = [1, 32, 60, 91, 121, 152, 182, 213, 244, 274, 305, 335]

# DOY -> "MM/DD" label (non-leap calendar; DOY 366 is Dec 31 of a leap year)
DOY_LABEL = {}
for _m in range(1, 13):
    for _d in range(1, 32):
        try:
            DOY_LABEL[datetime(2001, _m, _d).timetuple().tm_yday] = f"{_m:02d}/{_d:02d}"
        except ValueError:
            pass
DOY_LABEL[366] = "12/31*"


# ---------------------------------------------------------------------------
# Loading and counting
# ---------------------------------------------------------------------------
def parse_start_date(raw):
    """Accept the catalog format plus a couple of common variants."""
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    try:
        return datetime.strptime(raw[:10], "%Y-%m-%d")
    except ValueError:
        sys.exit(f"ERROR: cannot parse date '{raw}'")


def load_storms(csv_path):
    """Read the classified catalog. Needs a start date and a classification."""
    storms = []
    with open(csv_path) as f:
        reader = csv.DictReader(f)
        date_col = "start_datetime" if "start_datetime" in reader.fieldnames else "storm_date"
        for row in reader:
            dt = parse_start_date(row[date_col].strip())
            storms.append({
                "start": dt,
                "doy": dt.timetuple().tm_yday,
                "month": dt.month,
                "year": dt.year,
                "classification": row["classification"].strip(),
            })
    return storms


def count_by_doy(storms, pad):
    """
    DOY occurrence counts. With padding, each storm contributes one count
    for every calendar date from (start - pad) to (start + pad).
    """
    counts = Counter()
    for s in storms:
        if pad == 0:
            counts[s["doy"]] += 1
        else:
            for offset in range(-pad, pad + 1):
                d = s["start"] + timedelta(days=offset)
                counts[d.timetuple().tm_yday] += 1

    last_doy = 366 if 366 in counts else 365
    return OrderedDict((d, counts.get(d, 0)) for d in range(1, last_doy + 1))


def cumulative(counts):
    """Cumulative fraction of counts through each DOY. Ends at exactly 1."""
    total = sum(counts.values())
    if total == 0:
        return OrderedDict((d, 0.0) for d in counts)
    out = OrderedDict()
    running = 0
    for d, v in counts.items():
        running += v
        out[d] = running / total
    return out


# ---------------------------------------------------------------------------
# CSV and summary writers
# ---------------------------------------------------------------------------
def write_counts_csv(counts, path, label):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["DOY", "MonthDay", "Count", "StormType"])
        for d, v in counts.items():
            w.writerow([d, DOY_LABEL.get(d, str(d)), v, label])


def write_cdf_csv(cdf, path, label):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["DOY", "MonthDay", "Cumulative", "StormType"])
        for d, v in cdf.items():
            w.writerow([d, DOY_LABEL.get(d, str(d)), f"{v:.6f}", label])


def write_summary(storms, groups, pad, path):
    """Verification checks plus a monthly breakdown, written to a text file."""
    tc = [s for s in storms if s["classification"] == "TC"]
    nt = [s for s in storms if s["classification"] == "NT"]
    multiplier = 2 * pad + 1

    lines = []
    lines.append("=" * 70)
    lines.append("SEASONALITY ANALYSIS SUMMARY")
    lines.append("=" * 70)
    lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    if pad == 0:
        lines.append("Padding:   none (one count per storm start date)")
    else:
        lines.append(f"Padding:   +/-{pad} days ({multiplier}-day window per storm, "
                     f"calendar-date based, leap year aware)")
    lines.append(f"Storms:    {len(storms)} total | {len(tc)} TC | {len(nt)} NT")
    lines.append("")

    lines.append("VERIFICATION")
    lines.append("-" * 70)
    for label, n_storms in [("TC", len(tc)), ("NT", len(nt)), ("ALL", len(storms))]:
        total = sum(groups[label]["counts"].values())
        expected = n_storms * multiplier
        status = "PASS" if total == expected else "FAIL"
        lines.append(f"{label:>4} count sum: {total:>6}   expected: {expected:>6} "
                     f"({n_storms} storms x {multiplier})   {status}")
    for label in ("TC", "NT", "ALL"):
        vals = list(groups[label]["cdf"].values())
        if not vals or sum(groups[label]["counts"].values()) == 0:
            lines.append(f"{label:>4} CDF endpoint: n/a (no storms of this type)")
        else:
            lines.append(f"{label:>4} CDF endpoint: {vals[-1]:.6f}   (must equal 1.000000)")
    lines.append("")

    lines.append("MONTHLY BREAKDOWN (storm start dates, unpadded)")
    lines.append("-" * 70)
    lines.append(f"{'Month':>8}  {'TC':>5}  {'NT':>5}  {'ALL':>5}")
    for m in range(1, 13):
        tcm = sum(1 for s in tc if s["month"] == m)
        ntm = sum(1 for s in nt if s["month"] == m)
        lines.append(f"{MONTH_NAMES[m - 1]:>8}  {tcm:>5}  {ntm:>5}  {tcm + ntm:>5}")
    lines.append(f"{'TOTAL':>8}  {len(tc):>5}  {len(nt):>5}  {len(storms):>5}")
    lines.append("")

    lines.append("PEAK SEASON (from padded counts)" if pad else "PEAK SEASON")
    lines.append("-" * 70)
    for label, subset in [("TC", tc), ("NT", nt)]:
        if not subset:
            continue
        counts = groups[label]["counts"]
        peak = max(counts, key=counts.get)
        doys = [s["doy"] for s in subset]
        lines.append(f"{label}: start dates span DOY {min(doys)} "
                     f"({DOY_LABEL.get(min(doys))}) to {max(doys)} "
                     f"({DOY_LABEL.get(max(doys))}); "
                     f"peak at DOY {peak} ({DOY_LABEL.get(peak)}) "
                     f"with {counts[peak]} counts")

    with open(path, "w") as f:
        f.write("\n".join(lines))
    return lines


# ---------------------------------------------------------------------------
# Plots
# ---------------------------------------------------------------------------
FULL_NAMES = {"TC": "Tropical cyclone (TC)", "NT": "Non-tropical (NT)",
              "ALL": "All storms"}
FULL_NAMES_LOWER = {"TC": "tropical cyclone (TC)", "NT": "non-tropical (NT)",
                    "ALL": "all storm"}


def get_matplotlib():
    try:
        import matplotlib
        matplotlib.use("Agg")
        from plot_style import apply_style
        apply_style()
        import matplotlib.pyplot as plt
        import numpy as np
        return plt, np
    except ImportError:
        print("  matplotlib/numpy not installed, skipping plots")
        return None, None


def format_doy_axis(ax, last_doy=366):
    ax.set_xticks(MONTH_START_DOY)
    ax.set_xticklabels(MONTH_NAMES, fontsize=9)
    ax.set_xlim(1, last_doy)
    ax.grid(True, alpha=0.3, linestyle=":")


def padding_note(pad):
    if pad == 0:
        return "Each event contributes one count on its start date."
    return (f"Each event contributes counts over a {2 * pad + 1}-day window "
            f"centered on its start date (±{pad} days).")


def plot_histogram(counts, n, label, color, pad, path):
    plt, _ = get_matplotlib()
    if not plt:
        return
    fig, ax = plt.subplots(figsize=(12, 5))
    doys = list(counts.keys())
    ax.bar(doys, [counts[d] for d in doys], width=1, color=color, alpha=0.85)
    ax.set_title(f"{FULL_NAMES[label]} events by day of year (n = {n})")
    ax.set_xlabel("Day of year")
    ax.set_ylabel("Number of events")
    ax.set_ylim(0, max(max(counts.values()), 1) * 1.08)
    from matplotlib.ticker import MaxNLocator
    ax.yaxis.set_major_locator(MaxNLocator(integer=True))
    format_doy_axis(ax, max(doys))
    fig.text(0.5, -0.02, padding_note(pad), ha="center", fontsize=8.5,
             color="#555555")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def plot_stacked(tc_counts, nt_counts, n_tc, n_nt, pad, path):
    plt, _ = get_matplotlib()
    if not plt:
        return
    from plot_style import TC_COLOR, NT_COLOR
    fig, ax = plt.subplots(figsize=(12, 5))
    doys = sorted(set(tc_counts) | set(nt_counts))
    nt_vals = [nt_counts.get(d, 0) for d in doys]
    tc_vals = [tc_counts.get(d, 0) for d in doys]
    ax.bar(doys, nt_vals, width=1, color=NT_COLOR, alpha=0.7,
           label=f"Non-tropical, n = {n_nt}")
    ax.bar(doys, tc_vals, width=1, color=TC_COLOR, alpha=0.85, bottom=nt_vals,
           label=f"Tropical cyclone, n = {n_tc}")
    ax.set_title(f"Storm catalog events by day of year (n = {n_tc + n_nt})")
    ax.set_xlabel("Day of year")
    ax.set_ylabel("Number of events")
    ax.legend(loc="upper left")
    stack_max = max(t + n for t, n in zip(tc_vals, nt_vals)) if doys else 1
    ax.set_ylim(0, max(stack_max, 1) * 1.08)
    from matplotlib.ticker import MaxNLocator
    ax.yaxis.set_major_locator(MaxNLocator(integer=True))
    format_doy_axis(ax, max(doys))
    fig.text(0.5, -0.02, padding_note(pad), ha="center", fontsize=8.5,
             color="#555555")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def plot_cdfs(groups, counts_n, pad, path):
    plt, _ = get_matplotlib()
    if not plt:
        return
    from plot_style import TC_COLOR, NT_COLOR, ALL_COLOR
    fig, ax = plt.subplots(figsize=(12, 5))
    styles = {"TC": (TC_COLOR, "-", 2.2), "NT": (NT_COLOR, "-", 2.2),
              "ALL": (ALL_COLOR, "--", 1.5)}
    all_doys = sorted(set().union(*(groups[k]["cdf"].keys() for k in groups)))
    for label in ("TC", "NT", "ALL"):
        cdf = groups[label]["cdf"]
        # Carry the last value forward across DOYs a type has no entry for
        vals, last = [], 0.0
        for d in all_doys:
            last = cdf.get(d, last)
            vals.append(last)
        color, ls, lw = styles[label]
        ax.plot(all_doys, vals, color=color, linestyle=ls, linewidth=lw,
                label=f"{FULL_NAMES[label]}, n = {counts_n[label]}")
    ax.set_title("Cumulative seasonal distribution of storm occurrence")
    ax.set_xlabel("Day of year")
    ax.set_ylabel("Cumulative fraction of events")
    ax.set_ylim(0, 1.05)
    ax.legend(loc="upper left")
    format_doy_axis(ax, max(all_doys))
    fig.text(0.5, -0.02, padding_note(pad), ha="center", fontsize=8.5,
             color="#555555")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def plot_heatmap(storms, path):
    """Month-by-year heatmaps (TC, NT, ALL) from unpadded start dates."""
    plt, np = get_matplotlib()
    if not plt:
        return
    tc = [s for s in storms if s["classification"] == "TC"]
    nt = [s for s in storms if s["classification"] == "NT"]
    years = sorted(set(s["year"] for s in storms))
    yindex = {y: i for i, y in enumerate(years)}

    def matrix(subset):
        m = np.zeros((len(years), 12), dtype=int)
        for s in subset:
            m[yindex[s["year"]], s["month"] - 1] += 1
        return m

    panels = [(matrix(tc), f"Tropical cyclone (n = {len(tc)})", "Reds"),
              (matrix(nt), f"Non-tropical (n = {len(nt)})", "Blues"),
              (matrix(storms), f"All storms (n = {len(storms)})", "Greys")]

    fig, axes = plt.subplots(1, 3, figsize=(20, max(8, len(years) * 0.35)),
                             gridspec_kw={"wspace": 0.3})
    for ax, (mat, title, cmap) in zip(axes, panels):
        im = ax.imshow(mat, aspect="auto", cmap=cmap, interpolation="nearest",
                       vmin=0, vmax=max(mat.max(), 1))
        ax.set_xticks(range(12))
        ax.set_xticklabels(MONTH_NAMES, fontsize=9, rotation=45, ha="right")
        ax.set_yticks(range(len(years)))
        ax.set_yticklabels(years, fontsize=8)
        ax.set_title(title, fontsize=11, pad=10)
        for i in range(len(years)):
            for j in range(12):
                if mat[i, j] > 0:
                    color = "white" if mat[i, j] > mat.max() * 0.6 else "#333333"
                    ax.text(j, i, str(mat[i, j]), ha="center", va="center",
                            fontsize=7, color=color)
        fig.colorbar(im, ax=ax, shrink=0.6, label="Number of events", pad=0.02)
    fig.suptitle("Monthly storm counts by year and storm type",
                 fontsize=13, y=1.01)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def plot_stripes(counts, label, n, base_color, pad, path):
    """Climate-stripe style strip: one colored band per DOY."""
    plt, np = get_matplotlib()
    if not plt:
        return
    import matplotlib.colors as mcolors

    ramps = {
        "red": ["#ffffff", "#f6e3e0", "#e5a99f", "#b2432f", "#6e2418", "#38100a"],
        "blue": ["#ffffff", "#dde8f4", "#9dbde0", "#3d6cb3", "#1e3d6e", "#0c1f3d"],
    }
    cmap = mcolors.LinearSegmentedColormap.from_list(f"{label}_stripe",
                                                     ramps[base_color], N=256)
    doys = list(counts.keys())
    vals = np.array([counts[d] for d in doys], dtype=float).reshape(1, -1)

    fig, ax = plt.subplots(figsize=(14, 2.5))
    im = ax.imshow(vals, aspect="auto", cmap=cmap, vmin=0, vmax=max(vals.max(), 1),
                   extent=[0.5, len(doys) + 0.5, 0, 1], interpolation="nearest")
    ax.set_xticks(MONTH_START_DOY)
    ax.set_xticklabels(MONTH_NAMES, fontsize=10)
    ax.set_xlim(0.5, len(doys) + 0.5)
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.set_title(f"Daily occurrence intensity, {FULL_NAMES_LOWER[label]} "
                 f"events (n = {n})", pad=12)
    cbar = fig.colorbar(im, ax=ax, orientation="horizontal", pad=0.25,
                        shrink=0.5, aspect=30)
    cbar.set_label("Number of events per day of year", fontsize=9)
    cbar.ax.tick_params(labelsize=8)
    fig.text(0.5, -0.05, padding_note(pad), ha="center", fontsize=8.5,
             color="#555555")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Storm catalog seasonality distributions.")
    parser.add_argument("--input", default=CLASSIFIED_CSV,
                        help="Classified storm catalog CSV from step 2")
    parser.add_argument("--output-dir", default=OUTPUT_DIR, help="Output directory")
    parser.add_argument("--padding", type=int, default=PADDING_DAYS,
                        help="Days of padding on each side of the start date "
                             f"(default {PADDING_DAYS}; 0 disables padding)")
    args = parser.parse_args()

    pad = args.padding
    if pad < 0:
        sys.exit("ERROR: --padding must be zero or positive")
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("STORM CATALOG SEASONALITY")
    print("=" * 60)
    print(f"Input:   {args.input}")
    print(f"Output:  {out_dir}")
    print(f"Padding: {padding_note(pad)}")
    print()

    storms = load_storms(args.input)
    tc = [s for s in storms if s["classification"] == "TC"]
    nt = [s for s in storms if s["classification"] == "NT"]
    print(f"Loaded {len(storms)} storms: {len(tc)} TC, {len(nt)} NT")

    groups = {}
    for label, subset in [("TC", tc), ("NT", nt), ("ALL", storms)]:
        counts = count_by_doy(subset, pad)
        groups[label] = {"counts": counts, "cdf": cumulative(counts)}

    # Tables
    for label in ("TC", "NT", "ALL"):
        write_counts_csv(groups[label]["counts"],
                         out_dir / f"{label}_seasonality_count.csv", label)
        write_cdf_csv(groups[label]["cdf"],
                      out_dir / f"{label}_seasonality_cumulative.csv", label)

    # Summary with verification checks (also echoed to the console)
    summary = write_summary(storms, groups, pad,
                            out_dir / "seasonality_summary.txt")
    print()
    print("\n".join(summary[:20]))

    # Plots
    print("\nGenerating plots...")
    counts_n = {"TC": len(tc), "NT": len(nt), "ALL": len(storms)}
    from plot_style import TC_COLOR, NT_COLOR
    plot_histogram(groups["TC"]["counts"], len(tc), "TC", TC_COLOR, pad,
                   out_dir / "plot_tc_seasonality.png")
    plot_histogram(groups["NT"]["counts"], len(nt), "NT", NT_COLOR, pad,
                   out_dir / "plot_nt_seasonality.png")
    plot_stacked(groups["TC"]["counts"], groups["NT"]["counts"], len(tc), len(nt),
                 pad, out_dir / "plot_combined_seasonality.png")
    plot_cdfs(groups, counts_n, pad, out_dir / "plot_cdf_seasonality.png")
    plot_heatmap(storms, out_dir / "plot_heatmap_seasonality.png")
    plot_stripes(groups["TC"]["counts"], "TC", len(tc), "red", pad,
                 out_dir / "plot_tc_stripes.png")
    plot_stripes(groups["NT"]["counts"], "NT", len(nt), "blue", pad,
                 out_dir / "plot_nt_stripes.png")

    print(f"\nAll outputs written to {out_dir}")


if __name__ == "__main__":
    main()
