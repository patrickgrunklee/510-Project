"""Checks for the station-group setup assumptions and pooled setup labor."""
import unittest
import simpy
from lcd_sim.params import get_scenario
from lcd_sim.model import LCDPlant, Lot, Part, Operator, OperatorPool


class IsolatedPlant(LCDPlant):
    def release(self):
        yield self.env.timeout(1e9)


class WorkcellSetupTests(unittest.TestCase):
    def plant(self, crew=None):
        sc = get_scenario(3, release_mode="cyclic", setup_scope="workcell",
                          setup_crew={} if crew is None else {"5-A": crew}, setup_staggered=False,
                          warmup_weeks=0, keep_logs=True,
                          failures_on=False, pm_on=False)
        return IsolatedPlant(sc)

    def part(self, plant, model, pid=0):
        lot = Lot(model, plant.plans[model], plant.sc.eoq[model][0],
                  plant.env.now, plant.routes[model])
        return Part(pid, lot, "PWR", plant.env.now)

    def test_setup_duration_and_labor_scale_with_crew(self):
        for crew in (1, 3, 6, 7):
            with self.subTest(crew=crew):
                plant = self.plant(crew)
                st = plant.stations["5-A"]
                part = self.part(plant, "M860")
                ev = st.arrive(part)
                duration = 6 * plant.sc.op_times("M860", "5-A")["setup"] / crew
                plant.env.run(until=duration - 1e-6)
                self.assertFalse(ev.triggered)
                self.assertEqual(plant.ops_busy["Fab"].value, crew)
                self.assertEqual(len(st.idle), 0)
                plant.env.run(until=duration + 1e-6)
                self.assertTrue(ev.triggered)
                self.assertEqual(st.group_setups, 1)
                self.assertEqual(plant.ops_busy["Fab"].value, 0)
                logs = [row for row in plant.op_log if row[4] == "setup"]
                self.assertEqual(len(logs), crew)
                self.assertAlmostEqual(sum(row[-1] - row[-2] for row in logs),
                                       6 * plant.sc.op_times("M860", "5-A")["setup"])
                # More units from this prepared group need no additional setup.
                again = st.arrive(Part(1, part.lot, "PWR", plant.env.now))
                self.assertTrue(again.triggered)
                self.assertEqual(st.group_setups, 1)

    def test_prepared_products_can_run_simultaneously(self):
        plant = self.plant()
        st = plant.stations["5-A"]
        parts = [self.part(plant, model, i) for i, model in enumerate(("M860", "M970", "M1080"))]
        st.prepared_lots.update(p.lot.id for p in parts)
        self.assertTrue(all(st.arrive(p).triggered for p in parts))
        self.assertEqual(len(st.in_process), 3)
        self.assertEqual(st.group_setups, 0)

    def test_new_lot_of_same_product_requires_a_new_group_setup(self):
        plant = self.plant()
        st = plant.stations["5-A"]
        first = self.part(plant, "M860")
        second = self.part(plant, "M860", 1)
        duration = plant.sc.op_times("M860", "5-A")["setup"]
        first_event = st.arrive(first)
        plant.env.run(until=duration + 1e-6)
        second_event = st.arrive(second)
        self.assertFalse(second_event.triggered)
        # The group must first finish the previous unit; no machine is preempted.
        self.assertFalse(st.group_setup_active)
        st.release(first_event.value, first.lot)
        plant.env.run(until=2 * duration)
        self.assertFalse(second_event.triggered)
        plant.env.run(until=2 * duration + 2e-6)
        self.assertTrue(second_event.triggered)
        self.assertEqual(st.group_setups, 2)

    def test_atomic_team_request_does_not_hold_partial_crew(self):
        env = simpy.Environment()
        ops = [Operator(str(i), "Fab") for i in range(3)]
        pool = OperatorPool(env, ops)
        first = pool.get().value
        team = pool.get(count=3)
        self.assertFalse(team.triggered)
        self.assertEqual(len(pool.available), 2)
        pool.put(first)
        self.assertTrue(team.triggered)
        self.assertEqual(len(team.value), 3)

    def test_oversized_crew_is_rejected(self):
        with self.assertRaises(ValueError):
            self.plant(8)



class StaggeredSetupTests(unittest.TestCase):
    """setup_staggered=True: each machine sets up on its own with one operator, no station-wide drain."""

    def test_new_lot_does_not_wait_for_busy_or_failed_machines(self):
        sc = get_scenario(3, release_mode="cyclic", setup_scope="workcell", setup_staggered=True,
                          warmup_weeks=0, failures_on=False, pm_on=False)
        plant = IsolatedPlant(sc)
        st = plant.stations["5-A"]
        old = Lot("M860", plant.plans["M860"], sc.eoq["M860"][0], 0, plant.routes["M860"])
        new = Lot("M970", plant.plans["M970"], sc.eoq["M970"][0], 0, plant.routes["M970"])
        self.assertTrue(st.arrive(Part(0, old, "PWR", 0)).triggered)   # one machine busy on the old lot
        down = st.idle.pop()                                           # one machine down for repair
        down.set_state("Failed")
        self.assertTrue(st.arrive(Part(1, new, "PWR", 0)).triggered)   # a free machine takes the new lot now
        self.assertFalse(st.group_setup_active)
        self.assertEqual(st.group_setups, 0)

    def test_full_run_uses_per_machine_setups(self):
        from lcd_sim.stats import run
        res = run(get_scenario(3, release_mode="heijunka", setup_scope="workcell", scrap_scale=0.25,
                               keep_logs=False, run_weeks=2))
        self.assertTrue(all(v == 0 for k, v in res.checks.items() if "must be 0" in k))


if __name__ == "__main__":
    unittest.main()
