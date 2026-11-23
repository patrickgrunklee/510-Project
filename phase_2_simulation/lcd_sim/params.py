"""Input data for the LCD plant simulation (single source of truth).

Sources
-------
* project-description_2026_fall.doc  - routing, scrap, efficiency, labor, maintenance
* ISYE 510 Phase 1 Report.docx       - demand growth, EOQ lots, machine counts,
                                        operators per cell, utilization predictions
* phase 2/phase 2 data.xlsx          - machine counts per year (No SPC)

All times are in MINUTES unless the name says otherwise.
"""
from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field

MODELS = ["M860", "M970", "M1080"]
STATIONS = ["1-M", "2-M", "3-A", "4-A", "5-A", "6-A",
            "7-M", "8-A", "9-M", "10-M", "11-A", "12-M"]

# ---------------------------------------------------------------- stations
# kind: "M" = manual bench (operator for whole cycle), "A" = automatic machine
# (operator only for setup / load / unload).  cell: Fab (WS1-8) / Asm (WS9-12)
STATION_INFO = {
    #  ws      description                            kind cell   eff   $/hr  equip $
    "1-M":  dict(desc="Unpack and label components",       kind="M", cell="Fab", eff=0.90, rate=15, cost=800),
    "2-M":  dict(desc="Sanitize LC displays and PCBs",     kind="M", cell="Fab", eff=0.95, rate=16, cost=1000),
    "3-A":  dict(desc="Connect display to power strip",    kind="A", cell="Fab", eff=1.05, rate=18, cost=9000),
    "4-A":  dict(desc="Tack keyboard inputs to display",   kind="A", cell="Fab", eff=1.02, rate=18, cost=12000),
    "5-A":  dict(desc="Bond power strip to PCB and LCD",   kind="A", cell="Fab", eff=0.99, rate=18, cost=15000),
    "6-A":  dict(desc="Insert backlights and sensors",     kind="A", cell="Fab", eff=0.95, rate=16, cost=8000),
    "7-M":  dict(desc="Verify alignment and bonds",        kind="M", cell="Fab", eff=0.90, rate=16, cost=800),
    "8-A":  dict(desc="Power up test on components",       kind="A", cell="Fab", eff=0.95, rate=18, cost=5600),
    "9-M":  dict(desc="Solder subassemblies and connectors", kind="M", cell="Asm", eff=0.93, rate=18, cost=1500),
    "10-M": dict(desc="Complete assembly in housing",      kind="M", cell="Asm", eff=0.90, rate=18, cost=1200),
    "11-A": dict(desc="Final test and record results",     kind="A", cell="Asm", eff=1.00, rate=22, cost=14500),
    "12-M": dict(desc="Pack and label units",              kind="M", cell="Asm", eff=1.05, rate=15, cost=1500),
}
CELLS = ["Fab", "Asm"]

# ---------------------------------------------------------------- routing
# (setup, cycle, load, unload) in minutes; transfer batch = 1 everywhere.
# Setup times at 3-A, 4-A, 5-A, 8-A, and 11-A include a 75% reduction.
# cycle == 0 means the model does not visit the station (7-M for M970/M1080).
_R = {
    "M860": {
        "1-M": (15, 1.6, 0, 0),   "2-M": (20, 1.1, 0, 0),   "3-A": (1.25, 0.4, 0.1, 0.1),
        "4-A": (2, 0.6, 0.2, 0.2), "5-A": (3.75, 1.5, 1.0, 0.2), "6-A": (10, 1.0, 0.1, 0.1),
        "7-M": (12, 0.8, 0.2, 0.2), "8-A": (2.5, 0.9, 0.2, 0.2), "9-M": (5, 3.2, 0, 0),
        "10-M": (5, 2.9, 0, 0),   "11-A": (2.5, 1.3, 0.2, 0.2), "12-M": (5, 1.5, 0, 0),
    },
    "M970": {
        "1-M": (0, 1.7, 0, 0),    "2-M": (0, 1.5, 0, 0),    "3-A": (2.5, 1.3, 0.1, 0.1),
        "4-A": (2.5, 0.3, 0.1, 0.1), "5-A": (15, 5.3, 0.8, 0.4), "6-A": (0, 1.7, 0.1, 0.1),
        "7-M": (0, 0, 0, 0),      "8-A": (2.5, 1.1, 0.2, 0.2), "9-M": (0, 6.9, 0, 0),
        "10-M": (0, 5.7, 0, 0),   "11-A": (2.5, 1.2, 0.2, 0.2), "12-M": (0, 1.5, 0, 0),
    },
    "M1080": {
        "1-M": (0, 2.1, 0, 0),    "2-M": (0, 1.6, 0, 0),    "3-A": (3, 1.5, 0.2, 0.1),
        "4-A": (3.5, 0.4, 0.2, 0.1), "5-A": (16.25, 6.1, 0.8, 0.4), "6-A": (0, 2.9, 0.2, 0.1),
        "7-M": (0, 0, 0, 0),      "8-A": (3.75, 1.5, 0.3, 0.2), "9-M": (0, 8.2, 0, 0),
        "10-M": (0, 6.0, 0, 0),   "11-A": (3.75, 1.3, 0.3, 0.2), "12-M": (0, 1.6, 0, 0),
    },
}
ROUTING = {m: {ws: dict(zip(("setup", "cycle", "load", "unload"), v))
               for ws, v in d.items()} for m, d in _R.items()}

