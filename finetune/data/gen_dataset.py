"""生成"ReAct 动作 JSON 格式对齐"合成数据集：system prompt 逐字复用
`agent_kernel.planners.react.SYSTEM_TMPL`，工具集合复用 `LocalToolbox` 的
calc/now 演示工具描述，保证训练分布跟 `ReactPlanner` 生产提示词一致
（不是自造一份新格式，微调目标就是"让小模型更会说 ReactPlanner 的话"）。

用法：python data/gen_dataset.py --out train.jsonl --eval-out eval.jsonl
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

FINETUNE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FINETUNE_ROOT.parent / "src"))

from agent_kernel.planners.react import SYSTEM_TMPL  # noqa: E402

TOOL_DESC = (
    "- calc: 计算四则运算表达式; 参数 JSON Schema: "
    '{"type": "object", "properties": {"expression": {"type": "string"}}, "required": ["expression"]}\n'
    "- now: 获取当前日期时间; 参数 JSON Schema: {}"
)
SYSTEM_PROMPT = SYSTEM_TMPL.format(tools=TOOL_DESC, memory="")

CALC_TEMPLATES = [
    ("{a} 加 {b} 等于多少？", "{a}+{b}"),
    ("帮我算一下 {a} 乘以 {b}", "{a}*{b}"),
    ("{a} 减去 {b} 是多少", "{a}-{b}"),
    ("计算 {a} 除以 {b}", "{a}/{b}"),
    ("({a} + {b}) * 2 等于几", "({a}+{b})*2"),
]
NOW_QUERIES = [
    "现在几点了？",
    "今天是什么日期？",
    "告诉我当前时间。",
    "现在的时间是？",
]
FINAL_QA_TRAIN = [
    ("中国的首都是哪里？", "北京"),
    ("Python 是什么类型的语言？", "一种解释型、动态类型的高级编程语言"),
    ("一年有多少个月？", "12 个月"),
    ("水的化学式是什么？", "H2O"),
    ("太阳系有几大行星？", "8 大行星"),
    ("你好，你是谁？", "你好，我是一个会使用工具的助手，有什么可以帮你？"),
]
# eval 用不同问题，避免跟训练集重复（calc/now 类别靠随机数天然不重复，final 类别问题
# 数量少、必须显式拆分，否则"评测"会退化成"背过的原题"）。
FINAL_QA_EVAL = [
    ("法国的首都是哪里？", "巴黎"),
    ("一周有几天？", "7 天"),
    ("日本的首都是哪里？", "东京"),
    ("一小时有多少分钟？", "60 分钟"),
]


def _example(user: str, assistant_obj: dict) -> dict:
    return {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user},
            {"role": "assistant", "content": json.dumps(assistant_obj, ensure_ascii=False)},
        ]
    }


def build_examples(rng: random.Random, n_calc: int, n_now: int, final_qa: list[tuple[str, str]]) -> list[dict]:
    examples: list[dict] = []
    for _ in range(n_calc):
        phrase, expr_tmpl = rng.choice(CALC_TEMPLATES)
        a, b = rng.randint(1, 99), rng.randint(1, 99)
        user = phrase.format(a=a, b=b)
        expr = expr_tmpl.format(a=a, b=b)
        examples.append(
            _example(user, {"thought": f"需要计算 {expr}", "tool": "calc", "args": {"expression": expr}})
        )
    for _ in range(n_now):
        user = rng.choice(NOW_QUERIES)
        examples.append(_example(user, {"thought": "需要查询当前时间", "tool": "now", "args": {}}))
    for user, answer in final_qa:
        examples.append(_example(user, {"thought": "可以直接回答", "final": answer}))
    rng.shuffle(examples)
    return examples


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=FINETUNE_ROOT / "data" / "train.jsonl")
    parser.add_argument("--eval-out", type=Path, default=FINETUNE_ROOT / "data" / "eval.jsonl")
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    rng = random.Random(args.seed)
    train = build_examples(rng, n_calc=48, n_now=16, final_qa=FINAL_QA_TRAIN)
    eval_ = build_examples(random.Random(args.seed + 1), n_calc=16, n_now=8, final_qa=FINAL_QA_EVAL)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as f:
        for ex in train:
            f.write(json.dumps(ex, ensure_ascii=False) + "\n")
    with args.eval_out.open("w", encoding="utf-8") as f:
        for ex in eval_:
            f.write(json.dumps(ex, ensure_ascii=False) + "\n")
    print(f"train: {len(train)} -> {args.out}")
    print(f"eval: {len(eval_)} -> {args.eval_out}")


if __name__ == "__main__":
    main()
