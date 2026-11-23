"""matplotlib charts for SimResult objects."""
from __future__ import annotations

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Patch

from .params import CELLS, FLOW_EDGES, MIN_PER_DAY, MIN_PER_WEEK, MODELS, STATION_INFO, STATIONS

# ---------------------------------------------------------------- palette
SURFACE, INK, INK2, MUTED, GRID, AXIS = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
MODEL_COLOR = dict(zip(MODELS, SERIES[:3]))
CELL_COLOR = {"Fab": SERIES[6], "Asm": SERIES[5]}
STATE_COLOR = {"Busy": "#2a78d6", "Setup": "#eda100", "WaitOp": "#e87ba4",
               "Idle": "#e1e0d9", "Failed": "#d03b3b", "PM": "#6250d6"}
STATE_LABEL = {"Busy": "Busy", "Setup": "Setup", "WaitOp": "Waiting for operator",
               "Idle": "Idle", "Failed": "Failed (repair)", "PM": "Planned maintenance"}
BLUES = LinearSegmentedColormap.from_list(
    "blues", ["#f0efec", "#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"])


def station_colors(cell):
    sts = [s for s in STATIONS if STATION_INFO[s]["cell"] == cell]
    return dict(zip(sts, SERIES))


def style():
    plt.rcParams.update({
        "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
        "axes.edgecolor": AXIS, "axes.labelcolor": INK2, "axes.titlecolor": INK,
        "axes.titlesize": 11, "axes.titleweight": "bold", "axes.labelsize": 9,
        "axes.spines.top": False, "axes.spines.right": False,
        "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6, "axes.axisbelow": True,
        "xtick.color": MUTED, "ytick.color": MUTED, "xtick.labelsize": 8, "ytick.labelsize": 8,
        "legend.frameon": False, "legend.fontsize": 8, "lines.linewidth": 1.6,
        "font.family": "sans-serif", "font.sans-serif": ["Helvetica", "Arial", "DejaVu Sans"],
        "figure.dpi": 110,
    })


style()


def _warm(ax, res):
    w = res.scenario.warmup_min / 60
    if w > 0:
        ax.axvspan(0, w, color=GRID, alpha=0.5, lw=0)
        ax.text(w / 2, 0.98, "warm-up", transform=ax.get_xaxis_transform(),
                ha="center", va="top", fontsize=7, color=MUTED)