# scrap rate per part, per station, per model
SCRAP = {
    "M860":  {"1-M": 0, "2-M": .01, "3-A": .02, "4-A": .03, "5-A": .05, "6-A": .01,
              "7-M": .03, "8-A": .04, "9-M": .04, "10-M": .02, "11-A": .03, "12-M": 0},
    "M970":  {"1-M": 0, "2-M": .005, "3-A": .015, "4-A": .02, "5-A": .04, "6-A": .01,
              "7-M": 0, "8-A": .03, "9-M": .028, "10-M": .015, "11-A": .024, "12-M": 0},
    "M1080": {"1-M": 0, "2-M": .005, "3-A": .013, "4-A": .022, "5-A": .06, "6-A": .01,
              "7-M": 0, "8-A": .03, "9-M": .03, "10-M": .016, "11-A": .023, "12-M": 0},
}

# ---------------------------------------------------------------- maintenance
# repair (min), mtbf (hr), pm_units (every N parts), pm_time (min).  None = no data.
MAINT = {
    "1-M":  dict(repair=None, mtbf=None, pm_units=None, pm_time=None),
    "2-M":  dict(repair=10,  mtbf=100, pm_units=None,  pm_time=None),
    "3-A":  dict(repair=30,  mtbf=50,  pm_units=12000, pm_time=10),
    "4-A":  dict(repair=60,  mtbf=75,  pm_units=12000, pm_time=20),
    "5-A":  dict(repair=120, mtbf=250, pm_units=12000, pm_time=20),
    "6-A":  dict(repair=60,  mtbf=60,  pm_units=6000,  pm_time=10),
    "7-M":  dict(repair=15,  mtbf=100, pm_units=None,  pm_time=None),
    "8-A":  dict(repair=90,  mtbf=250, pm_units=6000,  pm_time=10),
    "9-M":  dict(repair=None, mtbf=None, pm_units=None, pm_time=None),
    "10-M": dict(repair=None, mtbf=None, pm_units=None, pm_time=None),
    "11-A": dict(repair=60,  mtbf=500, pm_units=6000,  pm_time=10),
    "12-M": dict(repair=None, mtbf=None, pm_units=None, pm_time=None),
}

