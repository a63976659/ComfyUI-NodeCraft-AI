# ComfyUI 性能调优实战

## 概述

本文档涵盖 ComfyUI 插件开发中的性能调优实践，从 GPU 显存管理到推理加速，从批处理优化到内存泄漏防护，并汇总 ComfyUI 特有的性能陷阱。每个主题均附带可直接参考的代码示例。

> 本文回答「**怎么改**」。优化前后的**测量**（基准计时、cProfile、py-spy、tracemalloc、GPU 显存统计、效果量化）
> 统一见《性能基准与Profiling指南》。

---

## 一、GPU 内存优化

### 1.1 CUDA 显存管理基础

PyTorch 的 CUDA 显存分配器使用缓存机制：释放的张量内存不会立即归还 GPU，而是缓存在进程内供下次分配复用。理解这一点是显存优化的前提。

```python
import torch

# 查看当前显存占用
print(f"已分配: {torch.cuda.memory_allocated() / 1024**2:.1f} MB")
print(f"已缓存: {torch.cuda.memory_reserved() / 1024**2:.1f} MB")

# 查看显存峰值
print(f"峰值分配: {torch.cuda.max_memory_allocated() / 1024**2:.1f} MB")

# 重置峰值统计（不影响实际显存）
torch.cuda.reset_peak_memory_stats()
```

### 1.2 model.cpu() / .cuda() 切换

当多个模型需要轮流使用 GPU 时，手动在 CPU 和 GPU 之间移动模型可以有效降低峰值显存：

```python
class MultiModelPipeline:
    def __init__(self):
        self.model_a = None
        self.model_b = None
        self._current_device = "cpu"

    def _load_model_a(self):
        if self.model_b is not None:
            self.model_b.cpu()
            torch.cuda.empty_cache()
        if self.model_a is None:
            self.model_a = MyModelA().to("cuda")
        else:
            self.model_a.cuda()
        self._current_device = "cuda"

    def _load_model_b(self):
        if self.model_a is not None:
            self.model_a.cpu()
            torch.cuda.empty_cache()
        if self.model_b is None:
            self.model_b = MyModelB().to("cuda")
        else:
            self.model_b.cuda()
        self._current_device = "cuda"
```

> **注意**：频繁的 `.cpu()` / `.cuda()` 切换会引入数据传输开销。仅在显存不足以同时容纳多个模型时使用此策略。

### 1.3 torch.cuda.empty_cache() 使用时机

`empty_cache()` 释放缓存中未使用的显存块归还给 GPU，但**不会释放正在使用的张量**。

```python
# 正确用法：先 del 不再需要的张量，再 empty_cache
def process_large_batch(images):
    results = []
    for chunk in chunks(images, batch_size=4):
        output = model(chunk)
        results.append(output.cpu())  # 立即移回 CPU
        del output                     # 释放引用
    torch.cuda.empty_cache()           # 清理缓存的显存块
    return torch.cat(results)
```

**错误用法**：在循环内部频繁调用 `empty_cache()`，会破坏缓存复用机制，导致频繁的 CUDA malloc/free，反而降低性能。

```python
# 错误：每轮都 empty_cache，破坏缓存复用
for image in images:
    result = model(image)
    torch.cuda.empty_cache()  # 不推荐！
```

### 1.4 显存碎片化处理

长时间运行的 ComfyUI 工作流可能产生显存碎片，表现为 "CUDA out of memory" 但实际显存足够：

```python
# 设置显存分配策略： expandable segments（PyTorch 2.1+）
import os
os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"

# 或在代码中设置
torch.cuda.memory.set_per_process_memory_fraction(0.9)  # 限制使用 90% 显存
```

---

## 二、推理速度优化

### 2.1 torch.no_grad()

推理时禁用梯度计算可减少内存占用并加速计算：

```python
# 方式一：上下文管理器
with torch.no_grad():
    output = model(input_tensor)

# 方式二：装饰器
@torch.no_grad()
def inference(model, x):
    return model(x)
```

### 2.2 torch.inference_mode()

`inference_mode()` 是 `no_grad()` 的进阶版本，性能更好（PyTorch 1.9+）。它会禁用视图追踪和版本计数器，进一步减少开销：

```python
# inference_mode 比 no_grad 更快
with torch.inference_mode():
    output = model(input_tensor)

# 装饰器形式
@torch.inference_mode()
def fast_inference(model, x):
    return model(x)
```

> **注意**：`inference_mode()` 下创建的张量无法在后续的 `autograd` 上下文中使用。如果需要保存推理结果用于反向传播，请使用 `no_grad()`。

### 2.3 half() 半精度推理

