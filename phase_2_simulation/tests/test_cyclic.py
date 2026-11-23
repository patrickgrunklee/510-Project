"""Behavior checks for single-unit rotation, mixed dispatch, and changeover costs."""
import unittest
from lcd_sim.params import MODELS, get_scenario
from lcd_sim.model import LCDPlant, Lot, Part
from lcd_sim.stats import run


class CyclicExperimentTests(unittest.TestCase):
    def test_unit_arrivals_repeat_123_at_constant_spacing(self):
        sc = get_scenario(3, release_mode="cyclic", keep_logs=False)
        plant = LCDPlant(sc)
        arrivals = []
        plant.spawn = lambda lot, seg: arrivals.append((plant.env.now, lot.model))
        plant.env.run(until=sc.kit_interarrival_min * 30 - 1e-8)
        self.assertEqual([m for _, m in arrivals], MODELS * 10)
        for a, b in zip(arrivals, arrivals[1:]):
            self.assertAlmostEqual(b[0] - a[0], sc.kit_interarrival_min)
        self.assertTrue(all(not st.lot_seq for st in plant.stations.values()))

    def test_different_models_share_parallel_machines_and_fifo_queue(self):
        sc = get_scenario(3, release_mode="cyclic", keep_logs=False,
                          failures_on=False, pm_on=False)
        plant = LCDPlant(sc)
        lots = [Lot(m, plant.plans[m], sc.eoq[m][0], 0, plant.routes[m]) for m in MODELS]
        st = plant.stations["1-M"]  # two machines
        parts = [Part(i, lots[j], "KIT", 0) for i, j in enumerate((0, 1, 2, 0))]
        events = [st.arrive(p) for p in parts]
        self.assertTrue(events[0].triggered and events[1].triggered)
        self.assertEqual(len(st.in_process), 2)
        self.assertFalse(events[2].triggered)
        st.release(events[0].value, lots[0])
        self.assertTrue(events[2].triggered)
        self.assertFalse(events[3].triggered)
        self.assertEqual(st.q_gate.value, 0)

    def test_each_machine_model_switch_pays_destination_setup(self):
        sc = get_scenario(3, release_mode="cyclic", warmup_weeks=0,
                          run_weeks=0.3, op_log_weeks=1,
                          failures_on=False, pm_on=False, scrap_on=False)
        result = run(sc)
        log = result.op_log
        switches = 0
        for machine, rows in log.groupby("Machine"):
            last_model, pending = None, None
            for row in rows.sort_values(["Start", "End"], kind="stable").itertuples():
                if row.Task == "setup":
                    self.assertNotEqual(last_model, row.Model)
                    expected = sc.op_times(row.Model, row.Station)["setup"]
                    self.assertAlmostEqual(row.End - row.Start, expected)
                    pending = row.Model
                    switches += 1
                elif row.Task in ("run", "load"):
                    needed = last_model != row.Model and sc.op_times(row.Model, row.Station)["setup"] > 0
                    self.assertEqual(pending == row.Model, needed, (machine, row))
                    last_model, pending = row.Model, None
        self.assertGreater(switches, 20)
        self.assertTrue(all(v == 0 for k, v in result.checks.items() if "must be 0" in k))
        self.assertTrue((result.stations["Gate-blocked %"] == 0).all())


if __name__ == "__main__":
    unittest.main()
