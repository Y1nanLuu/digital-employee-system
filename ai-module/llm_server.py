"""
本地 LLM 服务：把 HuggingFace 缓存里的 Qwen1.5-0.5B-Chat 包装成
OpenAI 兼容的 /v1/chat/completions 接口，供 extractor.py 调用。

用途：
  - 无 GPU / 无外网环境下的真实模型调用（满足"验证模型调用"验收项）
  - 模型来自本机 HF 缓存（~/.cache/huggingface），离线加载，零下载

启动：python llm_server.py   （默认 127.0.0.1:8000）
"""

import os
import sys
import time
import uuid

# 强制离线：只用本地缓存，不访问 HuggingFace Hub
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import torch
from fastapi import FastAPI
from pydantic import BaseModel
from transformers import AutoModelForCausalLM, AutoTokenizer

MODEL_ID = "Qwen/Qwen1.5-0.5B-Chat"   # 已在本机 HF 缓存
HOST, PORT = "127.0.0.1", 8010  # 避开业务后端 8000

t0 = time.time()
print(f"[llm_server] 加载模型 {MODEL_ID}（CPU，离线缓存）...")
tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, local_files_only=True)
model = AutoModelForCausalLM.from_pretrained(
    MODEL_ID, local_files_only=True, torch_dtype=torch.float32)
model.eval()
print(f"[llm_server] 模型就绪，耗时 {time.time()-t0:.1f}s，监听 http://{HOST}:{PORT}/v1")

app = FastAPI(title="local-llm", description="OpenAI 兼容的本地推理服务")


class Message(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    model: str = MODEL_ID
    messages: list[Message]
    temperature: float = 0.0
    max_tokens: int = 512


@app.post("/v1/chat/completions")
def chat(req: ChatRequest):
    t1 = time.time()
    prompt = tokenizer.apply_chat_template(
        [m.model_dump() for m in req.messages],
        tokenize=False, add_generation_prompt=True)
    inputs = tokenizer(prompt, return_tensors="pt")
    greedy = req.temperature <= 0.01
    with torch.no_grad():
        out = model.generate(
            **inputs,
            max_new_tokens=req.max_tokens,
            do_sample=not greedy,
            temperature=max(req.temperature, 1e-5),
            pad_token_id=tokenizer.eos_token_id)
    text = tokenizer.decode(out[0][inputs["input_ids"].shape[1]:],
                            skip_special_tokens=True).strip()
    latency = time.time() - t1
    print(f"[llm_server] 生成 {len(text)} 字符，耗时 {latency:.1f}s")
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": req.model,
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": text}}],
        "usage": {"prompt_tokens": int(inputs["input_ids"].shape[1]),
                  "completion_tokens": len(out[0]) - int(inputs["input_ids"].shape[1]),
                  "total_tokens": len(out[0])},
        "_latency_seconds": round(latency, 2),  # 便于演示时展示耗时
    }


@app.get("/v1/models")
def models():
    return {"data": [{"id": MODEL_ID, "object": "model"}]}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
