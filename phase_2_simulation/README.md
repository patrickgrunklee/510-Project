# LCD plant simulation (SimPy)

An Arena-style discrete-event model of the Phase 1 LCD plant plan. It covers:
- part flow along `../precedence_diagram.png`: Separate after 1-M and 2-M, and Match of the three sub-assemblies before 7-M (M860) or 8-A (M970/M1080),
- two mixed-model cells (Fab WS 1–8, Assembly WS 9–12),
- EOQ lots,
- cross-trained operator pools,
- scrap, planned maintenance and breakdowns.

## Run it
```bash
pip install -r requirements.txt
jupyter notebook lcd_simulation.ipynb      # or open it in VS Code
```
A full run of the notebook takes about 2 min. Replications run in parallel.

Quick use from Python:
```python
from lcd_sim.params import get_scenario
from lcd_sim.stats import run, run_replications
from lcd_sim import plots as P

res = run(get_scenario(3))                       # one replication, Year 3 plan
res.bottlenecks(); res.stations; res.operators; res.scrap
P.plot_wave_levels(res); P.plot_block_diagram(res)   # daily stacked queue bars; precedence block view

reps, summary = run_replications(get_scenario(3, operators={"Fab": 8}), n=10, n_jobs=8)
```

## Files
| File | What it holds |
|---|---|
| `lcd_sim/params.py` | All input data: routing, scrap, efficiency, maintenance, machines and operators per year, EOQ, demand. Also the precedence segments, the Phase 1 scrap factors, and the `Scenario` switches. |
| `lcd_sim/model.py` | The SimPy model: lot release, Separate/Match, lot gates, machines, operator pools, scrap decide, failures. |
| `lcd_sim/stats.py` | Reports: utilization and capacity per station, machine and operator; scrap; flow time; replication CIs. |
| `lcd_sim/plots.py` | Charts: wave levels (daily stacked queue bars by model), WIP, resource states, utilization vs Phase 1, operator Gantt and heatmap, scrap Pareto, precedence block diagram. |
| `lcd_sim/experiments.py` | Scenario comparisons: maintenance, operator sweep, capacity what-ifs, Years 1–3. |
| `lcd_simulation.ipynb` | The working notebook. |
| `phase2_assumptions_simulation.ipynb` | Executed simulation of `phase2_assumptions.md`: station-wide setup teams, mixed products, and ten-replication policy comparisons. |
| `phase2_assumptions_comparison.csv` | Mean KPIs exported by the assumptions notebook. |

## Scenario switches (`get_scenario(year, ...)`)
| Switch | Default | What it does |
|---|---|---|
| `machines={"9-M": 7}` | Phase 1 count | Override the number of machines at a station. |
| `operators={"Fab": 8}` | Phase 1 crew | Override the crew per shift for a cell. |
| `run_weeks`, `warmup_weeks` | 8, 2 | Run length and warm-up. |
| `release_mode` | `"continuous"` | `"continuous"`: evenly spaced kits in product runs with lot gates. `"batch"`: whole-lot arrivals. `"cyclic"`: M860 → M970 → M1080 one kit at a time, global FIFO station queues, and no lot gates. `"heijunka"`: like `"cyclic"`, but the model order is a deterministic level sequence weighted by planned kit volume (see Experiment 3). |
| `seed` | 1 | Random seed. |
| `scrap_on`, `scrap_scale` | True, 1.0 | Turn scrap on or off. `scrap_scale=0.5` models SPC. |
| `failures_on`, `pm_on` | True, True | Breakdowns and planned maintenance. |
| `failure_mode` | `"count"` | `"count"` = every N parts. `"time"` = EXPO(MTBF) wall-clock. |
| `tqm` | False | Cuts repair and PM time by 75%. |
| `setup_scope` | `"machine"` | `"machine"`: setup when an individual machine changes model. `"station"`: Phase 1 one-setup convention with lot gates. `"workcell"`: station-group team setup once per product lot, with mixed cyclic arrivals. |
| `setup_crew` | `{}` | For `setup_scope="workcell"`, operators reserved per station, defaulting to machine count. Example: `{"5-A": 3}`. Elapsed setup = machine count × effective setup time / crew. Crew cannot exceed the cell pool. |
| `op_priority={"1-M": -1}` | none | Seize priority for operators. A lower number is served first. |
| `repair_uses_operator` | False | Repairs and PM tie up a line operator. |
| `move_time` | 0 | Minutes to move a part between stations (material handling). |

## Modeling assumptions (to confirm)
1. **Process times are deterministic.** Each part's time is (load + cycle × learning + unload) / efficiency. Setup / efficiency is charged when a machine starts a lot of a different model. Learning is 0.99^(year−1).
   Setup inputs at 3-A, 4-A, 5-A, 8-A, and 11-A are 25% of the original project values (a 75% reduction) for every model.
2. **Flow follows the precedence diagram.**
   - A lot launches `ceil(EOQ × f₁₋ₘ)` component kits at 1-M.
   - **After 1-M** the kit separates into a keyboard (→ 4-A) and an LCD/PCB set (→ 2-M).
   - **After 2-M** that separates into a power strip/PCB/LCD branch (→ 3-A → 5-A) and a backlight/sensor branch (→ 6-A).
   - Each branch is sent `ceil(EOQ × f)` items, where *f* is the Phase 1 scrap factor (`Prod Req` sheet), so enough survive to make EOQ good units.
   - Components beyond a branch's need are counted as **spare**.
   - **Match:** one sub-assembly of each type from the same lot becomes one display assembly → 7-M (M860) or 8-A (M970/M1080). Sub-assemblies left over when the lot closes are counted as **unmatched**.
