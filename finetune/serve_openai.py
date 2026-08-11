"""极简 OpenAI /v1/chat/completions 兼容 server，包住微调后的本地模型。

vLLM 目前不原生支持 Windows（需要 WSL2/Linux），本机是 Windows 直跑场景，所以不用
vLLM——用标准库 `http.server` 起一个最小 OpenAI 兼容端点（跟 `adapters/interop_a2a.py`
的 A2A HTTP 传输层同一个模式：不为了"正确的名字"引一个大依赖，能用标准库就用标准库）。
`agent_kernel.adapters.model.litellm.LiteLLMModel` 对接它零新代码：
    LiteLLMModel("openai/agent-kernel-toolcall", api_base="http://127.0.0.1:8000/v1", api_key="local")
litellm 的 `openai/` provider 前缀 + 自定义 `api_base` 是标准用法，vLLM/llama.cpp/本
server 对 litellm 来说长得一模一样，生产环境想换回 vLLM 只需要换启动命令，不用改
ModelPort 一行代码。

用法：python serve_openai.py --adapter runs/lora-toolcall/adapter --port 8000
"""
from __future__ import annotations

import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

MAX_REQUEST_BYTES = 1_048_576
MAX_NEW_TOKENS = 200


def _make_handler(model, tokenizer, model_name: str):
    import torch

    model.eval()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *a):  # 静默默认访问日志，走 print 自己控制
            pass

        def do_POST(self) -> None:
            if self.path.rstrip("/") != "/v1/chat/completions":
                self.send_response(404)
                self.end_headers()
                return
            length = int(self.headers.get("Content-Length", 0))
            if length <= 0 or length > MAX_REQUEST_BYTES:
                self.send_response(400)
                self.end_headers()
                return
            body = json.loads(self.rfile.read(length))
            messages = body.get("messages", [])

            inputs = tokenizer.apply_chat_template(
                messages, tokenize=True, add_generation_prompt=True, return_tensors="pt"
            )
            with torch.no_grad():
                out = model.generate(
                    input_ids=inputs,
                    max_new_tokens=body.get("max_tokens") or MAX_NEW_TOKENS,
                    do_sample=False,
                    pad_token_id=tokenizer.eos_token_id,
                )
            text = tokenizer.decode(out[0][inputs.shape[1]:], skip_special_tokens=True).strip()

            response = {
                "id": "chatcmpl-local",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model_name,
                "choices": [
                    {"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}
                ],
                "usage": {
                    "prompt_tokens": int(inputs.shape[1]),
                    "completion_tokens": int(out.shape[1] - inputs.shape[1]),
                    "total_tokens": int(out.shape[1]),
                },
            }
            payload = json.dumps(response, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--adapter", default="runs/lora-toolcall/adapter")
    parser.add_argument("--model-name", default="agent-kernel-toolcall")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer

    base_model_name = "Qwen/Qwen2.5-0.5B-Instruct"
    print(f"加载基座模型 {base_model_name} + adapter {args.adapter} ...")
    tokenizer = AutoTokenizer.from_pretrained(args.adapter)
    base_model = AutoModelForCausalLM.from_pretrained(base_model_name)
    model = PeftModel.from_pretrained(base_model, str(args.adapter))

    handler = _make_handler(model, tokenizer, args.model_name)
    server = HTTPServer((args.host, args.port), handler)
    print(f"OpenAI 兼容端点已就绪：http://{args.host}:{args.port}/v1/chat/completions")
    server.serve_forever()


if __name__ == "__main__":
    main()
