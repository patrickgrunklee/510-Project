"""Scenario comparisons (Arena Process Analyzer equivalent)."""
from __future__ import annotations

import pandas as pd

from .params import CELLS, OPERATORS, get_scenario
from .stats import run_replications

KEY_KPIS = [
    "Throughput|Total per week", "Throughput|% of demand", "WIP|Average WIP (parts)",
    "WIP|Ending WIP (parts)", "Flow time (hr)|M860", "Flow time (hr)|M970", "Flow time (hr)|M1080",
    "Operators|Fab utilization", "Operators|Asm utilization",
    "Operators|Fab wait for operator (min/part)", "Operators|Asm wait for operator (min/part)",
    "Lots|Finished / EOQ",
]


def compare(scenarios: dict, n: int = 5, n_jobs: int = 1):
    """Run each scenario n times.

    Returns (long, wide): long has one row per scenario x KPI with Average and
    Half Width; wide is a readable 'mean ± hw' table (KPIs x scenarios).
    """
    rows = []
    for name, sc in scenarios.items():
        _, summ = run_replications(sc, n=n, n_jobs=n_jobs)
        s = summ.reset_index()
        s["KPI"] = s.Category + "|" + s.Statistic
        s["Scenario"] = name
        rows.append(s[["Scenario", "KPI", "Average", "Half Width", "Minimum", "Maximum"]])
    long = pd.concat(rows, ignore_index=True)
    wide = long.assign(Value=long.apply(lambda r: f"{r.Average:,.3g} ± {r['Half Width']:,.2g}", axis=1)) \
        .pivot(index="KPI", columns="Scenario", values="Value")
    wide = wide.reindex(index=long.KPI.drop_duplicates(), columns=list(scenarios))
    return long, wide


def maintenance_study(year=3, n=5, n_jobs=1, **kw):
    sc = {
        "No failures / PM": get_scenario(year, failures_on=False, pm_on=False, **kw),
        "PM only": get_scenario(year, failures_on=False, **kw),
        "Failures + PM (base)": get_scenario(year, **kw),
        "TQM (-75% downtime)": get_scenario(year, tqm=True, **kw),
    }
    return compare(sc, n, n_jobs)


def operator_sweep(year=3, cell="Asm", deltas=(-1, 0, 1, 2), n=5, n_jobs=1, **kw):
    base = OPERATORS[year][cell]
    sc = {f"{cell} {base + d} ({d:+d})": get_scenario(year, operators={cell: base + d}, **kw)
          for d in deltas if base + d > 0}
    return compare(sc, n, n_jobs)


def min_crew(year=3, target=0.98, n=3, n_jobs=1, max_extra=4, **kw):
    """Smallest crew per cell (other cell at plan) whose throughput >= target x demand."""
    out = []
    for cell in CELLS:
        base = OPERATORS[year][cell]
        for crew in range(max(1, base - 2), base + max_extra + 1):
            sc = get_scenario(year, operators={cell: crew}, **kw)
            _, s = run_replications(sc, n=n, n_jobs=n_jobs)
            pct = s.loc[("Throughput", "% of demand"), "Average"]
            util = s.loc[("Operators", f"{cell} utilization"), "Average"]
            out.append(dict(Cell=cell, Crew=crew, Plan=base, **{"% of demand": pct, "Utilization": util,
                            "Meets target": pct >= target}))
    df = pd.DataFrame(out)
    best = df[df["Meets target"]].groupby("Cell").Crew.min()
    return df, best


def capacity_whatif(year=3, n=5, n_jobs=1, extra=None, **kw):
    """Phase 1 recommendation (+1 bench at 9-M and 10-M) plus extra scenarios.

    extra: {name: get_scenario kwargs}, e.g. {"+1 1-M": {"machines": {"1-M": 3}}}
    """
    from .params import MACHINES
    m = MACHINES[year]
    sc = {"Plan": get_scenario(year, **kw),
          "+1 bench 9-M & 10-M": get_scenario(year, machines={"9-M": m["9-M"] + 1, "10-M": m["10-M"] + 1}, **kw)}
    for name, over in (extra or {}).items():
        sc[name] = get_scenario(year, **{**kw, **over})
    return compare(sc, n, n_jobs)


def years_compare(n=5, n_jobs=1, **kw):
    return compare({f"Year {y}": get_scenario(y, **kw) for y in (1, 2, 3)}, n, n_jobs)
