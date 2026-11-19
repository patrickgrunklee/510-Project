"""Arena-style reports: turn a finished LCDPlant into pandas tables.

collect(plant)        -> SimResult (one replication, all detail tables)
run(scenario)         -> SimResult
run_replications(...) -> (per-replication KPI table, Category-Overview summary)
"""
from __future__ import annotations

import math
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

from .model import MACHINE_STATES, simulate
from .params import (CELLS, MERGE_INPUTS, MIN_PER_WEEK, MODELS, PHASE1_UTIL, SEG_NAME, SEGMENTS,
                     STATION_INFO, STATIONS, WEEKS_PER_YEAR, Scenario, visits)

# two-sided 95% t critical values, df = 1..30
_T95 = [12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306, 2.262, 2.228, 2.201, 2.179,
        2.160, 2.145, 2.131, 2.120, 2.110, 2.101, 2.093, 2.086, 2.080, 2.074, 2.069, 2.064,
        2.060, 2.056, 2.052, 2.048, 2.045, 2.042]


def t95(df: int) -> float:
    return _T95[df - 1] if 1 <= df <= 30 else 1.96


@dataclass
class SimResult:
    scenario: Scenario
    T: float                       # statistics period (min, after warm-up)
    stations: pd.DataFrame         # utilization / capacity / queues per station
    machines: pd.DataFrame         # per machine states and counts
    operators: pd.DataFrame        # per operator utilization
    op_station: pd.DataFrame       # operator x station minutes
    cells: pd.DataFrame            # per cell operator summary
    throughput: pd.DataFrame       # per model vs demand
    flow: pd.DataFrame             # completed parts (post warm-up)
    scrap: pd.DataFrame            # scrapped parts (post warm-up)
    lots: pd.DataFrame             # lot-level yield / flow
    trace: pd.DataFrame            # time series samples (if keep_logs)
    op_log: pd.DataFrame           # operator tasks (if keep_logs)
    kpis: dict                     # flat KPIs for replication summaries
    checks: dict                   # model sanity checks

    def bottlenecks(self) -> pd.DataFrame:
        cols = ["Machines", "Utilization", "Phase 1 util", "Avg queue", "Avg wait (min)",
                "Gate-blocked %", "Wait for operator (min/part)", "Down %", "Spare capacity %"]
        df = self.stations[cols].copy()
        df["Sim rank"] = df["Utilization"].rank(ascending=False).astype(int)
        df["Phase 1 rank"] = df["Phase 1 util"].rank(ascending=False).astype(int)
        return df.sort_values("Utilization", ascending=False)