3. **Scrap** is a Bernoulli draw after each operation, at the station × model rate, and removes only the item at that station (e.g. one keyboard).
4. **Failures are count-based and non-preemptive.**
   - PM happens every *PM frequency* parts.
   - Breakdowns happen every `MTBF × 60 / average minutes per part` parts, with the average weighted by the launched mix.
   - The machine finishes its part before going down.
   - Repairs use maintenance staff, not line operators, unless `repair_uses_operator=True`.
5. **One lot per station at a time.** A station finishes every surviving part of its current lot before it starts the next lot, in release order. Lots still pipeline across stations.
6. **Operators** are a FIFO pool per cell, sized at the crew per shift.
   - Manual benches hold the operator for setup and the whole cycle.
   - Automatic machines hold the operator only for setup, load and unload.
7. **Only productive time is simulated** (2 × 7.5 h shifts, 5 days, 50 weeks, so 4,500 min/week). WIP carries over between shifts.
8. **Kits arrive one at a time** at a constant interval based on planned annual kit volume (about 1.020 productive minutes in Year 3). Product runs retain their EOQ quantities and mixed-model lot sequence. The next run starts after the previous run's last kit arrival plus one interval; it does not wait for downstream completion. `release_mode="batch"` restores whole-lot releases at even intervals. No WIP cap is imposed.
   Part flow time starts at its individual kit arrival. Split components inherit that timestamp; a matched assembly uses the oldest constituent kit's timestamp. Lot flow time still starts at the first kit in the lot.
9. **Buffers are unbounded** (no blocking) and moves are instantaneous by default.
10. **Planned yields** (EOQ / kits) are 78.3% / 85.3% / 83.5%, the same as in the Phase 1 report. Because scrap is random, a lot can fall a few units short of EOQ (on average lots deliver 99.4% of EOQ).

## Experiment 2: single-unit product rotation
Notebook section 15 uses `get_scenario(3, release_mode="cyclic")`. One kit arrives every 1.020 productive minutes, in strict M860/M970/M1080 order. This keeps the total kit arrival rate but changes the arrival mix to equal thirds. Demand targets are unchanged.

Stations serve arrived units FIFO across all products. Parallel machines may process different products simultaneously; an idle machine already configured for the next unit's model is preferred. Every machine pays the destination model's setup time whenever its model changes, including the first setup. The existing zero setup inputs and 75% reductions still apply. There is no resequencing to form product batches and no WIP cap.

Per-model EOQ groups remain only as scrap-planning and component-matching records: matching units assemble as soon as components are available within their record, with no wait for the whole group. These records impose no arrival or station gates. They preserve the existing split quantities and surplus accounting. Growing WIP under the unbounded arrival policy indicates overload; an eight-week average then describes that window, not steady state.

## Experiment 3: levelled (heijunka) kit release
`get_scenario(3, release_mode="heijunka", setup_scope="workcell")` keeps everything from Experiment 2 (one kit every 1.020 productive minutes, global FIFO stations, no lot gates, workcell setups) but replaces the equal 1-2-3 rotation with a deterministic sequence weighted by each model's planned kit volume (`launch_qty × lots/yr`: about 37% M860, 30% M970, 34% M1080 in Year 3).

The sequence uses goal chasing: before kit *k*, each model's target is *k* × its kit share, and the model furthest behind its target is released (ties in M860/M970/M1080 order). Every prefix of the sequence is within one kit of the ideal mix, so each model's kits, and therefore its good output, arrive at an even rate that matches its own demand. The cyclic rotation sends each model a third of the kits, so it under-builds M860 (~90% of demand) and over-builds M970 (~113%).

Year 3, 8 replications × 20 measured weeks, weekly output as % of each model's weekly demand:

| Release | Model | Avg | Worst week | Best week | Std (pts) | Weeks ≥ 97% | Avg WIP |
|---|---|---|---|---|---|---|---|
| Continuous product runs (default) | M860 | 99.8% | 57.9% | 120.9% | 20.3 | 63% | 1,012 |
| | M970 | 101.1% | 70.8% | 124.9% | 12.0 | 70% | |
| | M1080 | 98.1% | 78.6% | 120.4% | 12.2 | 65% | |
| Cyclic 1-2-3 + workcell | M860 | 89.8% | 83.4% | 95.3% | 1.7 | 0% | 198 |
| | M970 | 112.9% | 106.0% | 120.3% | 2.0 | 100% | |
| | M1080 | 99.2% | 93.6% | 104.0% | 1.7 | 93% | |
| **Heijunka + workcell** | M860 | 99.7% | 93.9% | 103.6% | 1.6 | 96% | 195 |
| | M970 | 99.7% | 95.3% | 103.7% | 1.4 | 99% | |
| | M1080 | 99.5% | 95.1% | 103.6% | 1.4 | 96% | |

The release order is fully deterministic. The remaining week-to-week variation comes from random scrap and failures. Averages sit just under 100% because lots deliver ~99.4% of EOQ on average (assumption 10). Tests: `tests/test_heijunka.py`.

## Validation
In the notebook (section 8), the historical Phase 1 check runs with whole-lot arrivals (`release_mode="batch"`), failures and PM off, and `setup_scope="station"`. Under those settings:
- **Year 3** station utilizations match Phase 1 Table 2.3.1 within ±1.2 points.
- **Lots** deliver about EOQ good units.
- **Accounting:** there are zero lot-integrity violations, and every closed lot has nothing left owed at any station.
