# Demo 录制与回放

离线确定性演示，无密钥、无网络、无 Docker daemon 依赖。

## 三个演示

| Demo | 内容 | 真实代码路径 |
|------|------|-------------|
| `research` | 委托式研究助手 | WorkerDelegationPort + SkillToolbox + DirSkillLoader |
| `files` | 安全文件审查 | 真实 pathlib 目录遍历工具 + 渐进式技能披露 |
| `ops` | HITL 审批 + 沙箱策略验证 | DockerSandbox 命令安全控制验证（离线干跑，不启动容器） |

所有 demo 使用 FakeScriptedModel 提供确定性模型输出；真模型链路见 `examples/run_demo.py --m1`。

## 录制

```powershell
.venv\Scripts\python.exe examples\record_demos.py
```

生成确定性 `.cast` 文件（固定 80×24、单调 0.1s 事件偏移、无 timestamp、\n 行尾）：
- `research.cast` — 委托式研究助手
- `files.cast` — 安全文件审查
- `ops.cast` — HITL 审批 + 沙箱策略验证

## 回放

本机线性回放（使用相对事件偏移，不依赖 header timestamp）：

```powershell
.venv\Scripts\python.exe examples\record_demos.py --play research
.venv\Scripts\python.exe examples\record_demos.py --play files
.venv\Scripts\python.exe examples\record_demos.py --play ops
```

或使用 [asciinema player](https://asciinema.org)：

```powershell
pip install asciinema
asciinema play docs/demos/research.cast
asciinema play docs/demos/files.cast
asciinema play docs/demos/ops.cast
```

## CI 确定性验证

```powershell
.venv\Scripts\python.exe examples\record_demos.py --check
```

运行所有 demo，生成确定性 asciicast v2 字节，解析已跟踪的 `.cast` 文件，验证 header/事件形状，逐字节比对。不写入文件。

## 坦诚声明

- ops demo 使用 DockerSandbox 构建安全命令并验证参数（--network none, --read-only, --cap-drop ALL, --security-opt no-new-privileges），但注入 fake runner 返回模拟输出——本机无 Docker daemon 时不启动真实容器。
- research demo 的 worker 委派为真实进程内 AgentKernel 调用，但模型为 FakeScriptedModel，不是真实 LLM。
- 录制为确定性格式；真模型运行输出因网络延迟/温度等不可重现，不在确定性录制范围内。