def collect(p) -> SimResult:
    sc, T = p.sc, p.env.now - p.stat_start
    t0 = p.stat_start

    # ---------------------------------------------------------- machines
    mrows = []
    for ws, st in p.stations.items():
        for m in st.machines:
            row = dict(Station=ws, Machine=m.name)
            row.update({f"{s} %": m.state_time[s] / T for s in MACHINE_STATES})
            row["Utilization"] = (m.state_time["Busy"] + m.state_time["Setup"]) / T
            row.update(Units=m.units, Setups=m.setups, Failures=m.failures, PMs=m.pms)
            mrows.append(row)
    machines = pd.DataFrame(mrows).set_index("Machine")

    # ---------------------------------------------------------- stations
    srows = []
    for ws, st in p.stations.items():
        mm = machines[machines.Station == ws]
        n = len(st.machines)
        tpp = sc.avg_time_per_part(ws)
        cap_hr = n * 60 / tpp                      # units/hr if never idle (no setups/down)
        thr_hr = st.processed / T * 60
        srows.append({
            "Station": ws, "Cell": st.cell, "Type": st.kind, "Machines": n,
            "Utilization": mm["Utilization"].mean(),
            "Phase 1 util": PHASE1_UTIL[sc.year][ws],
            **{f"{s} %": mm[f"{s} %"].mean() for s in MACHINE_STATES},
            "Down %": mm["Failed %"].mean() + mm["PM %"].mean(),
            "Avg queue": st.q.mean(), "Max queue": st.q.max,
            "Avg gated queue": st.q_gate.mean(),
            "Gate-blocked %": st.gate_blocked.mean(),
            "Processed": st.processed,
            "Avg wait (min)": st.q.mean() * T / st.processed if st.processed else np.nan,
            "Wait for operator (min/part)": st.op_wait_total / st.op_wait_n if st.op_wait_n else 0.0,
            "Avg time/part (min)": tpp,
            "Capacity (units/hr)": cap_hr,
            "Throughput (units/hr)": thr_hr,
            "Capacity used %": thr_hr / cap_hr,
            "Spare capacity %": 1 - thr_hr / cap_hr,
            "Available machine-hr": n * T / 60,
            "Setups": int(mm["Setups"].sum()), "Failures": int(mm["Failures"].sum()),
            "PMs": int(mm["PMs"].sum()), "Lots started": st.lots_started,
            "Lot violations": st.lot_violations,
        })
    stations = pd.DataFrame(srows).set_index("Station")

    # ---------------------------------------------------------- operators
    ops = pd.Series(p.op_time, dtype=float)
    op_station = (ops.unstack(fill_value=0.0) if len(ops) else pd.DataFrame())
    op_station = op_station.reindex(index=[o.name for o in p.operators],
                                    columns=STATIONS, fill_value=0.0)
    operators = pd.DataFrame({
        "Cell": [o.cell for o in p.operators],
        "Busy (hr)": op_station.sum(axis=1).values / 60,
        "Utilization": op_station.sum(axis=1).values / T,
        "Main station": op_station.idxmax(axis=1).values,
    }, index=op_station.index)
    crow = []
    for c in CELLS:
        crew = sc.operators[c]
        cst = stations[stations.Cell == c]
        crow.append({"Cell": c, "Crew per shift": crew,
                     "Avg busy": p.ops_busy[c].mean(), "Peak busy": p.ops_busy[c].max,
                     "Utilization": p.ops_busy[c].mean() / crew,
                     "Avg WIP in cell": p.cell_wip[c].mean(),
                     "Avg wait for operator (min/part)":
                         (cst["Wait for operator (min/part)"] * cst["Processed"]).sum()
                         / max(cst["Processed"].sum(), 1),
                     "Avg wait for machine (min/part)":
                         (cst["Avg wait (min)"] * cst["Processed"]).sum()
                         / max(cst["Processed"].sum(), 1)})
    cells = pd.DataFrame(crow).set_index("Cell")

    # ---------------------------------------------------------- flow / throughput
    flow = pd.DataFrame(p.done_log, columns=["Part", "Model", "Lot", "Release", "Done",
                                             "Process min", "Labor $"])
    flow = flow[flow.Done >= t0].copy()
    flow["Flow time (hr)"] = (flow.Done - flow.Release) / 60
    weeks = T / MIN_PER_WEEK
    trows = []
    for m in MODELS:
        f = flow[flow.Model == m]
        dem = sc.demand[m] / WEEKS_PER_YEAR
        trows.append({"Model": m, "Finished": len(f), "Per week": len(f) / weeks,
                      "Demand per week": dem, "% of demand": len(f) / weeks / dem,
                      "Avg flow time (hr)": f["Flow time (hr)"].mean(),
                      "P90 flow time (hr)": f["Flow time (hr)"].quantile(.9)})
    throughput = pd.DataFrame(trows).set_index("Model")
    tot = throughput[["Finished", "Per week", "Demand per week"]].sum()
    throughput.loc["Total"] = {**tot, "% of demand": tot["Per week"] / tot["Demand per week"],
                               "Avg flow time (hr)": flow["Flow time (hr)"].mean(),
                               "P90 flow time (hr)": flow["Flow time (hr)"].quantile(.9)}

    # ---------------------------------------------------------- scrap
    scrap = pd.DataFrame(p.scrap_log, columns=["Part", "Model", "Lot", "Segment", "Station", "Time",
                                               "Invested min", "Labor $"])
    scrap["Item"] = scrap.Segment.map(SEG_NAME)
    scrap = scrap[scrap.Time >= t0].reset_index(drop=True)

    # ---------------------------------------------------------- lots
    lrows = []
    for l in p.lots:
        r = dict(Lot=l.id, Model=l.model, Release=l.release_time, EOQ=l.eoq, Kits=l.qty,
                 Finished=l.finished, Done=l.done_time,
                 Scrapped=l.scrapped, Spare=sum(l.spare.values()))
        r.update({f"Scrap {g}": l.scrap[g] for g in SEGMENTS})
        r.update({f"Spare {g}": l.spare[g] for g in ("KB", "PWR", "BL")})
        r.update({f"Unmatched {g}": l.unmatched[g] for g in MERGE_INPUTS})
        r["Unmatched"] = sum(l.unmatched.values()) if l.done_time is not None else np.nan
        lrows.append(r)
    lots = pd.DataFrame(lrows).set_index("Lot")
    closed = lots.Done.notna()
    lots["Finished / EOQ"] = np.where(closed, lots.Finished / lots.EOQ, np.nan)
    lots["Short of EOQ"] = np.where(closed, (lots.EOQ - lots.Finished).clip(lower=0), np.nan)
    lots["Yield (finished / kits)"] = np.where(closed, lots.Finished / lots.Kits, np.nan)
    lots["Planned yield"] = lots.Model.map(sc.yield_)
    lots["Lot flow time (hr)"] = (lots.Done - lots.Release) / 60

    trace = pd.DataFrame(p.trace)
    if len(trace):
        trace["hr"] = trace.t / 60
    op_log = pd.DataFrame(p.op_log, columns=["Operator", "Cell", "Station", "Machine", "Task",
                                             "Lot", "Model", "Start", "End"])

    # ---------------------------------------------------------- KPIs
    k = {}
    for m in MODELS + ["Total"]:
        k[f"Throughput|{m} per week"] = throughput.loc[m, "Per week"]
    k["Throughput|% of demand"] = throughput.loc["Total", "% of demand"]
    for m in MODELS:
        k[f"Flow time (hr)|{m}"] = throughput.loc[m, "Avg flow time (hr)"]
    k["WIP|Average WIP (parts)"] = p.wip.mean()
    k["WIP|Ending WIP (parts)"] = p.wip.value
    for c in CELLS:
        k[f"Operators|{c} utilization"] = cells.loc[c, "Utilization"]
        k[f"Operators|{c} wait for operator (min/part)"] = cells.loc[c, "Avg wait for operator (min/part)"]
    for ws in STATIONS:
        k[f"Station utilization|{ws}"] = stations.loc[ws, "Utilization"]
    for ws in STATIONS:
        k[f"Avg queue|{ws}"] = stations.loc[ws, "Avg queue"]
    for ws in STATIONS:
        k[f"Down %|{ws}"] = stations.loc[ws, "Down %"]
    k["Scrap|Items scrapped per week"] = len(scrap) / weeks
    cl = lots[lots.Done.notna() & (lots.Release >= t0)]
    k["Lots|Finished / EOQ"] = cl["Finished / EOQ"].mean() if len(cl) else np.nan
    k["Lots|Spare components per lot (not sent)"] = cl["Spare"].mean() if len(cl) else np.nan
    k["Lots|Unmatched sub-assys per lot"] = cl["Unmatched"].mean() if len(cl) else np.nan
    k["Scrap|Labor $ scrapped per week"] = scrap["Labor $"].sum() / weeks

    # ---------------------------------------------------------- sanity checks
    done_lots = [l for l in p.lots if l.done_time is not None]
    checks = {
        "lot violations (must be 0)": int(stations["Lot violations"].sum()),
        "closed lots with items still owed (must be 0)":
            sum(any(v != 0 for v in l.owed.values()) or any(v != 0 for v in l.remaining.values())
                for l in done_lots),
        "negative owed counts (must be 0)": sum(v < 0 for l in p.lots for v in l.owed.values()),
        "finished > EOQ x plan (must be 0)": sum(l.finished > l.plan["ASM"] for l in p.lots),
        "max operator utilization (<= 1)": float(operators.Utilization.max()) if len(operators) else 0,
    }
    return SimResult(sc, T, stations, machines, operators, op_station, cells, throughput,
                     flow, scrap, lots, trace, op_log, k, checks)


