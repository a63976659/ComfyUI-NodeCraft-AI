import aiohttp
import sys
import asyncio
from pathlib import Path

_plugin_root = str(Path(__file__).parent.parent.resolve())
if _plugin_root not in sys.path:
    sys.path.insert(0, _plugin_root)
from 后端.文件读写操作 import load_settings


class AICoderClient:
    """LLM API 客户端 - 支持 OpenAI 兼容接口"""

    async def generate_response(self, system_prompt, messages_history):
        """
        调用 LLM 生成回复

        Args:
            system_prompt: 系统提示词（含知识库规范）
            messages_history: 压缩后的对话历史 [{"role": "user/assistant", "content": "..."}]

        Returns:
            str: AI 回复文本
        """
        # 每次调用时读取最新设置
        settings = load_settings()

        base_url = settings.get("base_url", "https://api.openai.com/v1")
        model_name = settings.get("model_name", "qwen2.5-coder-32b-instruct")
        api_key = settings.get("api_key", "")
        temperature = settings.get("temperature", 0.2)
        max_tokens = settings.get("max_tokens", 4096)

        # 构造请求
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        payload = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                *messages_history
            ],
            "temperature": temperature,
            "max_tokens": max_tokens
        }

        # 异步 HTTP 调用
        url = f"{base_url.rstrip('/')}/chat/completions"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=120)) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        return result["choices"][0]["message"]["content"]
                    else:
                        error_text = await resp.text()
                        return f"[API 错误 {resp.status}]: {error_text[:500]}"
        except aiohttp.ClientError as e:
            return f"[连接错误]: {str(e)}"
        except Exception as e:
            return f"[未知错误]: {str(e)}"


class LocalModelClient:
    """本地模型客户端，使用 transformers 加载"""

    def __init__(self):
        self.模型实例 = None
        self.分词器 = None
        self.当前模型名 = None

    def 加载模型(self, 模型路径: str):
        """加载本地模型到 GPU"""
        from transformers import AutoModelForCausalLM, AutoTokenizer

        print(f"[AI Coder] 正在加载本地模型: {模型路径}")

        self.分词器 = AutoTokenizer.from_pretrained(模型路径, trust_remote_code=True)
        self.模型实例 = AutoModelForCausalLM.from_pretrained(
            模型路径,
            device_map="auto",
            torch_dtype="auto",
            trust_remote_code=True,
            low_cpu_mem_usage=True
        )
        self.当前模型名 = Path(模型路径).name
        print(f"[AI Coder] 本地模型加载完成: {self.当前模型名}")

    def 卸载模型(self):
        """卸载当前模型释放显存"""
        if self.模型实例 is not None:
            del self.模型实例
            del self.分词器
            self.模型实例 = None
            self.分词器 = None
            self.当前模型名 = None
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except ImportError:
                pass
            print("[AI Coder] 本地模型已卸载")

    async def generate_response(self, system_prompt, messages_history):
        """本地推理生成回复（在线程池中执行避免阻塞）"""
        import torch

        if self.模型实例 is None or self.分词器 is None:
            return "[错误]: 本地模型未加载，请先选择并加载一个本地模型"

        # 构建对话格式
        formatted_messages = [{"role": "system", "content": system_prompt}]
        for msg in messages_history:
            formatted_messages.append({"role": msg["role"], "content": msg["content"]})

        # 使用 tokenizer 的 chat template
        text = self.分词器.apply_chat_template(
            formatted_messages, tokenize=False, add_generation_prompt=True
        )
        inputs = self.分词器(text, return_tensors="pt").to(self.模型实例.device)

        # 推理（在线程池中执行避免阻塞）
        loop = asyncio.get_running_loop()

        def _generate():
            with torch.no_grad():
                outputs = self.模型实例.generate(
                    **inputs,
                    max_new_tokens=4096,
                    temperature=0.7,
                    top_p=0.9,
                    repetition_penalty=1.1,
                    do_sample=True
                )
            # 只取新生成的部分
            new_tokens = outputs[0][inputs["input_ids"].shape[1]:]
            return self.分词器.decode(new_tokens, skip_special_tokens=True)

        try:
            result = await loop.run_in_executor(None, _generate)
            return result
        except Exception as e:
            return f"[本地推理错误]: {str(e)}"
