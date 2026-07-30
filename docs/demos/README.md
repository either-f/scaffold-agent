# Demo 录制与回放

## 录制

```powershell
.venv\Scripts\python.exe examples\record_demos.py
```

生成三个 `.cast` 文件：
- `research.cast` — 委托式研究助手（技能发现 + 正文加载 + 多步工具调用）
- `files.cast` — 安全文件整理与技能发现（动态技能 + 工具委托）
- `ops.cast` — HITL 审批 + 沙箱运算（CodeAct + Docker 沙箱 CLI 审批）

## 回放

使用本机 recorder（线性回放）：

```powershell
.venv\Scripts\python.exe examples\record_demos.py --play research
.venv\Scripts\python.exe examples\record_demos.py --play files
.venv\Scripts\python.exe examples\record_demos.py --play ops
```

或使用 [asciinema player](https://asciinema.org)（推荐）：

```powershell
# 安装
pip install asciinema

# 本地播放
asciinema play docs/demos/research.cast
asciinema play docs/demos/files.cast
asciinema play docs/demos/ops.cast
```

## CI 离线验证

```powershell
.venv\Scripts\python.exe examples\record_demos.py --check
```

仅运行三个 demo 并检查 exit code，不写入 `.cast` 文件。无密钥、无网络依赖。
