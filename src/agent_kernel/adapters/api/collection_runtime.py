"""Single-process collection workers, owned by the API lifespan."""
from concurrent.futures import ThreadPoolExecutor
from threading import RLock

from ... import module_registry
from ..platform_collectors import COLLECTORS, execute_collection_run
from ..platform_store import PlatformStore
from .scheduler import build_scheduler


class CollectionRuntime:
    def __init__(self, store: PlatformStore, scheduler_enabled: bool = False) -> None:
        self.store = store
        self._lock = RLock()
        self._closed = False
        self.executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="collection")
        self.scheduler = build_scheduler(self._scheduled) if scheduler_enabled else None

    def start(self) -> None:
        if self.scheduler is not None:
            self.scheduler.start(paused=True)
            for rule in self.store.list_automations():
                if rule.get("module_id") and not rule["enabled"]:
                    self.set_enabled(rule["module_id"], False)
            self.scheduler.resume()

    def trigger(self, module_id: str, trigger: str = "manual") -> dict:
        if module_id not in COLLECTORS:
            raise KeyError(module_id)
        with self._lock:
            if self._closed:
                raise RuntimeError("采集服务已关闭")
            connection = self.store.fork()
            try:
                run_id, created = connection.create_collection_run(module_id, trigger)
                run = connection.get_collection_run(run_id)
                if created:
                    try:
                        self.executor.submit(execute_collection_run, self.store, module_id, run_id)
                    except RuntimeError:
                        connection.update_collection_run(run_id, status="failed", error="采集任务未能入队")
                        raise
                return {"run_id": run_id, "module": module_id, "status": run["status"], "reused": not created}
            finally:
                connection.close()

    def _scheduled(self, module: module_registry.ModuleSpec) -> None:
        with self._lock:
            if self._closed:
                return
            connection = self.store.fork()
            try:
                enabled = any(rule.get("module_id") == module.id and rule["enabled"]
                              for rule in connection.list_automations())
            finally:
                connection.close()
            if enabled:
                self.trigger(module.id, "schedule")

    def set_enabled(self, module_id: str, enabled: bool) -> None:
        if self.scheduler is not None:
            if enabled:
                self.scheduler.resume_job(module_id)
            else:
                self.scheduler.pause_job(module_id)

    def toggle_rule(self, automation_id: int) -> bool:
        # Share the scheduler callback lock so disabling has a definite boundary.
        with self._lock:
            connection = self.store.fork()
            try:
                rule = next(r for r in connection.list_automations() if r["id"] == automation_id)
                enabled = not bool(rule["enabled"])
                connection.toggle_automation(automation_id, enabled)
                self.set_enabled(rule["module_id"], enabled)
                return enabled
            finally:
                connection.close()

    def close(self) -> None:
        with self._lock:
            self._closed = True
        if self.scheduler is not None and self.scheduler.running:
            self.scheduler.shutdown(wait=True)
        self.executor.shutdown(wait=True)
