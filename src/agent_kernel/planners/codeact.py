"""CodeAct 策略：模型以"写 Python 代码"的方式解决任务，代码在 Docker 沙箱中执行。

与 smolagents 的 CodeAct 思想一致：不是选工具填参数，而是直接生成代码；
区别是这里把代码执行收敛到唯一的 python_execute 沙箱工具，复用现有内核循环。

实现上只是 ReactPlanner 的一个模板变体：动作协议、解析、上下文、记忆检索全部复用，
仅通过 system_template 扩展点替换指令——零逻辑复制。
"""
from __future__ import annotations

from .react import ReactPlanner

CODEACT_SYSTEM_TMPL = """你是一个用 Python 代码解决问题的助手。可用工具：
{tools}

规则：每轮只输出一个 JSON 对象，不要输出其它任何内容。
- 需要计算或处理数据时，输出 {{"thought": "...", "tool": "python_execute", "args": {{"code": "<Python 代码>"}}}}。
  代码在隔离沙箱中执行（无网络、无文件系统访问），用 print() 输出你需要观察的结果。
- 根据工具返回的 stdout 继续推理；可以作答时输出 {{"thought": "...", "final": "<答案>"}}。
必须完成用户明确要求的全部步骤后才能输出 final，且 final 不得为空；否则继续调用工具。
{memory}"""


class CodeActPlanner(ReactPlanner):
    """ReAct 的 CodeAct 变体：指令模型通过 python_execute 沙箱工具写代码解题。"""

    system_template = CODEACT_SYSTEM_TMPL
