"""SimPy model of the LCD plant - Arena-style block logic.

Arena module  ->  here
---------------------------------------------------------------
Create        ->  LCDPlant.release()          lots in a mixed-model rotation
Station/Route ->  Station (one per WS) + LCDPlant.part_flow()  (one segment of the
                  precedence diagram per entity)
Separate      ->  after 1-M (keyboard / LCD-PCB) and 2-M (power strip / backlights);
                  each branch is sent ceil(EOQ x Phase 1 scrap factor) items
Match/Batch   ->  one keyboard + power/PCB/LCD + backlight sub-assy of the same lot
                  -> one display assembly (before 7-M, or 8-A for M970/M1080)
Seize/Release ->  Station.arrive()/release() (resource set of Machine objects)
                  + cell operator pools (OperatorPool, FIFO or priority like Arena Seize priority)
Process delay ->  setup / load / cycle / unload timeouts (deterministic)
Decide (2-way by chance) -> scrap draw after every operation
Failure (Count / Time) -> Machine downtime after N parts (non-preemptive, "Wait")
Dispose       ->  completed / scrapped logs
Statistics    ->  TimeWeighted (DSTAT) + tallies + counters, cleared at warm-up
"""
from __future__ import annotations

import heapq
import itertools
import random
from collections import Counter, defaultdict, deque

import simpy

from .params import (MAINT, MERGE_INPUTS, MIN_PER_YEAR, MODELS, SEG_CHILDREN, SEGMENTS,
                     STATION_INFO, STATION_SEG, STATIONS, CELLS, Scenario, route, seg_route)

MACHINE_STATES = ["Busy", "Setup", "WaitOp", "Idle", "Failed", "PM"]


class TimeWeighted:
    """Time-persistent statistic (Arena DSTAT): exact time-average of a level."""

    def __init__(self, env, value=0.0):
        self.env, self.value = env, value
        self.last = self.t0 = env.now
        self.area, self.max = 0.0, value

    def update(self, value):
        now = self.env.now
        self.area += self.value * (now - self.last)
        self.last, self.value = now, value
        if value > self.max:
            self.max = value

    def add(self, d):
        self.update(self.value + d)

    def reset(self):
        self.area, self.last, self.t0, self.max = 0.0, self.env.now, self.env.now, self.value

    def mean(self):
        now = self.env.now
        T = now - self.t0
        return (self.area + self.value * (now - self.last)) / T if T > 0 else 0.0


class Lot:
    _ids = itertools.count(1)

    def __init__(self, model, plan, eoq, t, stations):
        self.id = next(Lot._ids)
        self.model, self.release_time, self.eoq = model, t, eoq
        self.plan = dict(plan)                    # segment -> items sent (planned)
        self.qty = plan["KIT"]                    # kits launched at 1-M
        # items still expected at each station (drives the lot gate)
        self.owed = {ws: plan[STATION_SEG[ws]] for ws in stations}
        self.remaining = dict(plan)               # segment items not yet ended
        self.sent = dict.fromkeys(plan, 0)
        self.sent["KIT"] = plan["KIT"]
        self.complete = set()
        self.buffer = {s: [] for s in MERGE_INPUTS}   # match queue
        self.spare = Counter()                    # components not sent at a Separate (surplus)
        self.unmatched = Counter()                # sub-assys left in the Match queue at lot end
        self.scrap = Counter()                    # scrapped items per segment
        self.finished = 0
        self.done_time = None

    @property
    def scrapped(self):
        return sum(self.scrap.values())


class Part:
    """One entity: a kit, a sub-assembly, or a display assembly (seg)."""
    __slots__ = ("id", "lot", "model", "seg", "t_release", "invested_min", "labor_cost")

    def __init__(self, pid, lot, seg, t, invested=0.0, labor=0.0):
        self.id, self.lot, self.model, self.seg, self.t_release = pid, lot, lot.model, seg, t
        self.invested_min = invested
        self.labor_cost = labor