def run(sc: Scenario) -> SimResult:
    return collect(simulate(sc))


def _kpis(sc: Scenario) -> dict:
    return run(sc).kpis


def run_replications(sc: Scenario, n: int = 10, n_jobs: int = 1):
    """n independent replications (seeds sc.seed .. sc.seed+n-1).

    Returns (reps, summary): reps has one row per replication; summary is the
    Arena 'Category Overview' layout - Average, Half Width (95%), Min, Max.
    """
    scs = [replace(sc, seed=sc.seed + i, keep_logs=False) for i in range(n)]
    if n_jobs > 1:
        with ProcessPoolExecutor(n_jobs) as ex:
            rows = list(ex.map(_kpis, scs))
    else:
        rows = [_kpis(s) for s in scs]
    reps = pd.DataFrame(rows)
    reps.index.name = "Replication"
    return reps, summarize(reps)


def summarize(reps: pd.DataFrame) -> pd.DataFrame:
    n = len(reps)
    hw = reps.std(ddof=1) * t95(n - 1) / math.sqrt(n) if n > 1 else reps.mean() * np.nan
    out = pd.DataFrame({"Average": reps.mean(), "Half Width": hw,
                        "Minimum": reps.min(), "Maximum": reps.max()})
    idx = [tuple(c.split("|", 1)) for c in out.index]
    out.index = pd.MultiIndex.from_tuples(idx, names=["Category", "Statistic"])
    return out
