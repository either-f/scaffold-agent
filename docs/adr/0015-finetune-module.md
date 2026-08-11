# ADR-0015: 微调模块落地为独立流水线，本次会话未跑出完整训练结果

日期: 2026-08-11  状态: 已采纳（训练未完整验证，见坦诚声明）

## 决策

- 新增 `finetune/` 独立目录，跟 `src/agent_kernel/` 完全分开：独立虚拟环境
  （`finetune/.venv`，Python 3.11，跟主环境 3.14 分开），独立 `requirements.txt`，
  不进 `pyproject.toml`，不参与内核第三方 import 检查——落实 PLAN.md 非目标条款
  "训模型限定为 M11 独立模块，不进内核、不参与内核零依赖 CI 检查"。
- 微调目标选"让 0.5B 小模型更稳定输出 `ReactPlanner` 的动作 JSON 格式"（不是通用
  问答微调）：`finetune/data/gen_dataset.py` 直接复用
  `agent_kernel.planners.react.SYSTEM_TMPL`，保证训练分布跟生产提示词一致；
  train/eval 按类别（calc/now/final-QA）分别用不同随机种子/不同题目生成，避免
  eval 跟 train 重复（尤其 final-QA 类别问题数量少，最初实现有泄漏，已修正为
  显式拆分成 `FINAL_QA_TRAIN`/`FINAL_QA_EVAL` 两个不相交列表）。
- 基座模型：`Qwen/Qwen2.5-0.5B-Instruct`。训练栈：`transformers` + `peft`
  （`LoraConfig` 直接传给 `SFTTrainer(peft_config=...)`，不手动
  `get_peft_model()`）+ `trl.SFTTrainer`。评测：`finetune/eval_tool_format.py`
  直接复用 `ReactPlanner._parse`（生产解析器本身）判定生成结果是否为合法动作
  JSON、是否跟标注一致，不重新实现一份近似解析逻辑。
- 原计划用 Unsloth 加速 + vLLM 生产 serving，两者都需要真实 CUDA torch；本机有
  RTX 4050（驱动 CUDA 12.7），但 `download.pytorch.org` 的 cu124 轮子（2.4GB）
  这次会话三次尝试全部失败/卡死（TLS 连接重置或下载无进展超过 10 分钟），换成
  PyPI 默认索引的 CPU 轮子才稳定装上——`unsloth` 导入时硬性要求 GPU
  （`unsloth_zoo.device_type.get_device_type()` 无 accelerator 直接抛
  `NotImplementedError`），因此弃用 unsloth，训练脚本改用纯
  `transformers`/`peft`/`trl`（CPU/GPU 都能跑，没有 unsloth 的强 GPU 假设）；
  serving 弃用 vLLM（不原生支持 Windows），改成标准库 `http.server` 实现最小
  OpenAI `/v1/chat/completions` 兼容端点（`finetune/serve_openai.py`），跟
  `adapters/interop_a2a.py` 的 A2A HTTP 传输层同一模式。`LiteLLMModel` 对接它
  零新代码：`LiteLLMModel("openai/<name>", api_base="http://127.0.0.1:PORT/v1")`。

## 原因（诊断过程，非猜测）

切到 CPU 后跑通了训练循环（模型加载、LoRA 包装、tokenizer、trainer 构造、进入
训练循环并算完第 1 步），但第 1 步耗时 3883 秒，27 步换算约 28 小时，明显不对。
逐层排查（而非猜测）：
1. 隔离测试单条 12 token 样本的裸 forward/backward（不经过 `SFTTrainer`）：
   forward 0.3s，**backward 35–40s**——已经排除 SFTTrainer/data collator/padding
   本身导致，backward 本身就异常慢。
2. `torch.__config__.show()` 确认 MKL/AVX2 正常启用；纯 `2048x2048` 矩阵乘法
   基准测试（10 次仅 0.29s）证明 BLAS 层没问题。
