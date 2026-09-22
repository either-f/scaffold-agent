"""定时调度：到点调用一个外部传入的 callback，不关心 callback 内部怎么跑 AgentKernel。

`schedule` 字段（cron 表达式）来自 module_registry.MODULES，加新模块不用碰这个文件。
"""
from __future__ import annotations

from typing import Callable
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from ... import module_registry

ModuleCallback = Callable[[module_registry.ModuleSpec], None]


def build_scheduler(callback: ModuleCallback) -> BackgroundScheduler:
    """按 module_registry.MODULES 的 cron 表达式注册定时任务，调用方 start() 后生效。"""
    timezone = ZoneInfo("Asia/Shanghai")
    scheduler = BackgroundScheduler(timezone=timezone)
    for module in module_registry.MODULES:
        scheduler.add_job(
            callback,
            CronTrigger.from_crontab(module.schedule, timezone=timezone),
            args=[module],
            id=module.id,
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=3600,
        )
    return scheduler


def trigger_now(module_id: str, callback: ModuleCallback) -> None:
    """兼容外部调用者的同步 callback 辅助；平台 HTTP 使用 collection_runtime。"""
    callback(module_registry.get(module_id))
