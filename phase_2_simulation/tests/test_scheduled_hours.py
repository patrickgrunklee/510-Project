"""Scheduled resources accrue for the full horizon, even with zero output."""
import unittest
from lcd_sim.model import LCDPlant, MACHINE_STATES
from lcd_sim.params import get_scenario, MIN_PER_WEEK
from lcd_sim.stats import collect


class NoArrivalsPlant(LCDPlant):
    def release(self):
        yield self.env.timeout(1e9)


class ScheduledHoursTests(unittest.TestCase):
    def test_scheduled_hours_do_not_depend_on_product_arrivals(self):
        sc = get_scenario(3, run_weeks=0.1, warmup_weeks=0, keep_logs=False)
        plant = NoArrivalsPlant(sc).run()
        # No completed flow is required to calculate all scheduled state hours.
        hours = sc.run_weeks * MIN_PER_WEEK / 60
        for cell, crew in sc.operators.items():
            self.assertEqual(plant.ops_busy[cell].mean(), 0)
            self.assertEqual(crew * hours, crew * plant.env.now / 60)
        for station in plant.stations.values():
            for machine in station.machines:
                self.assertAlmostEqual(sum(machine.state_time.values()) / 60, hours)
                self.assertAlmostEqual(machine.state_time['Idle'] / 60, hours)

    def test_reported_labor_and_machine_states_reconcile(self):
        sc = get_scenario(3, run_weeks=0.1, warmup_weeks=0, keep_logs=False,
                          release_mode='cyclic', setup_scope='workcell', scrap_scale=.25)
        result = collect(LCDPlant(sc).run())
        hours = sc.run_weeks * MIN_PER_WEEK / 60
        for cell, row in result.cells.iterrows():
            self.assertAlmostEqual(row['Scheduled labor-hr'], sc.operators[cell] * hours)
            self.assertAlmostEqual(row['Scheduled labor-hr'], row['Busy labor-hr'] + row['Idle labor-hr'])
        for ws, row in result.stations.iterrows():
            self.assertAlmostEqual(row['Scheduled machine-hr'], sc.machines[ws] * hours)
            self.assertAlmostEqual(row['Scheduled machine-hr'], sum(row[f'{s} machine-hr'] for s in MACHINE_STATES))


if __name__ == '__main__':
    unittest.main()
