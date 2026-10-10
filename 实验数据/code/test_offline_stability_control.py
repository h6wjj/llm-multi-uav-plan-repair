import copy
import json
import unittest
from offline_stability_control import ROOT, enumerate_global, edit_key, npsd_key, score


class OfflineControlTests(unittest.TestCase):
    def test_edit_priority_beats_shorter_route(self):
        a = dict(bundle_edits=1, action_edits=3, route_cost=100, downstream_overlap=1, owner_tuple=["z"])
        b = dict(bundle_edits=2, action_edits=2, route_cost=1, downstream_overlap=0, owner_tuple=["a"])
        self.assertLess(edit_key(a), edit_key(b))

    def test_action_priority_before_route(self):
        a = dict(bundle_edits=1, action_edits=1, route_cost=100, downstream_overlap=0, owner_tuple=["a"])
        b = dict(a, action_edits=2, route_cost=1)
        self.assertLess(edit_key(a), edit_key(b))

    def test_score_hand_calculation(self):
        suffix = {"x": {"remaining_waypoints": [[1, 0, 0]]}, "y": {"remaining_waypoints": [[3, 0, 0]]}}
        s = score(["x", "y"], {"x": "b", "y": "b"}, {"x": "a", "y": "b"},
                  suffix, {"a": [0, 0, 0], "b": [0, 0, 0]}, {"b"}, "x")
        self.assertEqual(s["route_cost"], 3)
        self.assertEqual(s["bundle_edits"], 1)
        self.assertEqual(s["action_edits"], 1)
        self.assertAlmostEqual(s["planned_npsd"], 2 / 3)

    def fixture(self):
        folder = ROOT / "实验数据/results/b3_independent_task_distribution_formal_v1"
        task = json.loads((folder / "tasks_recoverable_failure_v1.jsonl").read_text(encoding="utf-8").splitlines()[0])
        row = json.loads((folder / "CDS_MR.jsonl").read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(task["task_id"], row["task_id"])
        return task, row["initial_execution_record"]["gym_metrics"]

    def test_domain_not_locked_to_healthy_owners(self):
        task, metrics = self.fixture()
        before = copy.deepcopy((task, metrics))
        candidates, graph, _ = enumerate_global(task, metrics)
        self.assertEqual(len(candidates), 8)
        self.assertTrue(any(c["bundle_edits"] > 1 for c in candidates))
        self.assertEqual((task, metrics), before)
        self.assertTrue(all("uav_1" not in options for options in graph.values()))

    def test_empty_domain_fails_not_drops_instance(self):
        task, metrics = self.fixture()
        for bundle in task["contract"]["mission"]["task_bundles"].values():
            if bundle["bundle_id"] == "B2":
                bundle["eligible_drone_ids"] = ["uav_1"]
        with self.assertRaises(ValueError):
            enumerate_global(task, metrics)

    def test_npsd_priority_separate_from_edit_priority(self):
        a = dict(bundle_edits=1, action_edits=10, route_cost=5, downstream_overlap=0, owner_tuple=["a"], planned_npsd=.9)
        b = dict(a, bundle_edits=2, action_edits=2, planned_npsd=.2)
        self.assertLess(edit_key(a), edit_key(b))
        self.assertGreater(npsd_key(a), npsd_key(b))


if __name__ == "__main__":
    unittest.main()
