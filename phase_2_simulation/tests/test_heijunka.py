"""Checks for the levelled (heijunka) kit release sequence."""
import itertools
import unittest
from lcd_sim.params import MODELS, get_scenario
from lcd_sim.model import LCDPlant
from lcd_sim.stats import run


class HeijunkaReleaseTests(unittest.TestCase):
    def test_sequence_is_deterministic_and_tracks_kit_mix(self):
        sc = get_scenario(3, release_mode="heijunka")
        kits = sc.annual_kits()
        total = sum(kits.values())
        a = list(itertools.islice(sc.heijunka_sequence(), 5000))
        b = list(itertools.islice(sc.heijunka_sequence(), 5000))
        self.assertEqual(a, b)
        count = dict.fromkeys(MODELS, 0)
        for k, m in enumerate(a, 1):
            count[m] += 1
            for x in MODELS:  # every prefix within one kit of the ideal mix
                self.assertLess(abs(count[x] - k * kits[x] / total), 1.0)

    def test_arrivals_follow_sequence_at_constant_spacing(self):
        sc = get_scenario(3, release_mode="heijunka", keep_logs=False)
        plant = LCDPlant(sc)
        arrivals = []
        plant.spawn = lambda lot, seg: arrivals.append((plant.env.now, lot.model))
        plant.env.run(until=sc.kit_interarrival_min * 300 - 1e-8)
        expected = list(itertools.islice(sc.heijunka_sequence(), 300))
        self.assertEqual([m for _, m in arrivals], expected)
        for a, b in zip(arrivals, arrivals[1:]):
            self.assertAlmostEqual(b[0] - a[0], sc.kit_interarrival_min)
        self.assertTrue(all(not st.lot_seq for st in plant.stations.values()))

    def test_workcell_run_meets_each_model_demand(self):
        sc = get_scenario(3, release_mode="heijunka", setup_scope="workcell", keep_logs=False)
        res = run(sc)
        self.assertTrue(all(v == 0 for k, v in res.checks.items() if "must be 0" in k))
        for m in MODELS:
            self.assertGreater(res.throughput.loc[m, "% of demand"], 0.97, m)

    def test_station_setups_rejected(self):
        with self.assertRaises(ValueError):
            get_scenario(3, release_mode="heijunka", setup_scope="station")


if __name__ == "__main__":
    unittest.main()
