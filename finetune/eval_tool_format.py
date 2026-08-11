"""微调前后对比：基座模型 vs LoRA 微调后模型，在 eval.jsonl 上测"动作 JSON 格式"
合法率——直接复用 `agent_kernel.planners.react.ReactPlanner._parse`（生产解析器本身，
不是重新实现一份近似逻辑），成功解析且跟标注的 tool/final 一致才算通过。

用法：python eval_tool_format.py --adapter runs/lora-toolcall/adapter \
    --output ../evals/baseline-m11-finetune-real.json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

FINETUNE_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(FINETUNE_ROOT.parent / "src"))

from agent_kernel.planners.react import ActionParseError, ReactPlanner  # noqa: E402
from agent_kernel.types import FinalAnswer, ToolCall  # noqa: E402

BASE_MODEL = "Qwen/Qwen2.5-0.5B-Instruct"
MAX_LENGTH = 1024


def load_eval_set(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def generate(model, tokenizer, messages: list[dict], max_new_tokens: int = 120) -> str:
    import torch

    model.eval()
    prompt_messages = messages[:2]  # system + user，不把标注答案喂给模型
    inputs = tokenizer.apply_chat_template(
        prompt_messages, tokenize=True, add_generation_prompt=True, return_tensors="pt"
    )
    with torch.no_grad():
        out = model.generate(
            input_ids=inputs,
            max_new_tokens=max_new_tokens,
            do_sample=False,
            pad_token_id=tokenizer.eos_token_id,
        )
    text = tokenizer.decode(out[0][inputs.shape[1]:], skip_special_tokens=True)
    return text.strip()


def score(examples: list[dict], generations: list[str]) -> dict:
    parseable = 0
    correct = 0
    details = []
    for ex, text in zip(examples, generations):
        expected = json.loads(ex["messages"][2]["content"])
        row = {"input": ex["messages"][1]["content"], "raw_output": text}
        try:
            action = ReactPlanner._parse(text)
            parseable += 1
            row["parsed"] = True
            if "tool" in expected:
                ok = isinstance(action, ToolCall) and action.name == expected["tool"] and (
                    expected["tool"] != "calc"
                    or action.args.get("expression") == expected["args"]["expression"]
                )
            else:
                ok = isinstance(action, FinalAnswer) and expected["final"] in action.content
            row["correct"] = ok
            correct += int(ok)
        except ActionParseError:
            row["parsed"] = False
            row["correct"] = False
        details.append(row)
    n = len(examples)
    return {
        "n": n,
        "parseable_rate": parseable / n if n else 0.0,
        "correct_rate": correct / n if n else 0.0,
        "details": details,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--eval-file", type=Path, default=FINETUNE_ROOT / "data" / "eval.jsonl")
    parser.add_argument("--adapter", type=Path, default=FINETUNE_ROOT / "runs" / "lora-toolcall" / "adapter")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer

    examples = load_eval_set(args.eval_file)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "suite": "finetune_tool_format_real",
        "base_model": BASE_MODEL,
        "device": "cpu",
        "n_eval": len(examples),
    }

    print(f"加载基座模型 {BASE_MODEL} ...")
    tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL)
    base_model = AutoModelForCausalLM.from_pretrained(BASE_MODEL)
    base_generations = [generate(base_model, tokenizer, ex["messages"]) for ex in examples]
    report["before"] = score(examples, base_generations)
    print(f"微调前: parseable={report['before']['parseable_rate']:.2%} correct={report['before']['correct_rate']:.2%}")

    print(f"加载 LoRA adapter {args.adapter} ...")
    ft_model = PeftModel.from_pretrained(base_model, str(args.adapter))
    ft_generations = [generate(ft_model, tokenizer, ex["messages"]) for ex in examples]
    report["after"] = score(examples, ft_generations)
    print(f"微调后: parseable={report['after']['parseable_rate']:.2%} correct={report['after']['correct_rate']:.2%}")

    report["ok"] = report["after"]["correct_rate"] > report["before"]["correct_rate"]

    print(json.dumps({k: v for k, v in report.items() if k not in ("before", "after")}, ensure_ascii=False, indent=2))
    if args.output:
        output = args.output if args.output.is_absolute() else FINETUNE_ROOT / args.output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"结果已写入 {output}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