class OperatorPool:
    """Pool of interchangeable (cross-trained) operators.

    Requests are served by priority (lower number first), then FIFO - the same
    as Arena's Seize priority.  The released operator goes to the back of the
    line, so work rotates across the crew.
    """

    def __init__(self, env, ops):
        self.env, self.available = env, deque(ops)
        self.queue, self._seq = [], itertools.count()

    def get(self, priority=0, count=1):
        ev = self.env.event()
        heapq.heappush(self.queue, (priority, next(self._seq), count, ev))
        self._dispatch()
        return ev

    def put(self, op):
        self.available.append(op)
        self._dispatch()

    def _dispatch(self):
        # Acquire setup teams atomically: partial crews must never hold operators.
        while self.queue and len(self.available) >= self.queue[0][2]:
            _, _, count, ev = heapq.heappop(self.queue)
            ops = [self.available.popleft() for _ in range(count)]
            ev.succeed(ops[0] if count == 1 else ops)


class Operator:
    def __init__(self, name, cell):
        self.name, self.cell = name, cell


class Machine:
    def __init__(self, sim, station, idx):
        self.sim, self.station = sim, station
        self.name = f"{station.name}#{idx + 1}"
        self.state, self.last_change = "Idle", sim.env.now
        self.state_time = dict.fromkeys(MACHINE_STATES, 0.0)
        self.last_model = None
        self.lot_id = None
        self.since_pm = self.since_fail = 0
        self.fail_pending = False
        self.units = self.setups = self.failures = self.pms = 0

    def set_state(self, s):
        now = self.sim.env.now
        self.state_time[self.state] += now - self.last_change
        self.state, self.last_change = s, now

    def reset_stats(self):
        self.set_state(self.state)
        self.state_time = dict.fromkeys(MACHINE_STATES, 0.0)
        self.units = self.setups = self.failures = self.pms = 0


class Station:
    def __init__(self, sim, ws, n):
        self.sim, self.env, self.name = sim, sim.env, ws
        self.info = STATION_INFO[ws]
        self.cell, self.kind = self.info["cell"], self.info["kind"]
        self.machines = [Machine(sim, self, i) for i in range(n)]
        self.idle = list(self.machines)
        self.waiting = defaultdict(deque)       # lot.id -> deque[(part, event, t)]
        self.lot_seq = deque()                  # lots that visit, in release order
        self.in_process = Counter()             # lot.id -> parts on machines
        self.lot_violations = 0                 # >0 would mean two lots at once
        self.last_lot_id = None
        self.setup_lot = None
        self.prepared_lots = set()
        self.group_setup_active = False
        # statistics
        self.q = TimeWeighted(self.env)         # parts waiting (all)
        self.q_gate = TimeWeighted(self.env)    # parts waiting behind the lot gate
        self.gate_blocked = TimeWeighted(self.env)  # 1 while idle machine + gated parts
        self.reset_counters()

    def reset_counters(self):
        self.group_setups = 0
        self.processed = 0
        self.wait_total = 0.0          # queue wait (arrive -> machine)
        self.op_wait_total = 0.0       # time holding a machine waiting for operator
        self.op_wait_n = 0
        self.lots_started = 0

    def reset_stats(self):
        for tw in (self.q, self.q_gate, self.gate_blocked):
            tw.reset()
        self.reset_counters()
        for m in self.machines:
            m.reset_stats()

    # -- lot gate ---------------------------------------------------------
    def current_lot(self):
        while self.lot_seq and self.lot_seq[0].owed[self.name] == 0:
            self.lot_seq.popleft()
        return self.lot_seq[0] if self.lot_seq else None

    def arrive(self, part):
        ev = self.env.event()
        self.waiting[part.lot.id].append((part, ev, self.env.now))
        self.q.add(1)
        self.dispatch()
        return ev

    def dispatch(self):
        if self.sim.sc.release_mode == "cyclic":
            if self.group_setup_active:
                return
            # Global FIFO among arrived units; machines may run different models together.
            while self.idle and self.waiting:
                lid = min(self.waiting, key=lambda k: (self.waiting[k][0][2],
                                                       self.waiting[k][0][0].id))
                dq = self.waiting[lid]
                next_part = dq[0][0]
                if self.sim.sc.setup_scope == "workcell" and lid not in self.prepared_lots:
                    t = self.sim.sc.op_times(next_part.model, self.name)["setup"]
                    if t == 0:
                        self.prepared_lots.add(lid)
                    else:
                        # Drain current work and maintenance before reserving the whole group.
                        if self.in_process or len(self.idle) != len(self.machines):
                            return
                        self.group_setup_active = True
                        self.idle.clear()
                        for m in self.machines:
                            m.set_state("WaitOp")
                        self.env.process(self.sim.setup_workcell(self, next_part))
                        return
                part, ev, t = dq.popleft()
                if not dq:
                    del self.waiting[lid]
                lot = part.lot
                m = self._pick_machine(lot)
                self.idle.remove(m)
                m.set_state("WaitOp")
                if lot.id != self.last_lot_id:
                    self.last_lot_id = lot.id
                    self.lots_started += 1
                self.in_process[lot.id] += 1
                self.q.add(-1)
                self.wait_total += self.env.now - t
                ev.succeed(m)
            self.q_gate.update(0)
            self.gate_blocked.update(0)
            return
        lot = self.current_lot()
        if lot is not None:
            dq = self.waiting.get(lot.id)
            while self.idle and dq:
                part, ev, t = dq.popleft()
                m = self._pick_machine(lot)
                self.idle.remove(m)
                m.set_state("WaitOp")
                if any(k != lot.id and c > 0 for k, c in self.in_process.items()):
                    self.lot_violations += 1
                if lot.id != self.last_lot_id:
                    self.last_lot_id = lot.id
                    self.lots_started += 1
                self.in_process[lot.id] += 1
                self.q.add(-1)
                self.wait_total += self.env.now - t
                ev.succeed(m)
            if dq is not None and not dq:
                self.waiting.pop(lot.id, None)
        cur = len(self.waiting.get(lot.id, ())) if lot is not None else 0
        gated = self.q.value - cur
        self.q_gate.update(gated)
        self.gate_blocked.update(1.0 if (self.idle and gated > 0) else 0.0)

    def _pick_machine(self, lot):
        for m in self.idle:                       # already running this lot
            if m.lot_id == lot.id:
                return m
        for m in self.idle:                       # already set up for the model
            if m.last_model == lot.model:
                return m
        return self.idle[0]

    def release(self, m, lot):
        self.in_process[lot.id] -= 1
        if self.in_process[lot.id] == 0:
            del self.in_process[lot.id]
        if self.sim.downtime_due(m):
            self.env.process(self.sim.downtime(m))
        else:
            m.set_state("Idle")
            self.idle.append(m)
        self.dispatch()

    def owed_done(self, lot, k=1):
        lot.owed[self.name] -= k
        if lot.owed[self.name] == 0:
            self.dispatch()

    def queue_by_model(self):
        out = dict.fromkeys(MODELS, 0)
        for dq in self.waiting.values():
            if dq:
                out[dq[0][0].model] += len(dq)
        return out


