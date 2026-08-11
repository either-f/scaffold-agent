# ADR-0012: Daytona 云沙箱 adapter，验证沙箱后端可否零改动替换

日期: 2026-08-10  状态: 已采纳（真实账号验证未完成，见坦诚声明）

## 决策

- 新增 `DaytonaSandbox`（`adapters/sandbox_daytona.py`），跟 `DockerSandbox` 同签名
  （`execute(code: str) -> str`），`SandboxToolbox` 无需改动即可换后端——两个类互不知道
  对方存在，靠鸭子类型对齐，`ports.py` 里从来就没有正式的 `SandboxExecutor` 抽象基类
  （沙箱不是六端口之一，纯粹是 `SandboxToolbox` 构造时接受的一个鸭子类型契约）。
- 补一个 `SandboxRunner(Protocol)`（`sandbox_docker.py`）给这份既有的鸭子类型契约起个
  名字，`SandboxToolbox.__init__` 的类型标注从写死的 `DockerSandbox | None` 放宽到
  `SandboxRunner | None`——只是类型标注放宽，不是新增抽象层，不产生任何运行时行为变化。
- 生命周期语义对齐 `DockerSandbox` 的 `docker run --rm`：`execute()` 每次调用创建一个
  `auto_delete_interval=0` 的 ephemeral 沙箱、跑一次代码、`finally` 里删除，调用之间不
  残留状态，跟 `DockerSandbox` 的"一次性容器"心智模型一致（放弃 Daytona 支持的常驻
  沙箱复用带来的冷启动优化，换取两个 adapter 行为对齐、可互换）。
- `client_factory` 可注入（对齐 `DockerSandbox` 的 `runner` 注入点），默认真实构造
  `daytona.Daytona(DaytonaConfig(api_key=...))`；单测传假 client 工厂验证控制流
  （错误映射、ephemeral 参数、截断、异常路径下仍尝试删除），见 `tests/test_sandbox_daytona.py`。
- 依赖声明为可选 extra `daytona = ["daytona>=0.203,<1"]`（0.x 版本号，未到 1.0，pin 到
  `<1` 而非追更细的 minor，对齐项目里其它 0.x 依赖如 `flashrank` 的 pin 方式）。

## 原因

M4 已有 `DockerSandbox`（本地 Docker，8 项隔离验证过）。Daytona（GitHub 7.2 万星，
当前沙箱类目最热，托管弹性沙箱）能不能作为第二个沙箱后端接入、验证"新沙箱概念接入不改
`SandboxToolbox`/`CodeActPlanner`/内核任何一行"，是这轮通用脚手架扩展里检验 M0 融合纪律
（"新概念出现时，接入方式永远是加插件"）最直接的一项——也是市面沙箱产品里辨识度最高的选择。

## 后果与迁移条件（坦诚声明）

- **未验证真实 Daytona 基础设施保证**：跟 M4 Docker 沙箱当初"先离线验证命令构造、再补
  真实容器验证"的路径一样，本次没有 Daytona 账号/API key，只做到了"控制流单测通过"这一
  层——create→code_run→delete 的调用序列、错误映射、ephemeral 参数是对的，但没有像
  `evals/baseline-m4-sandbox-real.json` 那样对真实基础设施做逐项验证（网络出站策略、
  超售隔离边界、真实截断行为、真实延迟）。等有可用账号时应补一份
  `evals/run_sandbox_daytona_real.py` + baseline JSON，对齐 M4 real 验证的证据标准。
- 隔离边界跟 `DockerSandbox` 不对等：`DockerSandbox` 显式声明 `--network none`/
  `--cap-drop ALL`/cgroup 限额，隔离保证写在本仓库代码里、可审计；`DaytonaSandbox` 的
  隔离保证在 Daytona 平台侧，本 adapter 不重新声明这些参数，信任边界从"我方代码"移到
  "第三方托管服务"，这是选择托管沙箱必然要接受的取舍，不是本次实现的缺陷。
- 一次性生命周期语义放弃了 Daytona 原生支持的沙箱复用/预热池（Warm Pools）带来的低延迟
  优势；如果真要用于生产、对延迟敏感，应改造成沙箱池化复用，不再是本次"跟 DockerSandbox
  行为对齐"的最简单接入路径，留作后续按需推进。
- 内核对本次改动零感知：只新增一个 adapter 文件、一个 Protocol 类型标注、一个 extra、
  一份单测，未触碰 `kernel.py` / `ports.py` / `planners/codeact.py`。
