"""DAG 动态图 Runtime：把任务拆成带依赖的节点图，按拓扑序分批并行执行独立节点；
每个节点执行套一层 Harness（超时 + 重试退避 + fallback 工具链）；节点给多个候选调用时
用 Race Strategy 并发竞速，取最快成功的结果。

跟 ReactPlanner 的关系：复用同一套"输出单个 JSON 动作"的协议风格，但整个 DAG 的拆解
→ 并行执行 → 结果汇总，全部在一次 planner.step() 里做完，对内核只暴露一次 FinalAnswer——
kernel.py 完全不用改，PlannerPort 契约不变（对齐 ports.py 的扩展性规则：新概念＝新策略）。

已知边界（ponytail）：
- DAG 内部的工具调用不过 EffectLedger/HITL approval，这两个横切面目前只在 kernel 的
  单步 ToolCall 路径生效；真要接，需要把 EffectLedger 一并传给 planner，当前用例
  （搜索/检索类只读工具）不需要。
- 内部执行也不过 EventBus，可观测性只看得到最终 FinalAnswer；PlannerPort.step() 拿不到
  bus，接上需要改端口契约，当前不做。
- 超时基于 ThreadPoolExecutor：Python 线程杀不掉，超时只是"不再等待"，后台线程会跑到
  自然结束——重的/有副作用的工具不适合设超时，配合只读工具使用。
"""
from __future__ import annotations

import json
import time
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FutureTimeoutError, as_completed
from dataclasses import dataclass, field

from ..ports import MemoryPort, ModelPort, PlannerPort, ToolPort
from ..types import Action, FinalAnswer, Message, RunState, ToolResult
from .react import ActionParseError

DECOMPOSE_TMPL = """你是一个会拆解任务的调度器。可用工具：
{tools}

规则：只输出一个 JSON 对象，不要输出其它任何内容。
不需要工具就能直接回答时输出 {{"final": "<答案>"}}。
需要一个或多个工具时输出：
{{"nodes": [
  {{"id": "n1", "tool": "<name>", "args": {{...}}, "depends_on": []}},
  {{"id": "n2", "tool": "<name>", "args": {{...}}, "depends_on": ["n1"]}}
]}}
depends_on 为空的节点会并行执行；有依赖的节点等前置节点全部完成后才执行。
节点可选字段：candidates（多个候选调用并发竞速取最快成功结果，格式同 tool/args 的列表）、
fallback（主调用失败后依次尝试的备用调用列表）、timeout（秒）、max_attempts（重试次数）。"""

SYNTHESIS_TMPL = """已执行完下列节点，请综合结果给出最终回答，只输出
{{"final": "<答案>"}}，不要输出其它任何内容：

{results}"""


@dataclass
class DagNode:
    id: str
    tool: str = ""
    args: dict = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)
    candidates: list[dict] = field(default_factory=list)
    fallback: list[dict] = field(default_factory=list)
    timeout: float | None = None
    max_attempts: int = 1
    backoff_base: float = 0.2


