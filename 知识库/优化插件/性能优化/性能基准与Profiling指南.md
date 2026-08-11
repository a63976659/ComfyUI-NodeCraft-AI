# 性能基准与 Profiling 指南

> **定位**：本文档是「**怎么测**」的唯一来源——所有基准计时口径与通用 Profiling 工具用法都在这里。
> 「**怎么改**」见《ComfyUI性能调优实战》。

## 1. 手动 Profiling

### 基准计时（time.perf_counter + CUDA 同步）

涉及 GPU 的计时**必须预热并同步**，否则测到的是异步下发耗时而非真实执行耗时：

```python
import time
import torch

def benchmark(func, warmup=3, runs=10, *args, **kwargs):
    """简单的函数性能基准测试"""
    # 预热（触发 CUDA 初始化、JIT 编译等）
    for _ in range(warmup):
        func(*args, **kwargs)
    torch.cuda.synchronize()  # 确保异步 CUDA 操作完成

    # 正式测量
    times = []
    for _ in range(runs):
        torch.cuda.synchronize()
        start = time.perf_counter()
        func(*args, **kwargs)
        torch.cuda.synchronize()
        elapsed = time.perf_counter() - start
        times.append(elapsed)

    avg = sum(times) / len(times)
    min_t = min(times)
    print(f"平均: {avg*1000:.2f}ms, 最小: {min_t*1000:.2f}ms, "
          f"标准差: {(sum((t-avg)**2 for t in times)/len(times))**0.5*1000:.2f}ms")
    return avg
```

> **关键**：CUDA 操作是异步的，必须在计时前后调用 `torch.cuda.synchronize()` 才能获得准确的计时。
> 纯 CPU 代码可省略 synchronize，但预热仍需保留（避免首次调用的导入/编译开销污染结果）。

### cProfile（CPU 耗时分析）
```python
import cProfile
import pstats

# 方式一：命令行
# python -m cProfile -s cumulative your_script.py

# 方式二：代码内
profiler = cProfile.Profile()
profiler.enable()
# ... 你的代码 ...
profiler.disable()
stats = pstats.Stats(profiler)
stats.sort_stats('cumulative')
stats.print_stats(20)  # 打印 top 20
```

### py-spy（低开销采样分析）
```bash
# 安装
pip install py-spy

# 生成火焰图
py-spy record -o profile.svg -- python your_script.py

# 实时 top 视图
py-spy top --pid <PID>
```

### tracemalloc（内存分析）
```python
import tracemalloc

tracemalloc.start()
# ... 你的代码 ...
snapshot = tracemalloc.take_snapshot()
top_stats = snapshot.statistics('lineno')

print("[ Top 10 内存分配 ]")
for stat in top_stats[:10]:
    print(stat)
```

### GPU 显存分析
```python
import torch

# 重置统计
torch.cuda.reset_peak_memory_stats()

# ... 推理代码 ...

# 查看显存使用
print(f"当前显存: {torch.cuda.memory_allocated() / 1024**2:.1f} MB")
print(f"峰值显存: {torch.cuda.max_memory_allocated() / 1024**2:.1f} MB")
print(f"缓存显存: {torch.cuda.memory_reserved() / 1024**2:.1f} MB")
```

### torch.profiler（GPU 算子级深度剖析）

需要定位「哪个 CUDA 算子最慢 / GPU 利用率为何低」时使用，用法与关键指标解读见
《ComfyUI性能调优实战》第五章——它紧邻具体的优化手段，便于测完直接对症下药。

## 2. 常见性能瓶颈模式

| 模式 | 症状 | 解决方案 |
|------|------|---------|
| 模型重复加载 | 每次执行耗时>5s | 缓存到类变量 |
| 显存泄漏 | 显存持续增长 | 使用 del + empty_cache |
| CPU/GPU 传输 | GPU 利用率低 | 批量传输，减少 .cpu()/.cuda() |
| 同步IO阻塞 | 响应延迟高 | 改用 asyncio |
| 无推理模式 | 显存占用高 | 加 no_grad/inference_mode |

## 3. 优化效果量化

建议在优化前后分别运行基准测试，记录：
- 执行时间变化（目标：减少 >20%）
- 内存峰值变化（目标：减少 >15%）
- GPU 显存变化（目标：减少 >10%）
