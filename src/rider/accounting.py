"""Non-core runtime timing and usage accounting utility."""
from contextlib import contextmanager
from copy import deepcopy
import time


class RuntimeAccounting:
    """Exclusive sequential intervals and policy-call deltas, including copies."""

    def __init__(self):
        self.started = time.perf_counter()
        self.cursor = self.started
        self.intervals = []
        self.stack = []
        self.policy_calls = []

    def _flush(self, now):
        if self.stack:
            self.intervals.append({"stage": self.stack[-1],
                                   "start_seconds": self.cursor - self.started,
                                   "end_seconds": now - self.started,
                                   "wall_seconds": now - self.cursor})
        self.cursor = now

    @contextmanager
    def stage(self, kind):
        self._flush(time.perf_counter())
        self.stack.append(kind)
        try:
            yield
        finally:
            self._flush(time.perf_counter())
            self.stack.pop()

    def call(self, kind, callback, *args, **kwargs):
        with self.stage(kind):
            return callback(*args, **kwargs)

    def policy_call(self, kind, policy, callback, *args, **kwargs):
        stats = getattr(policy, "stats", None)
        before = deepcopy(stats()) if callable(stats) else None
        try:
            return callback(*args, **kwargs)
        finally:
            after = deepcopy(stats()) if callable(stats) else None
            delta = None
            if before is not None and after is not None:
                delta = {key: value - before.get("qwen", {}).get(key, 0)
                         for key, value in after.get("qwen", {}).items()
                         if type(value) in (int, float)}
            self.policy_calls.append({"stage": kind, "qwen_delta": delta,
                                      "stats_available": delta is not None})

    def summary(self):
        now = time.perf_counter()
        totals = {}
        for row in self.intervals:
            totals[row["stage"]] = totals.get(row["stage"], 0.0) + row["wall_seconds"]
        task = sum(value for key, value in totals.items() if key.startswith("task:"))
        diagnostic = sum(value for key, value in totals.items() if not key.startswith("task:"))
        qwen, by_stage = {}, {}
        for row in self.policy_calls:
            if row["qwen_delta"] is None:
                continue
            bucket = by_stage.setdefault(row["stage"], {})
            for key, value in row["qwen_delta"].items():
                qwen[key] = qwen.get(key, 0) + value
                bucket[key] = bucket.get(key, 0) + value
        complete = all(row["stats_available"] for row in self.policy_calls)
        return {"task_wall_seconds": task,
                "task_wall_complete": False,
                "wall_accounting_missing": ["Uninstrumented setup/finalization cannot be assigned precisely to deployment versus research", "Diagnostic cache warming prevents counterfactual cold-cache deployment timing", "Historical runs have no recoverable separated stage intervals"],
                "research_total_wall_seconds": now - self.started,
                "diagnostic_wall_seconds": diagnostic,
                "wall_stage_breakdown": totals,
                "wall_stage_intervals": deepcopy(self.intervals),
                "unclassified_wall_seconds": max(0.0, now - self.started - task - diagnostic),
                "wall_accounting_note": "Measured exclusive instrumented intervals including routing/update and persistence; setup/finalization unclassified. Not counterfactual cold-cache deployment latency.",
                "policy_stats_campaign": {"qwen": qwen if complete else None,
                                          "qwen_by_stage": by_stage,
                                          "calls": deepcopy(self.policy_calls),
                                          "complete": complete,
                                          "token_usage_complete": False,
                                          "missing": ["Failed backend calls may not expose token usage in policy stats; no monetary bill inferred", "Prior campaigns and shared-cache creation costs are not charged again to this runtime"],
                                          "scope": "runtime policy-call deltas including copies; excludes construction and previous campaigns"}}
