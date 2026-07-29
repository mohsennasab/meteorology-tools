"""
Shared figure style for all plots in this repository.

Call apply_style() once before creating any figure. It sets Arial as the
font, normal (not bold) weights throughout, and consistent sizes suitable
for reports and publications. If Arial is not installed (some Linux
systems), matplotlib falls back to the next font in the list.
"""


def apply_style():
    import matplotlib

    matplotlib.rcParams.update({
        "font.family": "sans-serif",
        "font.sans-serif": ["Arial", "Helvetica", "Liberation Sans", "DejaVu Sans"],
        "font.size": 10,
        "axes.titlesize": 12,
        "axes.titleweight": "normal",
        "axes.labelsize": 10,
        "axes.labelweight": "normal",
        "axes.linewidth": 0.8,
        "axes.edgecolor": "#444444",
        "axes.grid": False,
        "figure.titleweight": "normal",
        "xtick.labelsize": 9,
        "ytick.labelsize": 9,
        "legend.fontsize": 9,
        "legend.framealpha": 0.9,
        "legend.edgecolor": "#cccccc",
        "savefig.dpi": 300,
        "savefig.facecolor": "white",
        "savefig.bbox": "tight",
    })


# Consistent series colors across all figures
TC_COLOR = "#b2432f"      # muted red for tropical events
NT_COLOR = "#3d6cb3"      # muted blue for non-tropical events
ALL_COLOR = "#3c3c3c"     # dark gray for the combined series
