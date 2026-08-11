"""DagPlanner 测试：拓扑排序分批并行、Race Strategy 取最快成功结果、
Harness 重试退避与 fallback 链、循环依赖不挂死。

运行：PYTHONPATH=src python3 tests/test_dag_planner.py   （也兼容 pytest）
"""
import json
import sys
import time

sys.path.insert(0, "src")

from agent_kernel.planners.dag import DagPlanner
from agent_kernel.ports import ModelPort, ToolPort
from agent_kernel.types import ModelOutput, RunState, ToolResult, ToolSpec


class SequenceModel(ModelPort):
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = 0

    def complete(self, messages, tools):
        output = self.outputs[min(self.calls, len(self.outputs) - 1)]
        self.calls += 1
        return output


class ProbeToolbox(ToolPort):
    """假工具箱：sleep 型验证并行调度、flaky 型验证重试、fail 型验证 fallback。"""

    def __init__(self):
        self._specs: dict[str, tuple] = {}
        self._fail_counts: dict[str, int] = {}

    def register_sleep(self, name: str, seconds: float, result: str) -> None:
        self._specs[name] = ("sleep", seconds, result)

    def register_flaky(self, name: str, fail_times: int, result: str) -> None:
        self._specs[name] = ("flaky", fail_times, result)
        self._fail_counts[name] = 0

    def register_always_fail(self, name: str) -> None:
        self._specs[name] = ("fail", None, None)

    def list_tools(self) -> list[ToolSpec]:
        return [ToolSpec(name=n, description=n) for n in self._specs]

    def call(self, name: str, args: dict) -> ToolResult:
        kind, param, result = self._specs[name]
        if kind == "sleep":
            time.sleep(param)
            return ToolResult(content=result)
        if kind == "flaky":
            self._fail_counts[name] += 1
            if self._fail_counts[name] <= param:
                raise RuntimeError(f"{name} 第 {self._fail_counts[name]} 次失败")
            return ToolResult(content=result)
        raise RuntimeError(f"{name} 总是失败")


def _decompose_output(nodes: list[dict]) -> ModelOutput:
    return ModelOutput(text=json.dumps({"nodes": nodes}, ensure_ascii=False))


def _synth_output(text: str = "已完成") -> ModelOutput:
    return ModelOutput(text=json.dumps({"final": text}, ensure_ascii=False))


def test_independent_nodes_run_in_parallel():
    tools = ProbeToolbox()
    tools.register_sleep("a", 0.15, "a-done")
    tools.register_sleep("b", 0.15, "b-done")
    tools.register_sleep("c", 0.05, "c-done")
    nodes = [
        {"id": "n1", "tool": "a", "args": {}, "depends_on": []},
        {"id": "n2", "tool": "b", "args": {}, "depends_on": []},
        {"id": "n3", "tool": "c", "args": {}, "depends_on": ["n1", "n2"]},
    ]
    model = SequenceModel([_decompose_output(nodes), _synth_output()])
    planner = DagPlanner()

    start = time.time()
    action = planner.step(RunState(), model, tools, None)
    elapsed = time.time() - start

    assert action.content == "已完成"
    assert elapsed < 0.28  # 串行需要 ~0.35s；并行两批（0.15 + 0.05）约 ~0.2s
    assert planner.last_run["results"]["n1"] == "a-done"
    assert planner.last_run["results"]["n3"] == "c-done"


def test_race_strategy_takes_fastest_success():
    tools = ProbeToolbox()
    tools.register_sleep("slow", 0.2, "slow-result")
    tools.register_sleep("fast", 0.02, "fast-result")
    nodes = [
        {
            "id": "n1",
            "candidates": [{"tool": "slow", "args": {}}, {"tool": "fast", "args": {}}],
            "depends_on": [],
        }
    ]
    model = SequenceModel([_decompose_output(nodes), _synth_output()])
    planner = DagPlanner()

    planner.step(RunState(), model, tools, None)

    assert planner.last_run["results"]["n1"] == "fast-result"


def test_harness_retries_with_backoff_then_succeeds():
    tools = ProbeToolbox()
    tools.register_flaky("flaky", fail_times=2, result="ok-after-retry")
    nodes = [{"id": "n1", "tool": "flaky", "args": {}, "depends_on": [], "max_attempts": 3, "backoff_base": 0.01}]
    model = SequenceModel([_decompose_output(nodes), _synth_output()])
    planner = DagPlanner()

    planner.step(RunState(), model, tools, None)

    assert planner.last_run["results"]["n1"] == "ok-after-retry"