# ---------------------------------------------------------------- year plans
LIST_PRICE = {"M860": 42.80, "M970": 53.10, "M1080": 59.50}
DEMAND = {  # finished units per year (Phase 1 Table 1.2.1)
    1: {"M860": 48300, "M970": 38400, "M1080": 36400},
    2: {"M860": 55545, "M970": 46080, "M1080": 47320},
    3: {"M860": 63877, "M970": 55296, "M1080": 61516},
}
EOQ = {  # (lot size in good units, lots per year) - Phase 1 Table 1.3.1
    1: {"M860": (655, 74), "M970": (496, 78), "M1080": (504, 73)},
    2: {"M860": (703, 80), "M970": (544, 85), "M1080": (574, 83)},
    3: {"M860": (754, 85), "M970": (596, 93), "M1080": (655, 94)},
}
MACHINES = {  # phase 2 data.xlsx, "Number of Machines (NO SPC)"
    1: {"1-M": 2, "2-M": 1, "3-A": 1, "4-A": 1, "5-A": 4, "6-A": 2, "7-M": 1, "8-A": 2,
        "9-M": 4, "10-M": 3, "11-A": 1, "12-M": 1},
    2: {"1-M": 2, "2-M": 2, "3-A": 1, "4-A": 1, "5-A": 5, "6-A": 2, "7-M": 1, "8-A": 2,
        "9-M": 5, "10-M": 4, "11-A": 2, "12-M": 1},
    3: {"1-M": 2, "2-M": 2, "3-A": 2, "4-A": 1, "5-A": 6, "6-A": 3, "7-M": 1, "8-A": 2,
        "9-M": 6, "10-M": 5, "11-A": 2, "12-M": 2},
}
OPERATORS = {  # per shift, cross-trained within the cell (Phase 1 Table 3.1.2)
    1: {"Fab": 5, "Asm": 9},
    2: {"Fab": 6, "Asm": 11},
    3: {"Fab": 7, "Asm": 13},
}
PHASE1_UTIL = {  # Phase 1 Table 2.3.1 (rough-cut, theoretical / rounded machines)
    1: {"1-M": .6611, "2-M": .9678, "3-A": .7872, "4-A": .4929, "5-A": .8727, "6-A": .6687,
        "7-M": .3416, "8-A": .5139, "9-M": .9239, "10-M": .9918, "11-A": .9684, "12-M": .7986},
    2: {"1-M": .7954, "2-M": .5828, "3-A": .9598, "4-A": .5847, "5-A": .8495, "6-A": .8169,
        "7-M": .3886, "8-A": .6205, "9-M": .9003, "10-M": .9024, "11-A": .5803, "12-M": .9578},
    3: {"1-M": .9597, "2-M": .7038, "3-A": .5867, "4-A": .6954, "5-A": .8636, "6-A": .6671,
        "7-M": .4422, "8-A": .7513, "9-M": .9162, "10-M": .8780, "11-A": .6973, "12-M": .5759},
}

# ---------------------------------------------------------------- calendar
SHIFTS_PER_DAY = 2
PRODUCTIVE_MIN_PER_SHIFT = 7.5 * 60
DAYS_PER_WEEK = 5
WEEKS_PER_YEAR = 50
MIN_PER_DAY = SHIFTS_PER_DAY * PRODUCTIVE_MIN_PER_SHIFT          # 900
MIN_PER_WEEK = MIN_PER_DAY * DAYS_PER_WEEK                       # 4500
MIN_PER_YEAR = MIN_PER_WEEK * WEEKS_PER_YEAR                     # 225,000 (3,750 h)


# ---------------------------------------------------------------- precedence
# Flow follows precedence_diagram.png (Arena: Separate -> branches -> Match/Batch).
#   1-M (component kit) --+--> 4-A ........................ keyboard sub-assy ---+
#                         +--> 2-M --+--> 3-A --> 5-A ...... power/PCB/LCD -------+--> MATCH --> 7-M* --> 8-A --> 9-M --> 10-M --> 11-A --> 12-M
#                                    +--> 6-A ............. backlight/sensors ----+          (* 7-M = M860 only)
SEGMENTS = {
    "KIT":  ["1-M"],
    "KB":   ["4-A"],
    "DISP": ["2-M"],
    "PWR":  ["3-A", "5-A"],
    "BL":   ["6-A"],
    "ASM":  ["7-M", "8-A", "9-M", "10-M", "11-A", "12-M"],
}
SEG_CHILDREN = {"KIT": ["KB", "DISP"], "DISP": ["PWR", "BL"]}   # Separate after these
MERGE_INPUTS = ["KB", "PWR", "BL"]                               # Match 1 of each -> ASM
SEG_NAME = {"KIT": "Component kit", "KB": "Keyboard sub-assy", "DISP": "LCD/PCB set",
            "PWR": "Power strip/PCB/LCD sub-assy", "BL": "Backlight/sensor sub-assy",
            "ASM": "Display assembly"}
STATION_SEG = {ws: seg for seg, l in SEGMENTS.items() for ws in l}
FLOW_EDGES = [("1-M", "2-M"), ("1-M", "4-A"), ("2-M", "3-A"), ("2-M", "6-A"), ("3-A", "5-A"),
              ("5-A", "7-M"), ("6-A", "7-M"), ("4-A", "7-M"), ("7-M", "8-A"), ("8-A", "9-M"),
              ("9-M", "10-M"), ("10-M", "11-A"), ("11-A", "12-M")]


def visits(model: str, ws: str) -> bool:
    return ROUTING[model][ws]["cycle"] > 0


def route(model: str) -> list[str]:
    return [ws for ws in STATIONS if visits(model, ws)]


def seg_route(model: str, seg: str) -> list[str]:
    return [ws for ws in SEGMENTS[seg] if visits(model, ws)]