class DagPlanner(PlannerPort):
    def __init__(
        self,
        max_workers: int = 8,
        default_timeout: float | None = None,
        default_max_attempts: int = 1,
        default_backoff_base: float = 0.2,
    ) -> None:
        self.max_workers = max_workers
        self.default_timeout = default_timeout
        self.default_max_attempts = default_max_attempts
        self.default_backoff_base = default_backoff_base
        self.last_run: dict = {}  # ponytail: 仅供测试/调试观察，非线程安全（同实例不要并发跑多个 run）

    def step(
        self,
        state: RunState,
        model: ModelPort,
        tools: ToolPort,
        memory: MemoryPort | None,
    ) -> Action:
        tool_specs = tools.list_tools()
        tool_desc = "\n".join(
            f"- {t.name}: {t.description}; 参数 JSON Schema: "
            f"{json.dumps(t.parameters, ensure_ascii=False)}"
            for t in tool_specs
        ) or "(无)"
        system = Message("system", DECOMPOSE_TMPL.format(tools=tool_desc))
        output = model.complete([system, *state.messages], [])
        parsed = self._parse(output.text)

        if "final" in parsed:
            return FinalAnswer(content=str(parsed["final"]))

        nodes = [self._parse_node(d) for d in parsed["nodes"]]
        results = self._execute_dag(nodes, tools)
        self.last_run = {"nodes": [n.id for n in nodes], "results": dict(results)}

        results_text = "\n".join(f"- {node_id}: {content}" for node_id, content in results.items())
        synth_output = model.complete([Message("user", SYNTHESIS_TMPL.format(results=results_text))], [])
        synth_parsed = self._parse(synth_output.text)
        return FinalAnswer(content=str(synth_parsed.get("final", synth_output.text)))

    # --------------------------------------------------------------- parsing
    @staticmethod
    def _parse(text: str) -> dict:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1 or end < start:
            raise ActionParseError(text)
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ActionParseError(text) from exc

    def _parse_node(self, d: dict) -> DagNode:
        return DagNode(
            id=d["id"],
            tool=d.get("tool", ""),
            args=d.get("args", {}),
            depends_on=list(d.get("depends_on", [])),
            candidates=list(d.get("candidates", [])),
            fallback=list(d.get("fallback", [])),
            timeout=d.get("timeout", self.default_timeout),
            max_attempts=d.get("max_attempts", self.default_max_attempts),
            backoff_base=d.get("backoff_base", self.default_backoff_base),
        )

    # ------------------------------------------------------------ execution
    def _execute_dag(self, nodes: list[DagNode], tools: ToolPort) -> dict[str, str]:
        """Kahn 拓扑排序分批：每批里 in-degree 为 0 的节点用线程池并行执行。"""
        by_id = {n.id: n for n in nodes}
        in_degree = {n.id: 0 for n in nodes}
        successors: dict[str, list[str]] = {n.id: [] for n in nodes}
        for n in nodes:
            for dep in n.depends_on:
                if dep in by_id:
                    in_degree[n.id] += 1
                    successors[dep].append(n.id)

        results: dict[str, str] = {}
        ready = [nid for nid, deg in in_degree.items() if deg == 0]
        resolved: set[str] = set()
        while ready:
            wave, ready = ready, []
            with ThreadPoolExecutor(max_workers=min(self.max_workers, len(wave))) as ex:
                futures = {ex.submit(self._run_node, by_id[nid], tools): nid for nid in wave}
                for fut in as_completed(futures):
                    nid = futures[fut]
                    results[nid] = fut.result()
                    resolved.add(nid)
            for nid in wave:
                for succ in successors[nid]:
                    in_degree[succ] -= 1
                    if in_degree[succ] == 0:
                        ready.append(succ)

        for n in nodes:
            if n.id not in resolved:
                results[n.id] = "[dag-error] 检测到循环依赖或前置节点缺失，跳过执行"
        return results

    def _run_node(self, node: DagNode, tools: ToolPort) -> str:
        groups = [node.candidates] if node.candidates else [[{"tool": node.tool, "args": node.args}]]
        groups += [[f] for f in node.fallback]
        last_error = ""
        for group in groups:
            for attempt in range(1, node.max_attempts + 1):
                if len(group) > 1:
                    ok, value = self._race(group, tools, node.timeout)
                else:
                    ok, value = self._call_once(group[0], tools, node.timeout)
                if ok:
                    return value
                last_error = value
                if attempt < node.max_attempts:
                    time.sleep(node.backoff_base * (2 ** (attempt - 1)))
        return f"[harness-exhausted] node={node.id}: {last_error}"

    @staticmethod
    def _call_once(candidate: dict, tools: ToolPort, timeout: float | None) -> tuple[bool, str]:
        try:
            if timeout is not None:
                with ThreadPoolExecutor(max_workers=1) as ex:
                    fut: Future = ex.submit(tools.call, candidate["tool"], candidate.get("args", {}))
                    result: ToolResult = fut.result(timeout=timeout)
            else:
                result = tools.call(candidate["tool"], candidate.get("args", {}))
            return True, result.content
        except FutureTimeoutError:
            return False, f"{candidate['tool']} 超时({timeout}s)"
        except Exception as exc:  # 工具异常不向上抛，转成失败结果参与重试/fallback 判定
            return False, f"{candidate['tool']} 失败: {exc}"

    @staticmethod
    def _race(candidates: list[dict], tools: ToolPort, timeout: float | None) -> tuple[bool, str]:
        with ThreadPoolExecutor(max_workers=len(candidates)) as ex:
            futures = {ex.submit(tools.call, c["tool"], c.get("args", {})): c for c in candidates}
            last_error = ""
            try:
                for fut in as_completed(futures, timeout=timeout):
                    c = futures[fut]
                    try:
                        result: ToolResult = fut.result()
                        return True, result.content
                    except Exception as exc:
                        last_error = f"{c['tool']} 失败: {exc}"
            except FutureTimeoutError:
                return False, f"race 超时({timeout}s)，最后一次错误: {last_error}"
            return False, f"race 全部候选失败，最后一次错误: {last_error}"
