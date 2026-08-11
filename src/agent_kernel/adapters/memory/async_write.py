"""AsyncMemory：add() 不阻塞调用方——扔进线程池异步执行，search() 保持同步
（检索结果必须等，没法异步）。

对齐 FoxChat"后处理四路并行"的效果，但落地方式不一样：agent-kernel 的
kernel.py::_finish_tool 目前顺序调 state.add() → memory.add() → emit 事件，
如果 memory.add() 涉及 embedding/网络请求会拖慢主循环。包一层 AsyncMemory
就能让主循环立即往下走，不用改 kernel.py 一个字——新概念＝新 adapter，这是
项目一贯的融合纪律（FoxChat 是在 LangGraph 节点图里天然支持并行分支，
agent-kernel 的 kernel.py 是单线程顺序循环，没有对应的节点图概念，所以用
adapter 层异步化去逼近同样的效果，而不是在 kernel.py 里引入并行控制流）。

ponytail: fire-and-forget 意味着 add() 返回时写入不一定已经完成——kernel.py
现有的"memory.add() 之后才 checkpoint"这个顺序，语义从"写入成功才checkpoint"
弱化成"提交成功才checkpoint"。可接受：episodic 消息本来就在 checkpoint 的
RunState.messages 里有全量兜底，离线巩固脚本从 checkpoint 读、不依赖这层
add() 是否完成。真正要求"memory.add 落盘保证"且没有其它兜底的场景不该用
这层。异常不重试、不进 EffectLedger（那是给外部有副作用的工具调用用的，
这里只是内部记忆写入，重试策略由 inner MemoryPort 自己决定）。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from ...ports import MemoryPort

OnError = Callable[[str, str, str, Exception], None]


class AsyncMemory(MemoryPort):
    def __init__(self, inner: MemoryPort, max_workers: int = 2, on_error: OnError | None = None) -> None:
        self.inner = inner
        self.max_workers = max_workers
        self.on_error = on_error
        self._executor = ThreadPoolExecutor(max_workers=max_workers)

    def add(self, run_id: str, role: str, content: str) -> None:
        future = self._executor.submit(self.inner.add, run_id, role, content)
        future.add_done_callback(lambda f: self._report_error(f, run_id, role, content))

    def _report_error(self, future, run_id: str, role: str, content: str) -> None:
        exc = future.exception()
        if exc is not None and self.on_error:
            self.on_error(run_id, role, content, exc)

    def search(self, query: str, k: int = 5) -> list[str]:
        return self.inner.search(query, k=k)

    def flush(self) -> None:
        """阻塞直到所有已提交的 add() 完成——测试/优雅关闭时用，正常主循环路径
        不该调（调了就失去"不阻塞"的意义）。"""
        self._executor.shutdown(wait=True)
        self._executor = ThreadPoolExecutor(max_workers=self.max_workers)