def test_harness_falls_back_after_exhausting_primary():
    tools = ProbeToolbox()
    tools.register_always_fail("broken")
    tools.register_sleep("backup", 0.0, "backup-result")
    nodes = [
        {
            "id": "n1",
            "tool": "broken",
            "args": {},
            "depends_on": [],
            "fallback": [{"tool": "backup", "args": {}}],
        }
    ]
    model = SequenceModel([_decompose_output(nodes), _synth_output()])
    planner = DagPlanner()

    planner.step(RunState(), model, tools, None)

    assert planner.last_run["results"]["n1"] == "backup-result"


def test_cyclic_dependency_does_not_hang_and_is_reported():
    tools = ProbeToolbox()
    nodes = [
        {"id": "n1", "tool": "missing", "args": {}, "depends_on": ["n2"]},
        {"id": "n2", "tool": "missing", "args": {}, "depends_on": ["n1"]},
    ]
    model = SequenceModel([_decompose_output(nodes), _synth_output()])
    planner = DagPlanner()

    planner.step(RunState(), model, tools, None)  # 挂死的话测试会超时失败

    assert "循环依赖" in planner.last_run["results"]["n1"]
    assert "循环依赖" in planner.last_run["results"]["n2"]


def test_on_node_done_fires_per_node_with_run_id_and_result():
    tools = ProbeToolbox()
    tools.register_sleep("a", 0.0, "a-done")
    tools.register_sleep("b", 0.0, "b-done")
    nodes = [
        {"id": "n1", "tool": "a", "args": {}, "depends_on": []},
        {"id": "n2", "tool": "b", "args": {}, "depends_on": ["n1"]},
    ]
    model = SequenceModel([_decompose_output(nodes), _synth_output()])
    events = []
    planner = DagPlanner(on_node_done=lambda run_id, node_id, result: events.append((run_id, node_id, result)))
    state = RunState(run_id="run-xyz")

    planner.step(state, model, tools, None)

    assert set(events) == {("run-xyz", "n1", "a-done"), ("run-xyz", "n2", "b-done")}
    # n1 是 n2 的前置依赖，必须先 resolve
    node_ids_in_order = [nid for _, nid, _ in events]
    assert node_ids_in_order.index("n1") < node_ids_in_order.index("n2")


def test_on_node_done_not_called_when_query_skips_dag():
    events = []
    model = SequenceModel([_synth_output("直接回答")])
    planner = DagPlanner(on_node_done=lambda run_id, node_id, result: events.append((run_id, node_id, result)))

    planner.step(RunState(), model, ProbeToolbox(), None)

    assert events == []


def test_on_node_done_optional_default_none_does_not_break_execution():
    tools = ProbeToolbox()
    tools.register_sleep("a", 0.0, "a-done")
    nodes = [{"id": "n1", "tool": "a", "args": {}, "depends_on": []}]
    model = SequenceModel([_decompose_output(nodes), _synth_output()])
    planner = DagPlanner()  # 不传 on_node_done

    action = planner.step(RunState(), model, tools, None)

    assert action.content == "已完成"


def test_trivial_query_skips_dag_entirely():
    model = SequenceModel([_synth_output("不需要工具，直接回答")])
    planner = DagPlanner()

    action = planner.step(RunState(), model, ProbeToolbox(), None)

    assert action.content == "不需要工具，直接回答"
    assert model.calls == 1  # 没有触发 DAG 执行与 synthesis 二次调用
    assert planner.last_run == {}


if __name__ == "__main__":
    test_independent_nodes_run_in_parallel()
    test_race_strategy_takes_fastest_success()
    test_harness_retries_with_backoff_then_succeeds()
    test_harness_falls_back_after_exhausting_primary()
    test_cyclic_dependency_does_not_hang_and_is_reported()
    test_on_node_done_fires_per_node_with_run_id_and_result()
    test_on_node_done_not_called_when_query_skips_dag()
    test_on_node_done_optional_default_none_does_not_break_execution()
    test_trivial_query_skips_dag_entirely()
    print("OK: DagPlanner 测试全部通过")
