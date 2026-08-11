"""LoRA SFT：让一个 0.5B 小模型更稳定地输出 `ReactPlanner` 的动作 JSON 格式。

用法：python train_lora.py --train data/train.jsonl --out runs/lora-toolcall

坦诚声明：本机有 RTX 4050（CUDA 12.7 驱动），但 `download.pytorch.org` 的
cu124 torch 轮子（2.4GB）这次会话里连续三次下载失败/卡死（TLS 连接被重置、
或干脆无进展），换成 PyPI 默认索引的 CPU 版 torch 才稳定装上；因此本次训练
走 CPU，不是不能用 GPU，是这次会话下不动那个轮子。改用 GPU 只需要
`pip install torch --index-url https://download.pytorch.org/whl/cu124` 装成功后
把本文件换回 unsloth（更快），或者给 `AutoModelForCausalLM.from_pretrained`
加 `device_map="cuda"`即可，训练逻辑本身不用改。
"""
from __future__ import annotations

import argparse
from pathlib import Path

BASE_MODEL = "Qwen/Qwen2.5-0.5B-Instruct"
MAX_LENGTH = 1024


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--train", type=Path, default=Path(__file__).parent / "data" / "train.jsonl")
    parser.add_argument("--out", type=Path, default=Path(__file__).parent / "runs" / "lora-toolcall")
    parser.add_argument("--epochs", type=int, default=3)
    args = parser.parse_args()

    from datasets import load_dataset
    from peft import LoraConfig
    from transformers import AutoModelForCausalLM, AutoTokenizer
    from trl import SFTConfig, SFTTrainer

    tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL)
    model = AutoModelForCausalLM.from_pretrained(BASE_MODEL)

    dataset = load_dataset("json", data_files=str(args.train), split="train")
    dataset = dataset.map(
        lambda ex: {"text": tokenizer.apply_chat_template(ex["messages"], tokenize=False, add_generation_prompt=False)}
    )

    peft_config = LoraConfig(
        r=16,
        lora_alpha=16,
        lora_dropout=0.0,
        bias="none",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
        task_type="CAUSAL_LM",
    )

    trainer = SFTTrainer(
        model=model,
        processing_class=tokenizer,
        train_dataset=dataset,
        peft_config=peft_config,
        args=SFTConfig(
            dataset_text_field="text",
            max_length=MAX_LENGTH,
            per_device_train_batch_size=4,
            gradient_accumulation_steps=2,
            warmup_steps=5,
            num_train_epochs=args.epochs,
            learning_rate=2e-4,
            logging_steps=1,
            optim="adamw_torch",
            output_dir=str(args.out / "trainer_state"),
            report_to="none",
            seed=3407,
            use_cpu=True,
        ),
    )
    result = trainer.train()
    print(f"训练完成，train_loss={result.training_loss:.4f}")

    adapter_dir = args.out / "adapter"
    trainer.model.save_pretrained(str(adapter_dir))
    tokenizer.save_pretrained(str(adapter_dir))
    print(f"LoRA adapter 已保存到 {adapter_dir}")


if __name__ == "__main__":
    main()