将模型和张量转换为 FP16 可将显存占用减半并利用 Tensor Core 加速：

```python
# 模型半精度
model = model.half()
input_tensor = input_tensor.half()
output = model(input_tensor)

# 更安全的方式：autocast（自动混合精度）
with torch.autocast(device_type="cuda", dtype=torch.float16):
    output = model(input_tensor)
```

> **注意**：某些操作在 FP16 下可能溢出（如 softmax 中的大指数值）。使用 `autocast` 可自动处理这些情况，比手动 `.half()` 更安全。

### 2.4 torch.compile() 图编译加速

PyTorch 2.0+ 的 `torch.compile()` 通过算子融合和图优化显著加速推理：

```python
# 编译模型（首次运行会较慢，后续加速）
compiled_model = torch.compile(model, mode="reduce-overhead")

# 在 ComfyUI 节点中使用
class FastInferenceNode:
    _compiled_model = None

    @classmethod
    def execute(cls, image, **kwargs):
        if cls._compiled_model is None:
            cls._compiled_model = torch.compile(
                cls._build_model(kwargs),
                mode="reduce-overhead"
            )
        with torch.inference_mode():
            return cls._compiled_model(image)
```

**编译模式选择**：
| 模式 | 适用场景 | 特点 |
|------|---------|------|
| `default` | 通用场景 | 平衡编译时间和运行时性能 |
| `reduce-overhead` | 小 batch 推理 | 减少 Python 开销，CUDA Graph |
| `max-autotune` | 固定输入形状 | 自动调优算子，编译时间最长 |

---

## 三、批处理优化

### 3.1 动态 batch size

根据输入数量和可用显存动态调整 batch size：

```python
def get_optimal_batch_size(image_count, base_size=4, max_vram_gb=8):
    """根据可用显存动态计算 batch size"""
    if torch.cuda.is_available():
        free_vram = torch.cuda.mem_get_info()[0] / 1024**3
        # 每张图约需 0.5-2GB 显存（取决于分辨率和模型）
        estimated_per_image = max_vram_gb / base_size
        batch_size = int(free_vram / estimated_per_image)
        return max(1, min(batch_size, image_count))
    return 1

def batch_process(model, images):
    results = []
    batch_size = get_optimal_batch_size(len(images))
    for i in range(0, len(images), batch_size):
        batch = images[i:i + batch_size]
        with torch.inference_mode():
            output = model(batch)
        results.append(output)
    return torch.cat(results, dim=0)
```

### 3.2 DataLoader num_workers

当节点需要从磁盘批量加载文件时，使用多进程 DataLoader 避免成为 I/O 瓶颈：

```python
from torch.utils.data import Dataset, DataLoader

class ImageFileDataset(Dataset):
    def __init__(self, file_paths):
        self.file_paths = file_paths

    def __len__(self):
        return len(self.file_paths)

    def __getitem__(self, idx):
        from PIL import Image
        import numpy as np
        img = Image.open(self.file_paths[idx]).convert("RGB")
        arr = np.array(img).astype(np.float32) / 255.0
        return torch.from_numpy(arr)

def load_batch(file_paths, batch_size=8):
    dataset = ImageFileDataset(file_paths)
    # num_workers > 0 启用多进程加载
    # persistent_workers=True 避免每轮重启进程
    loader = DataLoader(
        dataset,
        batch_size=batch_size,
        num_workers=4,
        persistent_workers=True,
        pin_memory=True  # 使用锁页内存加速 CPU→GPU 传输
    )
    return loader
```

> **注意**：ComfyUI 节点通常在主线程执行，DataLoader 的多进程可能在某些环境下引起问题。建议仅在批量文件处理场景中使用，并确保正确关闭 DataLoader。

---

## 四、内存泄漏防护

### 4.1 常见泄漏模式

#### 模式一：全局变量持有张量

```python
# 错误：全局缓存不断增长
_global_cache = {}

def process(image):
    key = hash(image.sum().item())
    if key not in _global_cache:
        _global_cache[key] = model(image)  # 张量永不释放
    return _global_cache[key]

# 正确：使用有界 LRU 缓存
from functools import lru_cache

@lru_cache(maxsize=8)
def _process_cached(key):
    return model(image)
```

#### 模式二：回调未清理

```python
# 错误：注册的回调持有闭包引用
class NodeWithCallback:
    def __init__(self):
        self._callbacks = []

    def register_callback(self, fn):
        self._callbacks.append(fn)  # 永不移除

    def cleanup(self):
        # 缺少清理逻辑
        pass

# 正确：提供清理机制
class NodeWithCallback:
    def __init__(self):
        self._callbacks = []

    def register_callback(self, fn):
        self._callbacks.append(fn)
        return lambda: self._callbacks.remove(fn)  # 返回取消函数

    def cleanup(self):
        self._callbacks.clear()
```

