# LLM 模型使用方案

## 来源
参考工具/项目：ComfyUI-Artificial-Intelligence（https://github.com/a63976659/ComfyUI-Artificial-Intelligence）
定位：在 ComfyUI 节点中集成本地 LLM 对话能力（Qwen 系列）

## 适用场景
- ComfyUI 节点需要 LLM 文本生成能力
- 需要本地离线运行的 AI 对话/翻译/创作节点
- 需要角色预设和可控生成参数的场景

## 核心思路
使用 transformers 库加载本地模型，通过 `apply_chat_template` 构造对话格式，配合温度、Top-P 等采样参数控制生成质量，以全局字典缓存已加载模型避免重复加载。

## 实现步骤

### 1. 模型加载
```python
from transformers import AutoModelForCausalLM, AutoTokenizer

tokenizer = AutoTokenizer.from_pretrained(model_path, trust_remote_code=True)
model = AutoModelForCausalLM.from_pretrained(
    model_path,
    device_map="auto",       # 自动分配 GPU/CPU
    torch_dtype="auto",      # 自动推断精度
    trust_remote_code=True   # 支持自定义代码
)
```

### 2. 对话格式构造
```python
messages = [
    {"role": "system", "content": system_prompt},
    {"role": "user", "content": user_input}
]

text_input = tokenizer.apply_chat_template(
    messages,
    tokenize=False,
    add_generation_prompt=True
)

model_inputs = tokenizer([text_input], return_tensors="pt").to(model.device)
```

### 3. 生成与参数控制
```python
generated_ids = model.generate(
    model_inputs.input_ids,
    max_new_tokens=2048,
    pad_token_id=tokenizer.eos_token_id,
    do_sample=True,
    temperature=0.7,    # 0.1-2.0，越高越有创意
    top_p=0.9           # 核采样概率阈值
)

# 截取生成部分（去掉输入）
generated_ids = [
    output_ids[len(input_ids):]
    for input_ids, output_ids in zip(model_inputs.input_ids, generated_ids)
]

response = tokenizer.batch_decode(generated_ids, skip_special_tokens=True)[0]
```

### 4. 可复现性（种子控制）
```python
import torch

torch.manual_seed(seed)
if torch.cuda.is_available():
    torch.cuda.manual_seed_all(seed)
```

### 5. 角色预设系统
```python
PRESETS = {
    "通用助手": "你是一个智能助手，请简洁准确地回答问题。",
    "创意作家": "你是一位富有创造力的作家...",
    "代码专家": "你是一位编程专家...",
}

system_prompt = PRESETS.get(preset_name, custom_prompt)
```

## ComfyUI 适配要点

1. **节点输入设计**：温度、Top-P、最大长度作为可调参数暴露
```python
"required": {
    "温度": ("FLOAT", {"default": 0.7, "min": 0.1, "max": 2.0, "step": 0.1}),
    "top_p": ("FLOAT", {"default": 0.9, "min": 0.1, "max": 1.0, "step": 0.05}),
    "最大长度": ("INT", {"default": 2048, "min": 64, "max": 8192, "step": 64}),
}
```

2. **输出为 STRING 类型**：可连接到其他文本处理节点
3. **全局模型缓存**：同一工作流中多次调用不会重复加载

## 注意事项

1. **显存占用**：7B 模型约需 14GB 显存，量化后约 4-8GB
2. **首次加载慢**：模型加载需要 10-30 秒（取决于模型大小和硬盘速度）
3. **trust_remote_code**：必须设置为 True 以支持自定义模型代码
4. **不支持流式**：当前为完整生成后返回，大文本可能等待较久
5. **资源释放**：不用的模型应主动从 `LOADED_MODELS` 中删除并调用 `gc.collect()`