class LCDPlant:
    def __init__(self, sc: Scenario):
        self.sc = sc
        self.env = simpy.Environment()
        self.rng = random.Random(sc.seed)
        Lot._ids = itertools.count(1)
        self.stat_start = sc.warmup_min
        self.log_end = sc.warmup_min + sc.op_log_weeks * 4500
        self.stations = {ws: Station(self, ws, sc.machines[ws]) for ws in STATIONS}
        self.pools, self.operators = {}, []
        for cell in CELLS:
            ops = [Operator(f"{cell}-{i + 1}", cell) for i in range(sc.operators[cell])]
            self.operators += ops
            self.pools[cell] = OperatorPool(self.env, ops)
        self.ops_busy = {c: TimeWeighted(self.env) for c in CELLS}
        self.wip = TimeWeighted(self.env)
        self.cell_wip = {c: TimeWeighted(self.env) for c in CELLS}
        self.op_time = defaultdict(float)          # (operator, station) -> min (post warm-up)
        self.op_log = []                           # task rows (if keep_logs)
        self.scrap_log, self.done_log, self.lots = [], [], []
        self.trace = []
        self.part_ids = itertools.count(1)
        self.ubf = {ws: sc.units_between_failures(ws) for ws in STATIONS}
        self.routes = {m: route(m) for m in MODELS}
        self.seg_routes = {(m, g): seg_route(m, g) for m in MODELS for g in SEGMENTS}
        self.plans = {m: sc.segment_plan(m) for m in MODELS}

        self.env.process(self.release())
        self.env.process(self._warmup())
        if sc.keep_logs:
            self.env.process(self.monitor())
        if sc.failures_on and sc.failure_mode == "time":
            for st in self.stations.values():
                if MAINT[st.name]["mtbf"]:
                    for m in st.machines:
                        self.env.process(self.time_failures(m))

    # ------------------------------------------------------------------ run
    def run(self):
        self.env.run(until=self.sc.warmup_min + self.sc.run_min)
        for st in self.stations.values():            # close state accumulators
            for m in st.machines:
                m.set_state(m.state)
        return self

    def _warmup(self):
        yield self.env.timeout(self.stat_start)
        for st in self.stations.values():
            st.reset_stats()
        for tw in [self.wip, *self.ops_busy.values(), *self.cell_wip.values()]:
            tw.reset()

    # -------------------------------------------------------------- release
    def lot_sequence(self):
        pos = []
        for m in MODELS:
            n = self.sc.eoq[m][1]
            pos += [((i + 0.5) / n, k, m) for k, i in enumerate(range(n))]
        return [m for _, _, m in sorted(pos)]

    def release(self):
        if self.sc.release_mode == "cyclic":
            # EOQ groups remain accounting/matching containers, never release or station gates.
            active, left = {}, {}
            for model in itertools.cycle(MODELS):
                if left.get(model, 0) == 0:
                    plan = self.plans[model]
                    active[model] = Lot(model, plan, self.sc.eoq[model][0],
                                        self.env.now, self.routes[model])
                    self.lots.append(active[model])
                    left[model] = plan["KIT"]
                self.spawn(active[model], "KIT")
                left[model] -= 1
                yield self.env.timeout(self.sc.kit_interarrival_min)
            return
        seq = self.lot_sequence()
        interval = MIN_PER_YEAR / len(seq)
        kit_interval = self.sc.kit_interarrival_min
        for model in itertools.cycle(seq):
            plan = self.plans[model]
            lot = Lot(model, plan, self.sc.eoq[model][0], self.env.now, self.routes[model])
            self.lots.append(lot)
            for ws in self.routes[model]:
                self.stations[ws].lot_seq.append(lot)
            for _ in range(plan["KIT"]):
                self.spawn(lot, "KIT")
                if self.sc.release_mode == "continuous":
                    yield self.env.timeout(kit_interval)
            if self.sc.release_mode == "batch":
                yield self.env.timeout(interval)

    def spawn(self, lot, seg, invested=0.0, labor=0.0, t_release=None):
        self.wip.add(1)
        p = Part(next(self.part_ids), lot, seg,
                 self.env.now if t_release is None else t_release, invested, labor)
        self.env.process(self.part_flow(p))

    # ------------------------------------------------------------ operators
    def setup_workcell(self, st, part):
        """One station-wide setup per product lot, with an atomic operator team."""
        crew = self.sc.setup_crew.get(st.name, len(st.machines))
        ops = yield self.pools[st.cell].get(self.sc.op_priority.get(st.name, 0), crew)
        ops = [ops] if crew == 1 else ops
        self.ops_busy[st.cell].add(crew)
        start = self.env.now
        for m in st.machines:
            m.set_state("Setup")
        duration = len(st.machines) * self.sc.op_times(part.model, st.name)["setup"] / crew
        yield self.env.timeout(duration)
        if self.env.now >= self.stat_start:
            st.group_setups += 1
        for op in ops:
            self.put_op(op, st.name, f"{st.name} group", "setup", part, start)
        st.prepared_lots.add(part.lot.id)
        for m in st.machines:
            m.setups += 1
            m.set_state("Idle")
            st.idle.append(m)
        st.group_setup_active = False
        st.dispatch()

    def get_op(self, cell, ws=None):
        op = yield self.pools[cell].get(self.sc.op_priority.get(ws, 0) if ws else 0)
        self.ops_busy[cell].add(1)
        return op

    def put_op(self, op, ws, mname, task, part, start):
        self.log_task(op, ws, mname, task, part, start)
        self.ops_busy[op.cell].add(-1)
        self.pools[op.cell].put(op)

    def log_task(self, op, ws, mname, task, part, start):
        end = self.env.now
        clipped = end - max(start, self.stat_start)
        if clipped > 0:
            self.op_time[(op.name, ws)] += clipped
        if self.sc.keep_logs and self.stat_start <= start < self.log_end:
            self.op_log.append((op.name, op.cell, ws, mname, task,
                                part.lot.id if part else None, part.model if part else None,
                                start, end))
        if part is not None:
            part.labor_cost += (end - start) * STATION_INFO[ws]["rate"] / 60

    # ------------------------------------------------------------ part flow
    def part_flow(self, part):
        env, sc = self.env, self.sc
        lot, model = part.lot, part.model
        rte = self.seg_routes[(model, part.seg)]
        for i, ws in enumerate(rte):
            st = self.stations[ws]
            self.cell_wip[st.cell].add(1)
            m = yield st.arrive(part)
            t = sc.op_times(model, ws)
            cell = st.cell
            t_req = env.now
            op = yield env.process(self.get_op(cell, ws))
            st.op_wait_total += env.now - t_req
            st.op_wait_n += 1
            # setup when this machine changes model (once per machine per lot)
            if m.lot_id != lot.id:
                first = st.setup_lot != lot.id       # first machine at this station for the lot
                st.setup_lot = lot.id
                if sc.setup_scope != "workcell" and m.last_model != model and t["setup"] > 0 and \
                        (sc.setup_scope == "machine" or first):
                    m.set_state("Setup")
                    s0 = env.now
                    yield env.timeout(t["setup"])
                    m.setups += 1
                    self.log_task(op, ws, m.name, "setup", part, s0)
                m.lot_id, m.last_model = lot.id, model
            m.set_state("Busy")
            if st.kind == "M":                      # operator for the whole cycle
                s0 = env.now
                dur = t["load"] + t["cycle"] + t["unload"]
                yield env.timeout(dur)
                self.put_op(op, ws, m.name, "run", part, s0)
            else:                                   # load -> unattended cycle -> unload
                s0 = env.now
                if t["load"] > 0:
                    yield env.timeout(t["load"])
                self.put_op(op, ws, m.name, "load", part, s0)
                yield env.timeout(t["cycle"])
                if t["unload"] > 0:
                    m.set_state("WaitOp")
                    t_req = env.now
                    op = yield env.process(self.get_op(cell, ws))
                    st.op_wait_total += env.now - t_req
                    m.set_state("Busy")
                    s0 = env.now
                    yield env.timeout(t["unload"])
                    self.put_op(op, ws, m.name, "unload", part, s0)
            part.invested_min += t["load"] + t["cycle"] + t["unload"]
            m.units += 1
            m.since_pm += 1
            m.since_fail += 1
            if env.now >= self.stat_start:
                st.processed += 1
            st.release(m, lot)
            st.owed_done(lot)
            self.cell_wip[cell].add(-1)
            # 2-way by-chance decide: scrap?
            if self.rng.random() < sc.scrap_rate(model, ws):
                lot.scrap[part.seg] += 1
                self.scrap_log.append((part.id, model, lot.id, part.seg, ws, env.now,
                                       part.invested_min, part.labor_cost))
                for d in rte[i + 1:]:
                    self.stations[d].owed_done(lot)
                self.wip.add(-1)
                self.item_end(lot, part.seg)
                return
            if sc.move_time:
                yield env.timeout(sc.move_time)
        self.segment_output(part)
        self.item_end(lot, part.seg)

    # ------------------------------------------------- separate / match logic
    def segment_output(self, part):
        lot, seg = part.lot, part.seg
        if seg in SEG_CHILDREN:                         # Separate
            self.wip.add(-1)
            for c in SEG_CHILDREN[seg]:
                if lot.sent[c] < lot.plan[c]:
                    lot.sent[c] += 1
                    self.spawn(lot, c, part.invested_min, part.labor_cost, part.t_release)
                else:
                    lot.spare[c] += 1                   # component not needed
        elif seg in MERGE_INPUTS:                       # wait in the match queue
            lot.buffer[seg].append(part)
            self.try_match(lot)
        else:                                           # finished display -> Dispose
            self.wip.add(-1)
            lot.finished += 1
            self.done_log.append((part.id, part.model, lot.id, part.t_release, self.env.now,
                                  part.invested_min, part.labor_cost))

    def try_match(self, lot):
        b = lot.buffer
        while all(b[s] for s in MERGE_INPUTS) and lot.sent["ASM"] < lot.plan["ASM"]:
            items = [b[s].pop() for s in MERGE_INPUTS]
            lot.sent["ASM"] += 1
            self.wip.add(-len(items))
            self.spawn(lot, "ASM", sum(i.invested_min for i in items),
                       sum(i.labor_cost for i in items), min(i.t_release for i in items))

    def item_end(self, lot, seg):
        lot.remaining[seg] -= 1
        if lot.remaining[seg] == 0:
            self.seg_complete(lot, seg)

    def cancel(self, lot, seg, k):
        """k planned items of seg will never come (upstream scrap): release the gates."""
        if k <= 0:
            return
        for ws in self.seg_routes[(lot.model, seg)]:
            self.stations[ws].owed_done(lot, k)
        lot.remaining[seg] -= k
        if lot.remaining[seg] == 0:
            self.seg_complete(lot, seg)

    def seg_complete(self, lot, seg):
        lot.complete.add(seg)
        for c in SEG_CHILDREN.get(seg, ()):
            self.cancel(lot, c, lot.plan[c] - lot.sent[c])
        if seg in MERGE_INPUTS and all(s in lot.complete for s in MERGE_INPUTS):
            self.try_match(lot)
            for s in MERGE_INPUTS:                      # unmatched sub-assemblies
                lot.unmatched[s] += len(lot.buffer[s])
                self.wip.add(-len(lot.buffer[s]))
                lot.buffer[s].clear()
            self.cancel(lot, "ASM", lot.plan["ASM"] - lot.sent["ASM"])
        if seg == "ASM":
            lot.done_time = self.env.now

    # ------------------------------------------------------------- failures
    def downtime_due(self, m):
        sc, mt = self.sc, MAINT[m.station.name]
        if sc.pm_on and mt["pm_units"] and m.since_pm >= mt["pm_units"]:
            return True
        if sc.failures_on:
            if sc.failure_mode == "count":
                u = self.ubf[m.station.name]
                if u and m.since_fail >= u:
                    return True
            elif m.fail_pending:
                return True
        return False

    def downtime(self, m):
        """Non-preemptive: called after a part finishes. PM first, then repair."""
        sc, mt, st = self.sc, MAINT[m.station.name], m.station
        factor = sc.tqm_factor if sc.tqm else 1.0
        jobs = []
        if sc.pm_on and mt["pm_units"] and m.since_pm >= mt["pm_units"]:
            jobs.append(("PM", mt["pm_time"] * factor))
            m.since_pm = 0
        if sc.failures_on and ((sc.failure_mode == "count" and self.ubf[st.name]
                                and m.since_fail >= self.ubf[st.name]) or m.fail_pending):
            jobs.append(("Failed", mt["repair"] * factor))
            m.since_fail = 0
            m.fail_pending = False
        for state, dur in jobs:
            op = None
            if sc.repair_uses_operator:
                m.set_state("WaitOp")
                op = yield self.env.process(self.get_op(st.cell))
            m.set_state(state)
            s0 = self.env.now
            if state == "PM":
                m.pms += 1
            else:
                m.failures += 1
            yield self.env.timeout(dur)
            if op is not None:
                self.put_op(op, st.name, m.name, "repair" if state == "Failed" else "PM", None, s0)
        m.set_state("Idle")
        st.idle.append(m)
        st.dispatch()

    def time_failures(self, m):
        mtbf = MAINT[m.station.name]["mtbf"] * 60
        while True:
            yield self.env.timeout(self.rng.expovariate(1 / mtbf))
            if m in m.station.idle:              # idle -> goes down now
                m.station.idle.remove(m)
                m.fail_pending = True
                yield self.env.process(self.downtime(m))
            else:
                m.fail_pending = True            # goes down after current part

    # -------------------------------------------------------------- monitor
    def monitor(self):
        dt = self.sc.monitor_interval
        while True:
            row = {"t": self.env.now, "WIP": self.wip.value}
            for c in CELLS:
                row[f"WIP {c}"] = self.cell_wip[c].value
                row[f"Ops busy {c}"] = self.ops_busy[c].value
            for ws, st in self.stations.items():
                row[f"Q {ws}"] = st.q.value
                for mdl, n in st.queue_by_model().items():
                    row[f"Q {ws} {mdl}"] = n
                row[f"Gate {ws}"] = st.q_gate.value
                row[f"Busy {ws}"] = sum(m.state in ("Busy", "Setup") for m in st.machines)
                row[f"Down {ws}"] = sum(m.state in ("Failed", "PM") for m in st.machines)
            self.trace.append(row)
            yield self.env.timeout(dt)


def simulate(sc: Scenario) -> LCDPlant:
    """Build and run one replication."""
    return LCDPlant(sc).run()