#### 模式三：缓存无上限

```python
# 错误：字典缓存无大小限制
_feature_cache = {}

def extract_features(image):
    key = image.sum().item()
    if key not in _feature_cache:
        _feature_cache[key] = heavy_compute(image)
    return _feature_cache[key]

# 正确：使用 OrderedDict 实现有界缓存
from collections import OrderedDict

class BoundedCache:
    def __init__(self, max_size=100):
        self._cache = OrderedDict()
        self._max_size = max_size

    def get(self, key):
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        return None

    def set(self, key, value):
        if key in self._cache:
            self._cache.move_to_end(key)
        self._cache[key] = value
        if len(self._cache) > self._max_size:
            self._cache.popitem(last=False)  # 淘汰最旧的
```

### 4.2 张量生命周期管理

```python
import gc

def safe_pipeline(images, model):
    results = []
    for image in images:
        with torch.inference_mode():
            output = model(image)
            results.append(output.cpu())  # 立即移到 CPU
            del output
        gc.collect()  # 定期触发 Python GC
    torch.cuda.empty_cache()
    return results
```

### 4.3 显存监控工具

```python
class VRAMMonitor:
    """显存使用监控器，用于检测泄漏"""
    def __init__(self, name="monitor"):
        self.name = name
        self._snapshots = []

    def snapshot(self, label=""):
        allocated = torch.cuda.memory_allocated() / 1024**2
        reserved = torch.cuda.memory_reserved() / 1024**2
        self._snapshots.append((label, allocated, reserved))
        print(f"[{self.name}] {label}: "
              f"allocated={allocated:.1f}MB, reserved={reserved:.1f}MB")

    def report(self):
        """检查是否存在显存持续增长（泄漏迹象）"""
        if len(self._snapshots) < 2:
            return
        first = self._snapshots[0][1]
        last = self._snapshots[-1][1]
        if last > first * 1.5:
            print(f"[警告] {self.name}: 显存从 {first:.1f}MB 增长到 {last:.1f}MB，"
                  f"可能存在泄漏")
```

---

## 五、torch.profiler 深度剖析

> 基准计时口径（warmup + `torch.cuda.synchronize()`）、cProfile、py-spy、tracemalloc、GPU 显存统计
> 等通用测量工具，统一见《性能基准与Profiling指南》，本文不再重复。
> 本章只保留与上下文优化手段强相关的**算子级 GPU 剖析**——它的输出直接映射到一、二、六章的优化动作。

```python
from torch.profiler import profile, ProfilerActivity, schedule

def torch_profile_inference(model, input_tensor):
    """使用 PyTorch Profiler 分析 GPU 利用率和算子耗时"""
    with profile(
        activities=[ProfilerActivity.CPU, ProfilerActivity.CUDA],
        schedule=schedule(wait=1, warmup=1, active=3),
        on_trace_ready=lambda p: print(p.key_averages().table(
            sort_by="cuda_time_total", row_limit=15
        ))
    ) as prof:
        for _ in range(5):
            with torch.inference_mode():
                model(input_tensor)
            prof.step()

    # 导出 Chrome Trace 文件用于可视化
    prof.export_chrome_trace("trace.json")
```

**关键指标解读**：
| 指标 | 含义 | 优化方向 |
|------|------|---------|
| Self CUDA Time | 算子自身 GPU 耗时 | 找到最耗时的 CUDA 算子 |
| CPU-GPU Overlap | CPU 与 GPU 并行度 | 提高 overlap 可隐藏 CPU 开销 |
| Device Utilization | GPU 利用率 | 低利用率说明存在瓶颈（数据传输、CPU 等待） |

---

## 六、ComfyUI 特有性能陷阱

### 6.1 节点缓存粒度

ComfyUI 通过节点输出的哈希值决定是否重新执行节点。如果 `IS_CHANGED` 返回的值不稳定，会导致不必要的重复计算：

```python
import hashlib

class ImageFilterNode:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "intensity": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 5.0, "step": 0.01}),
            }
        }

    # 正确：返回稳定的缓存键
    @classmethod
    def IS_CHANGED(cls, image, intensity):
        # 量化 intensity 避免浮点精度导致频繁缓存失效
        return (int(image.sum().item()), round(intensity, 2))

    # 错误：返回随机值或时间戳，导致每次都重新执行
    # @classmethod
    # def IS_CHANGED(cls, image, intensity):
    #     return float("nan")  # 每次都重新执行（仅适用于实时预览节点）
```