3. 排除线程竞争：`torch.set_num_threads(1)` 下 backward 仍是 35s 左右（forward
   反而变慢到 2s），说明不是多线程调度开销。
4. 排除是 HF loss 计算：改成 `logits.sum().backward()`（绕开 `CrossEntropyLoss`）
   仍然 42s。排除 attention 实现：`attn_implementation="eager"` 仍然 38s。
5. 对照测试：同参数量级的纯 24 层 MLP，同尺寸输入，forward/backward 都在
   0.02s——证明不是"这台机器 backward 普遍慢"，是 Qwen2.5 这类 transformer
   （RoPE + GQA attention）在这台机器上的 backward 路径有问题。
6. 换 `torch==2.5.1`（排除是 2.11.0 的版本回归）问题依旧；升级过程中还顺带
   发现并修复一个真实环境问题：`unsloth` 装的 `torchvision==0.26.0` 跟
   `torch==2.5.1` ABI 不兼容，`torchvision::nms` 算子注册失败导致
   `transformers` 连模型类都导入不了（`ModuleNotFoundError`），卸载
   `torchvision`/`torchao` 后恢复正常。
7. 用真实训练样本（213 token，非人工构造的极短样本）重测：forward 87s，
   backward 485s——随长度**非线性**恶化（12→213 token，长度增 17.75 倍，
   forward 耗时增 290 倍），且比值不稳定，符合"CPU 在持续重负载下热降频"的
   特征（笔记本 RTX 4050 机型，独显散热预算下 CPU 持续高负载容易触发降频），
   而不是纯算法复杂度问题。

排查到步骤 7 时已经能确定：这是**这台机器这次会话的物理/环境限制**（CPU 在
Qwen2.5 架构负载下的可持续吞吐异常低，且 CUDA 轮子这次会话下不动），不是
`finetune/` 代码里能再修的配置问题——继续排查需要能控制机器散热/电源策略或换一
台机器，超出本次会话能做的范围。

## 后果与迁移条件（坦诚声明）

- **训练未完整跑通**：`finetune/train_lora.py` 在真实环境里验证到"进入训练循环
  并完成前几步"这一层（模型加载、LoRA 包装、数据管线、trainer 构造全部真实
  跑过），但没有在本次会话里跑完全部 27 步产出一个可用 adapter，因此
  `eval_tool_format.py` 的微调前后对比、`serve_openai.py` 的真实 serving 验证
  都没有真实数据——这两个脚本本身逻辑完整、参数正确（`--help` 冒烟测试通过，
  `ReactPlanner._parse` 复用路径读代码可确认正确），但缺少端到端真实运行证据。
- **不是设计缺陷**：`gen_dataset.py` 已真实生成 70/28 条 train/eval 数据；
  模型选型、LoRA 配置、SFTTrainer 接线方式全部经过真实 API 核对（`processing_class`
  取代旧版 `tokenizer` 参数、`max_length` 取代 `max_seq_length`、
  `peft_config` 直传 `SFTTrainer` 等，都是读了本机实际安装版本的函数签名后
  写的，不是照旧文档抄的）。换一台散热正常/GPU 可用的机器，`uv pip install
  torch --index-url https://download.pytorch.org/whl/cu124` 装成功后原样运行
  `train_lora.py` 预期可在数分钟内跑完。
- 迁移条件：拿到能用的 CUDA 环境后，(1) 重新跑 `train_lora.py` 产出真实
  adapter，(2) 跑 `eval_tool_format.py` 产出真实前后对比，写入
  `evals/baseline-m11-finetune-real.json`，(3) 用 `serve_openai.py` 起服务，
  拿 `LiteLLMModel` 真实调用一次验证零新代码对接，把这条坦诚声明改成"已解决"。
- 内核零改动：`finetune/` 完全独立，`src/agent_kernel/` 未改一行；唯一跨目录
  依赖是 `finetune/` 的脚本 `sys.path.insert` 读取 `agent_kernel.planners.react`
  的 `SYSTEM_TMPL`/`ReactPlanner._parse`（只读，不反向依赖）。