# ---------------------------------------------------------------- time series
def daily_queue(res, ws, how="mean"):
    """Queue at ws per productive day, split by model (rows = day 1..n)."""
    tr = res.trace
    day = (tr.t // MIN_PER_DAY).astype(int) + 1
    cols = [f"Q {ws} {m}" for m in MODELS]
    d = tr[cols].groupby(day).agg(how)
    d.columns = MODELS
    d.index.name = "Day"
    return d


def plot_wave_levels(res, stations=None, how="mean"):
    """Wave levels: average queue per day at each station, stacked by model."""
    stations = stations or STATIONS
    n = len(stations)
    cols = 3 if n > 4 else n
    rows = int(np.ceil(n / cols))
    fig, axes = plt.subplots(rows, cols, figsize=(4.4 * cols, 2.2 * rows), sharex=True, squeeze=False)
    warm_days = res.scenario.warmup_min / MIN_PER_DAY
    for ax, ws in zip(axes.flat, stations):
        d = daily_queue(res, ws, how)
        bottom = np.zeros(len(d))
        for m in MODELS:
            ax.bar(d.index, d[m], bottom=bottom, width=0.86, color=MODEL_COLOR[m], label=m,
                   edgecolor=SURFACE, linewidth=0.4)
            bottom += d[m].values
        if warm_days:
            ax.axvspan(0.5, warm_days + 0.5, color=GRID, alpha=0.45, lw=0, zorder=0)
            ax.text((warm_days + 1) / 2, 0.98, "warm-up", transform=ax.get_xaxis_transform(),
                    ha="center", va="top", fontsize=7, color=MUTED)
        ax.set_title(f"{ws}  ({res.scenario.machines[ws]} machines)", loc="left", fontsize=9)
        ax.set_ylim(bottom=0)
        ax.grid(axis="x", visible=False)
    for ax in axes.flat[n:]:
        ax.set_visible(False)
    for ax in axes[-1]:
        ax.set_xlabel("Productive day (2 shifts)")
    h, l = axes.flat[0].get_legend_handles_labels()
    label = "average" if how == "mean" else how
    fig.suptitle(f"Wave levels: {label} parts waiting per day, by model", x=0.01, y=0.995,
                 ha="left", fontweight="bold")
    fig.tight_layout(rect=(0, 0, 1, 0.965))
    fig.legend(h, l, loc="upper right", ncol=3, bbox_to_anchor=(0.99, 0.995))
    return fig


def plot_queue_lines(res, stations=None, gated=True):
    """Detailed queue length over time (every 5 min sample), all vs behind the lot gate."""
    tr, stations = res.trace, stations or STATIONS
    n = len(stations)
    cols = 3 if n > 4 else n
    rows = int(np.ceil(n / cols))
    fig, axes = plt.subplots(rows, cols, figsize=(4.2 * cols, 2.1 * rows), sharex=True, squeeze=False)
    for ax, ws in zip(axes.flat, stations):
        ax.fill_between(tr.hr, tr[f"Q {ws}"], color=SERIES[0], alpha=0.25, lw=0)
        ax.plot(tr.hr, tr[f"Q {ws}"], color=SERIES[0], lw=1.2, label="Waiting (all)")
        if gated:
            ax.plot(tr.hr, tr[f"Gate {ws}"], color=SERIES[1], lw=1.0, label="Waiting behind lot gate")
        ax.set_title(f"{ws}  ({res.scenario.machines[ws]} machines)", loc="left", fontsize=9)
        ax.set_ylim(bottom=0)
        _warm(ax, res)
    for ax in axes.flat[n:]:
        ax.set_visible(False)
    for ax in axes[-1]:
        ax.set_xlabel("Productive hours")
    h, l = axes.flat[0].get_legend_handles_labels()
    fig.suptitle("Queue levels (parts waiting) by station", x=0.01, y=0.995, ha="left", fontweight="bold")
    fig.tight_layout(rect=(0, 0, 1, 0.965))
    fig.legend(h, l, loc="upper right", ncol=2, bbox_to_anchor=(0.99, 0.995))
    return fig


def _daily(res, cols, how="mean"):
    tr = res.trace
    day = (tr.t // MIN_PER_DAY).astype(int) + 1
    d = tr[cols].groupby(day).agg(how)
    d.index.name = "Day"
    return d


def _warm_days(ax, res):
    w = res.scenario.warmup_min / MIN_PER_DAY
    if w:
        ax.axvspan(0.5, w + 0.5, color=GRID, alpha=0.45, lw=0, zorder=0)
        ax.text((w + 1) / 2, 0.98, "warm-up", transform=ax.get_xaxis_transform(),
                ha="center", va="top", fontsize=7, color=MUTED)


def plot_wip(res):
    """Average WIP per day, stacked by where it sits."""
    d = _daily(res, ["WIP", "WIP Fab", "WIP Asm"])
    d["At match / between cells"] = (d["WIP"] - d["WIP Fab"] - d["WIP Asm"]).clip(lower=0)
    parts = {"Fab cell (WS 1-8)": ("WIP Fab", CELL_COLOR["Fab"]),
             "Assembly cell (WS 9-12)": ("WIP Asm", CELL_COLOR["Asm"]),
             "Waiting at the match": ("At match / between cells", AXIS)}
    fig, ax = plt.subplots(figsize=(10, 3.2))
    bottom = np.zeros(len(d))
    for label, (col, color) in parts.items():
        ax.bar(d.index, d[col], bottom=bottom, width=0.86, color=color, label=label,
               edgecolor=SURFACE, linewidth=0.4)
        bottom += d[col].values
    _warm_days(ax, res)
    ax.set_xlabel("Productive day (2 shifts)"), ax.set_ylabel("Parts (average per day)")
    ax.grid(axis="x", visible=False)
    ax.set_title("Work in process per day", loc="left", pad=22)
    ax.legend(loc="lower left", bbox_to_anchor=(0, 1.0), ncol=3)
    fig.tight_layout()
    return fig


def plot_operators_busy(res):
    """Average operators busy per day vs crew size, one panel per cell."""
    fig, axes = plt.subplots(1, 2, figsize=(11, 2.8))
    for ax, c in zip(axes, CELLS):
        d = _daily(res, [f"Ops busy {c}"])[f"Ops busy {c}"]
        crew = res.scenario.operators[c]
        ax.bar(d.index, d, width=0.86, color=CELL_COLOR[c], edgecolor=SURFACE, linewidth=0.4)
        ax.axhline(crew, color=INK2, ls="--", lw=1)
        ax.text(d.index.max(), crew + 0.1, f"crew = {crew}", ha="right", va="bottom", fontsize=8, color=INK2)
        _warm_days(ax, res)
        ax.set_title(f"{c} operators busy (average per day)", loc="left")
        ax.set_xlabel("Productive day (2 shifts)")
        ax.set_ylim(0, crew + 1)
        ax.grid(axis="x", visible=False)
    fig.tight_layout()
    return fig


# ---------------------------------------------------------------- utilization
def plot_machine_states(res, by="station"):
    df = res.stations if by == "station" else res.machines
    states = ["Busy", "Setup", "WaitOp", "Failed", "PM", "Idle"]
    data = df[[f"{s} %" for s in states]] * 100
    fig, ax = plt.subplots(figsize=(10, 0.32 * len(data) + 1.2))
    left = np.zeros(len(data))
    y = np.arange(len(data))[::-1]
    for s in states:
        v = data[f"{s} %"].values
        ax.barh(y, v, left=left, color=STATE_COLOR[s], label=STATE_LABEL[s],
                edgecolor=SURFACE, linewidth=1, height=0.75)
        left += v
    ax.set_yticks(y, data.index)
    ax.set_xlim(0, 100), ax.set_xlabel("% of time")
    ax.grid(axis="y", visible=False)
    ax.set_title(f"Resource states by {by}", loc="left", pad=24)
    ax.legend(ncol=6, loc="lower left", bbox_to_anchor=(0, 1.0), fontsize=7.5)
    fig.tight_layout()
    return fig


def plot_utilization(res):
    """Simulated utilization vs Phase 1 rough-cut prediction."""
    df = res.stations
    y = np.arange(len(df))[::-1]
    fig, ax = plt.subplots(figsize=(8, 4.6))
    ax.barh(y + 0.19, df["Utilization"] * 100, height=0.36, color=SERIES[0], label="Simulation (busy + setup)")
    ax.barh(y - 0.19, df["Phase 1 util"] * 100, height=0.36, color=AXIS, label="Phase 1 rough-cut")
    occ = (df["Utilization"] + df["WaitOp %"] + df["Down %"]) * 100
    ax.scatter(occ, y + 0.19, marker="|", s=120, color=INK, label="+ waiting for operator + down", zorder=3)
    ax.axvline(90, color=INK2, ls="--", lw=1, label="90% cap")
    for yi, v in zip(y, df["Utilization"] * 100):
        ax.text(v + 1, yi + 0.19, f"{v:.0f}%", va="center", fontsize=7, color=INK2)
    ax.set_yticks(y, df.index), ax.grid(axis="y", visible=False)
    ax.set_xlim(0, 110), ax.set_xlabel("Utilization %")
    ax.set_title("Station utilization: simulation vs Phase 1", loc="left", pad=28)
    ax.legend(loc="lower left", bbox_to_anchor=(0, 1.0), ncol=4, fontsize=7.5)
    fig.tight_layout()
    return fig


def plot_capacity(res):
    df = res.stations
    y = np.arange(len(df))[::-1]
    fig, ax = plt.subplots(figsize=(8, 4.6))
    ax.barh(y, df["Capacity (units/hr)"], color=GRID, height=0.7, label="Capacity (units/hr)")
    ax.barh(y, df["Throughput (units/hr)"], color=SERIES[0], height=0.4, label="Actual throughput (units/hr)")
    for yi, c, u in zip(y, df["Capacity (units/hr)"], df["Capacity used %"]):
        ax.text(c + 0.5, yi, f"{u:.0%} used", va="center", fontsize=7, color=INK2)
    ax.set_yticks(y, df.index), ax.grid(axis="y", visible=False)
    ax.set_xlabel("Units per productive hour")
    ax.set_title("Capacity vs throughput by station", loc="left", pad=22)
    ax.legend(loc="lower left", bbox_to_anchor=(0, 1.0), ncol=2, fontsize=7.5)
    fig.tight_layout()
    return fig


def plot_machine_heatmap(res):
    m = res.machines.copy()
    m["idx"] = m.index.str.split("#").str[1].astype(int)
    mat = m.pivot(index="Station", columns="idx", values="Utilization").reindex(STATIONS)
    fig, ax = plt.subplots(figsize=(1.0 * mat.shape[1] + 2.5, 4.8))
    im = ax.imshow(mat.values * 100, cmap=BLUES, vmin=0, vmax=100, aspect="auto")
    for (i, j), v in np.ndenumerate(mat.values):
        if not np.isnan(v):
            ax.text(j, i, f"{v:.0%}", ha="center", va="center", fontsize=7.5,
                    color="#ffffff" if v > 0.55 else INK)
    ax.set_xticks(range(mat.shape[1]), [f"#{c}" for c in mat.columns])
    ax.set_yticks(range(len(mat)), mat.index)
    ax.grid(False)
    fig.colorbar(im, ax=ax, label="Utilization %", shrink=0.8)
    ax.set_title("Utilization of each machine", loc="left")
    fig.tight_layout()
    return fig


# ---------------------------------------------------------------- operators
def plot_operators(res):
    ops = res.operators
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(13, 0.28 * len(ops) + 1.5),
                                 gridspec_kw={"width_ratios": [1, 1.6]})
    y = np.arange(len(ops))[::-1]
    a1.barh(y, ops.Utilization * 100, color=[CELL_COLOR[c] for c in ops.Cell], height=0.7)
    a1.set_yticks(y, ops.index), a1.set_xlim(0, 100), a1.set_xlabel("Utilization %")
    a1.grid(axis="y", visible=False)
    a1.axvline(90, color=INK2, ls="--", lw=1)
    a1.legend(handles=[Patch(color=CELL_COLOR[c], label=c) for c in CELLS], loc="lower right")
    a1.set_title("Operator utilization", loc="left")
    share = res.op_station.div(res.T) * 100
    share = share.loc[:, share.sum() > 0]
    im = a2.imshow(share.values, cmap=BLUES, aspect="auto", vmin=0, vmax=max(share.values.max(), 1))
    a2.set_xticks(range(share.shape[1]), share.columns), a2.set_yticks(range(len(share)), share.index)
    a2.grid(False)
    for (i, j), v in np.ndenumerate(share.values):
        if v >= 1:
            a2.text(j, i, f"{v:.0f}", ha="center", va="center", fontsize=6.5,
                    color="#ffffff" if v > share.values.max() * 0.55 else INK)
    fig.colorbar(im, ax=a2, label="% of operator time", shrink=0.8)
    a2.set_title("Where operators spend their time (% of time at each station)", loc="left")
    fig.tight_layout()
    return fig


def plot_operator_gantt(res, cell="Fab", start_hr=None, hours=3.0):
    log = res.op_log[res.op_log.Cell == cell]
    if log.empty:
        raise ValueError("No operator log - run with keep_logs=True")
    t0 = (start_hr * 60) if start_hr is not None else log.Start.min()
    t1 = t0 + hours * 60
    log = log[(log.End > t0) & (log.Start < t1)]
    ops = [o for o in res.operators.index if o.startswith(cell)]
    cols = station_colors(cell)
    fig, ax = plt.subplots(figsize=(12, 0.4 * len(ops) + 1.4))
    for i, o in enumerate(ops[::-1]):
        g = log[log.Operator == o]
        for ws, gg in g.groupby("Station"):
            ax.broken_barh(list(zip((gg.Start - t0) / 60, (gg.End - gg.Start) / 60)),
                           (i - 0.35, 0.7), facecolors=cols[ws], edgecolor=SURFACE, linewidth=0.3)
    ax.set_yticks(range(len(ops)), ops[::-1])
    ax.set_xlim(0, hours), ax.set_xlabel(f"Hours from t = {t0 / 60:.1f} h")
    ax.grid(axis="y", visible=False)
    ax.legend(handles=[Patch(color=c, label=ws) for ws, c in cols.items()],
              ncol=len(cols), loc="lower left", bbox_to_anchor=(0, 1.0))
    ax.set_title(f"{cell} operator activity (white gaps = idle)", loc="left", pad=22)
    fig.tight_layout()
    return fig


# ---------------------------------------------------------------- scrap & flow
def plot_scrap(res):
    sc = res.scrap
    weeks = res.T / MIN_PER_WEEK
    tab = sc.pivot_table(index="Station", columns="Model", values="Part", aggfunc="count",
                         fill_value=0).reindex(columns=MODELS, fill_value=0)
    tab = tab.loc[tab.sum(axis=1).sort_values(ascending=False).index] / weeks
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(13, 3.8))
    bottom = np.zeros(len(tab))
    x = np.arange(len(tab))
    for m in MODELS:
        a1.bar(x, tab[m], bottom=bottom, color=MODEL_COLOR[m], label=m, edgecolor=SURFACE, width=0.7)
        bottom += tab[m].values
    a1.set_xticks(x, tab.index), a1.grid(axis="x", visible=False)
    a1.set_ylabel("Scrapped units per week")
    a1.set_title("Scrap by station (Pareto)", loc="left")
    a1.legend()
    day = (sc.Time // MIN_PER_DAY).astype(int) + 1
    per_day = sc.assign(Day=day).pivot_table(index="Day", columns="Model", values="Part", aggfunc="count",
                                             fill_value=0).reindex(columns=MODELS, fill_value=0)
    bottom = np.zeros(len(per_day))
    for m in MODELS:
        a2.bar(per_day.index, per_day[m], bottom=bottom, width=0.86, color=MODEL_COLOR[m], label=m,
               edgecolor=SURFACE, linewidth=0.4)
        bottom += per_day[m].values
    a2.set_xlabel("Productive day (2 shifts)"), a2.set_ylabel("Scrapped items per day")
    a2.grid(axis="x", visible=False)
    a2.set_title("Scrap per day (after warm-up)", loc="left")
    a2.legend()
    fig.tight_layout()
    return fig


def plot_flow_time(res):
    f = res.flow
    fig, ax = plt.subplots(figsize=(9, 3.2))
    bins = np.linspace(0, f["Flow time (hr)"].quantile(0.995), 50)
    for m in MODELS:
        ax.hist(f.loc[f.Model == m, "Flow time (hr)"], bins=bins, histtype="step", lw=1.6,
                color=MODEL_COLOR[m], label=f"{m} (mean {f.loc[f.Model == m, 'Flow time (hr)'].mean():.1f} h)")
    ax.set_xlabel("Flow time, release to finished (productive hours)"), ax.set_ylabel("Parts")
    ax.set_title("Flow time distribution", loc="left")
    ax.legend()
    fig.tight_layout()
    return fig


def plot_throughput(res):
    f = res.flow.copy()
    f["Week"] = ((f.Done - res.scenario.warmup_min) // MIN_PER_WEEK).astype(int) + 1
    tab = f.pivot_table(index="Week", columns="Model", values="Part", aggfunc="count",
                        fill_value=0).reindex(columns=MODELS, fill_value=0)
    fig, axes = plt.subplots(1, len(MODELS), figsize=(11, 3.4), sharey=True)
    ymax = max(tab.values.max(), res.throughput.loc[list(MODELS), "Demand per week"].max()) * 1.15
    for ax, m in zip(axes, MODELS):
        ax.bar(tab.index, tab[m], color=MODEL_COLOR[m], edgecolor=SURFACE, width=0.7)
        dem = res.throughput.loc[m, "Demand per week"]
        ax.axhline(dem, color=INK, ls="--", lw=1)
        ax.set_xticks(tab.index), ax.grid(axis="x", visible=False)
        ax.set_ylim(0, ymax)
        ax.set_xlabel("Week (after warm-up)")
        ax.set_title(m, loc="left", color=MODEL_COLOR[m])
        ax.set_title(f"mean {tab[m].mean():,.0f}  ·  demand {dem:,.0f} /wk (--)", loc="right",
                     fontsize=8, fontweight="normal", color=INK2)
    axes[0].set_ylabel("Finished units")
    fig.suptitle("Weekly good output vs demand, by model", x=0.01, ha="left",
                 fontsize=11, fontweight="bold", color=INK)
    fig.tight_layout()
    return fig


# ---------------------------------------------------------------- block diagram
def plot_block_diagram(res, metric="Utilization"):
    """Precedence-diagram block view: colour = utilization, text = machines / util / avg queue."""
    st = res.stations
    pos = {"1-M": (0, 1), "2-M": (1, 1.75), "4-A": (1, 0.1), "3-A": (2, 2.5), "6-A": (2, 1.0),
           "5-A": (3, 2.5), "7-M": (4, 1), "8-A": (5, 1), "9-M": (6, 1), "10-M": (7, 1),
           "11-A": (8, 1), "12-M": (9, 1)}
    fig, ax = plt.subplots(figsize=(16, 5.2))
    W, H = 0.8, 0.62
    # cell backgrounds
    ax.add_patch(FancyBboxPatch((-0.5, -0.32), 5.0, 3.2, boxstyle="round,pad=0,rounding_size=0.12",
                                facecolor="#f0efec", edgecolor="none", zorder=0))
    ax.add_patch(FancyBboxPatch((5.55, 0.5), 3.95, 1.0, boxstyle="round,pad=0,rounding_size=0.12",
                                facecolor="#f0efec", edgecolor="none", zorder=0))
    ax.text(-0.42, 2.78, "FABRICATION CELL (WS 1-8)", fontsize=8, color=INK2, fontweight="bold", va="top")
    ax.text(5.63, 1.42, "ASSEMBLY CELL (WS 9-12)", fontsize=8, color=INK2, fontweight="bold", va="top")
    for a, b in FLOW_EDGES:
        (x0, y0), (x1, y1) = pos[a], pos[b]
        ax.add_patch(FancyArrowPatch((x0 + W / 2, y0), (x1 - W / 2, y1), arrowstyle="-|>",
                                     mutation_scale=10, color=MUTED, lw=1, zorder=1))
    for ws, (x, y) in pos.items():
        v = st.loc[ws, metric]
        txt = "#ffffff" if v > 0.55 else INK
        dashed = ws == "7-M"
        ax.add_patch(FancyBboxPatch((x - W / 2, y - H / 2), W, H, boxstyle="round,pad=0.02,rounding_size=0.06",
                                    facecolor=BLUES(min(v, 1.0)), edgecolor=INK2 if dashed else AXIS,
                                    lw=1, ls="--" if dashed else "-", zorder=2))
        ax.text(x, y + 0.17, ws, ha="center", va="center", fontweight="bold", fontsize=10, color=txt, zorder=3)
        ax.text(x, y - 0.02, f"{int(st.loc[ws, 'Machines'])} × {'bench' if st.loc[ws, 'Type'] == 'M' else 'machine'}",
                ha="center", va="center", fontsize=7.5, color=txt, zorder=3)
        ax.text(x, y - 0.18, f"util {v:.0%} · q {st.loc[ws, 'Avg queue']:.0f}",
                ha="center", va="center", fontsize=7.5, color=txt, zorder=3)
    ax.text(4.25, 0.5, "MATCH: 1 keyboard + 1 power/PCB/LCD + 1 backlight sub-assy\n"
            "-> 7-M (M860 only); M970/M1080 go straight to 8-A", ha="center", va="top",
            fontsize=6.5, color=INK2)
    ax.text(-0.9, 1, "Kits\nreleased", ha="center", va="center", fontsize=8, color=INK2)
    ax.add_patch(FancyArrowPatch((-0.68, 1), (-W / 2, 1), arrowstyle="-|>", mutation_scale=10, color=MUTED))
    ax.text(10.0, 1, "Finished\ngoods", ha="center", va="center", fontsize=8, color=INK2)
    ax.add_patch(FancyArrowPatch((9 + W / 2, 1), (9.7, 1), arrowstyle="-|>", mutation_scale=10, color=MUTED))
    ax.set_xlim(-1.3, 10.4), ax.set_ylim(-0.45, 2.95)
    ax.set_aspect("equal"), ax.axis("off")
    sm = plt.cm.ScalarMappable(cmap=BLUES, norm=plt.Normalize(0, 100))
    fig.colorbar(sm, ax=ax, shrink=0.55, label="Utilization %", pad=0.01)
    ax.set_title(f"Year {res.scenario.year} plant (precedence flow): station utilization and average queue",
                 loc="left", fontweight="bold")
    fig.tight_layout()
    return fig


# ---------------------------------------------------------------- experiments
def plot_compare(long_df, kpi, title=None, fmt="{:.1f}", ref=None):
    """Bar + 95% CI for one KPI across scenarios (long_df from experiments.compare)."""
    d = long_df[long_df.KPI == kpi]
    fig, ax = plt.subplots(figsize=(1.3 * len(d) + 3, 3.2))
    x = np.arange(len(d))
    ax.bar(x, d.Average, yerr=d["Half Width"], color=SERIES[0], width=0.6,
           error_kw=dict(ecolor=INK2, lw=1, capsize=3))
    for xi, v in zip(x, d.Average):
        ax.text(xi, v, fmt.format(v), ha="center", va="bottom", fontsize=8, color=INK2)
    if ref is not None:
        ax.axhline(ref, color=INK, ls="--", lw=1)
    ax.set_xticks(x, d.Scenario, rotation=15, ha="right"), ax.grid(axis="x", visible=False)
    ax.set_title(title or kpi, loc="left")
    fig.tight_layout()
    return fig