@dataclass
class Scenario:
    """Everything one simulation run needs.  Build with get_scenario()."""
    year: int = 3
    machines: dict = field(default_factory=dict)
    operators: dict = field(default_factory=dict)
    demand: dict = field(default_factory=dict)
    eoq: dict = field(default_factory=dict)
    # run control
    run_weeks: float = 8
    warmup_weeks: float = 2
    seed: int = 1
    monitor_interval: float = 5.0          # min between trace samples (plots)
    keep_logs: bool = True                 # operator task log + trace (off for reps)
    op_log_weeks: float = 1.0              # weeks of operator task log kept (Gantt)
    # behaviour switches
    learning: float | None = None          # run-time factor; default 0.99**(year-1)
    scrap_on: bool = True
    scrap_scale: float = 1.0               # 0.5 = SPC (-50% scrap)
    failures_on: bool = True               # breakdowns
    pm_on: bool = True                     # planned maintenance
    failure_mode: str = "count"            # "count" (per part) or "time" (EXPO(MTBF))
    tqm: bool = False                      # TQM: downtime -75%
    tqm_factor: float = 0.25
    repair_uses_operator: bool = False     # repairs/PM by maintenance, not line operators
    move_time: float = 0.0                 # min to move a part between stations
    release_mode: str = "continuous"      # "batch", "continuous" product runs, or "cyclic" 1-2-3
    setup_scope: str = "machine"           # "machine": every machine sets up for a new lot
                                           # "station": one setup per lot per station (Phase 1 RCA)
    op_priority: dict = field(default_factory=dict)  # ws -> priority (lower = served first)
    setup_crew: dict = field(default_factory=dict)   # workcell setup crew by station; default machine count

    def __post_init__(self):
        if self.release_mode not in ("continuous", "batch", "cyclic"):
            raise ValueError("release_mode must be 'continuous', 'batch', or 'cyclic'")
        if self.setup_scope not in ("machine", "station", "workcell"):
            raise ValueError("setup_scope must be 'machine', 'station', or 'workcell'")
        if self.release_mode == "cyclic" and self.setup_scope == "station":
            raise ValueError("Cyclic dispatch requires machine or workcell setups")
        if self.setup_scope == "workcell":
            if self.release_mode != "cyclic":
                raise ValueError("Workcell setup experiment requires cyclic mixed-product arrivals")
            for ws, count in self.machines.items():
                crew = self.setup_crew.get(ws, count)
                if not isinstance(crew, int) or not 1 <= crew <= self.operators[STATION_INFO[ws]["cell"]]:
                    raise ValueError(f"Invalid setup crew at {ws}: {crew}")
        if self.learning is None:
            self.learning = 0.99 ** max(self.year - 1, 0)

    # derived ------------------------------------------------------------
    @property
    def kit_interarrival_min(self) -> float:
        """Constant kit spacing preserving the planned annual kit volume and mix."""
        annual_kits = sum(self.launch_qty(m) * self.eoq[m][1] for m in MODELS)
        return MIN_PER_YEAR / annual_kits

    @property
    def run_min(self) -> float:
        return self.run_weeks * MIN_PER_WEEK

    @property
    def warmup_min(self) -> float:
        return self.warmup_weeks * MIN_PER_WEEK

    def scrap_rate(self, model: str, ws: str) -> float:
        return SCRAP[model][ws] * self.scrap_scale if self.scrap_on else 0.0

    def station_factors(self, model: str) -> tuple[dict, dict]:
        """Phase 1 scrap factors along the precedence diagram (Prod Req sheet).

        Returns (factor per station, factor at the first station of each segment):
        units that must START at a station per good finished unit.  Scrap only
        compounds along a branch; at a Separate the parent must feed the most
        demanding child.
        """
        f, first = {}, {}
        out = 1.0
        for ws in reversed(seg_route(model, "ASM")):
            f[ws] = out / (1 - self.scrap_rate(model, ws))
            out = f[ws]
        first["ASM"] = out

        def req(seg):
            o = first["ASM"] if seg in MERGE_INPUTS else max(req(c) for c in SEG_CHILDREN[seg])
            for ws in reversed(seg_route(model, seg)):
                f[ws] = o / (1 - self.scrap_rate(model, ws))
                o = f[ws]
            first[seg] = o
            return o

        req("KIT")
        return f, first

    def segment_plan(self, model: str) -> dict:
        """Items each segment is sent per lot: ceil(EOQ x factor) - 'send the EOQ through'."""
        eoq = self.eoq[model][0]
        _, first = self.station_factors(model)
        return {seg: math.ceil(eoq * first[seg] - 1e-9) for seg in SEGMENTS}

    def yield_(self, model: str) -> float:
        """Planned good units per kit launched at 1-M."""
        return 1 / self.station_factors(model)[1]["KIT"]

    def launch_qty(self, model: str) -> int:
        return self.segment_plan(model)["KIT"]

    def station_qty(self, model: str, ws: str) -> int:
        """Planned items processed at ws per lot."""
        return self.segment_plan(model)[STATION_SEG[ws]] if visits(model, ws) else 0

    def op_times(self, model: str, ws: str) -> dict:
        """Effective task times (min) for one part, efficiency + learning applied."""
        r = ROUTING[model][ws]
        eff = STATION_INFO[ws]["eff"]
        return dict(setup=r["setup"] / eff, load=r["load"] / eff,
                    cycle=r["cycle"] * self.learning / eff, unload=r["unload"] / eff)

    def time_per_part(self, model: str, ws: str) -> float:
        t = self.op_times(model, ws)
        return t["load"] + t["cycle"] + t["unload"]

    def parts_per_year(self, model: str, ws: str = "1-M") -> float:
        """Planned items per year processed at ws (default: kits launched at 1-M)."""
        return self.station_qty(model, ws) * self.eoq[model][1]

    def avg_time_per_part(self, ws: str) -> float:
        """Average machine time per part at ws, weighted by the planned volume there."""
        num = den = 0.0
        for m in MODELS:
            if visits(m, ws):
                w = self.parts_per_year(m, ws)
                num += w * self.time_per_part(m, ws)
                den += w
        return num / den

    def units_between_failures(self, ws: str) -> int | None:
        mtbf = MAINT[ws]["mtbf"]
        if not mtbf:
            return None
        return max(1, round(mtbf * 60 / self.avg_time_per_part(ws)))

    def describe(self):
        """Tables of the scenario inputs (pandas DataFrames)."""
        import pandas as pd
        st = pd.DataFrame([
            dict(Station=ws, Description=STATION_INFO[ws]["desc"], Type=STATION_INFO[ws]["kind"],
                 Cell=STATION_INFO[ws]["cell"], Machines=self.machines[ws],
                 Efficiency=STATION_INFO[ws]["eff"],
                 **{f"Time/part {m}": round(self.time_per_part(m, ws), 3) if visits(m, ws) else None
                    for m in MODELS},
                 **{f"Setup {m}": round(self.op_times(m, ws)["setup"], 2) if visits(m, ws) else None
                    for m in MODELS},
                 **{f"Qty/lot {m}": self.station_qty(m, ws) or None for m in MODELS},
                 **{f"Scrap factor {m}": round(self.station_factors(m)[0][ws], 4) if visits(m, ws) else None
                    for m in MODELS},
                 PM_every_units=MAINT[ws]["pm_units"], PM_min=MAINT[ws]["pm_time"],
                 MTBF_hr=MAINT[ws]["mtbf"], Repair_min=MAINT[ws]["repair"],
                 Units_between_failures=self.units_between_failures(ws))
            for ws in STATIONS]).set_index("Station")
        prod = pd.DataFrame([
            dict(Model=m, Demand=self.demand[m], EOQ=self.eoq[m][0], Lots_per_year=self.eoq[m][1],
                 Planned_yield=round(self.yield_(m), 4),
                 **{f"Sent to {seg} ({'/'.join(seg_route(m, seg)) or '-'})": q
                    for seg, q in self.segment_plan(m).items()},
                 Kits_per_year=self.parts_per_year(m))
            for m in MODELS]).set_index("Model")
        return st, prod


def get_scenario(year: int = 3, machines: dict | None = None,
                 operators: dict | None = None, **kw) -> Scenario:
    """Base scenario for a year, with optional overrides.

    Examples
    --------
    get_scenario(3)                                    # Phase 1 Year 3 plan
    get_scenario(3, machines={"9-M": 7}, tqm=True)     # extra 9-M bench + TQM
    get_scenario(2, operators={"Asm": 12}, seed=7)
    """
    m = copy.deepcopy(MACHINES[year])
    m.update(machines or {})
    o = copy.deepcopy(OPERATORS[year])
    o.update(operators or {})
    return Scenario(year=year, machines=m, operators=o,
                    demand=copy.deepcopy(DEMAND[year]), eoq=copy.deepcopy(EOQ[year]), **kw)