### 6.2 IS_CHANGED 频繁触发

某些节点（如随机噪声生成器）如果不正确实现 `IS_CHANGED`，会导致下游所有节点重复执行：

```python
class SeedNoiseNode:
    @classmethod
    def IS_CHANGED(cls, seed, width, height):
        # 好的做法：基于 seed 和尺寸生成稳定键
        return f"{seed}_{width}_{height}"

    # 问题做法：返回 float("nan") 会导致所有下游节点每次都重新执行
    # 当用户只想调整下游参数时，噪声节点也会不必要地重新执行
```

### 6.3 大图分块处理

处理高分辨率图片时，直接送入模型可能 OOM。分块处理是常见解决方案：

```python
def tiled_process(image, model, tile_size=512, overlap=64):
    """
    分块处理大图，避免 OOM
    image: [B, H, W, C] 格式
    """
    B, H, W, C = image.shape
    output = torch.zeros_like(image)

    for y in range(0, H, tile_size - overlap):
        for x in range(0, W, tile_size - overlap):
            # 计算分块边界
            y2 = min(y + tile_size, H)
            x2 = min(x + tile_size, W)
            y1 = max(0, y2 - tile_size)
            x1 = max(0, x2 - tile_size)

            # 提取分块
            tile = image[:, y1:y2, x1:x2, :]

            # 处理分块
            with torch.inference_mode():
                result = model(tile)

            # 混合重叠区域（使用渐变权重避免接缝）
            weight = torch.ones_like(result)
            blend_size = min(overlap, y2 - y1, x2 - x1)
            for i in range(blend_size):
                w = i / blend_size
                if y1 > 0:
                    weight[:, i, :, :] *= w
                if y2 < H:
                    weight[:, -(i+1), :, :] *= w
                if x1 > 0:
                    weight[:, :, i, :] *= w
                if x2 < W:
                    weight[:, :, -(i+1), :] *= w

            output[:, y1:y2, x1:x2, :] = (
                output[:, y1:y2, x1:x2, :] * (1 - weight) +
                result * weight
            )

            del tile, result, weight

    torch.cuda.empty_cache()
    return output
```

### 6.4 模型重复加载

ComfyUI 的模型管理器会缓存已加载的模型，但自定义节点如果绕过管理器直接加载，会导致重复占用显存：

```python
# 错误：每次执行都重新加载模型
class BadNode:
    def execute(self, image):
        model = load_model("model_path.pth")  # 每次都加载！
        return model(image)

# 正确：使用类级别缓存
class GoodNode:
    _model_cache = {}

    @classmethod
    def _get_model(cls, model_path):
        if model_path not in cls._model_cache:
            cls._model_cache[model_path] = load_model(model_path)
        return cls._model_cache[model_path]

    def execute(self, image, model_path):
        model = self._get_model(model_path)
        with torch.inference_mode():
            return model(image)
```

### 6.5 不必要的 CPU↔GPU 数据传输

```python
# 错误：在循环中频繁 CPU↔GPU 传输
def bad_loop(images, model):
    results = []
    for img in images:
        img_gpu = img.cuda()          # CPU → GPU
        output = model(img_gpu)
        output_cpu = output.cpu()     # GPU → CPU
        results.append(output_cpu)
    return results

# 正确：批量传输
def good_loop(images, model):
    batch = torch.stack(images).cuda()  # 一次性传输
    with torch.inference_mode():
        outputs = model(batch)
    return outputs.cpu()  # 一次性传回
```

---

## 七、性能优化检查清单

在提交插件代码前，逐项检查以下要点：

| 类别 | 检查项 | 通过标准 |
|------|--------|---------|
| 显存 | 推理是否使用 `no_grad()` 或 `inference_mode()` | 是 |
| 显存 | 中间张量是否及时 `del` | 是 |
| 显存 | 模型是否使用类级别缓存避免重复加载 | 是 |
| 显存 | `empty_cache()` 是否仅在长流程结束时调用 | 是 |
| 速度 | 是否使用半精度推理（`half()` 或 `autocast`） | 推荐 |
| 速度 | 批处理是否合并小操作 | 是 |
| 速度 | 是否避免循环内 CPU↔GPU 传输 | 是 |
| 缓存 | `IS_CHANGED` 是否返回稳定值 | 是 |
| 缓存 | 全局缓存是否有大小限制 | 是 |
| 泄漏 | 回调/事件监听是否有清理机制 | 是 |
| 泄漏 | 节点是否有 `del` 清理方法 | 推荐 |
