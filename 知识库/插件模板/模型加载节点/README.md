# ModelLoaderNode — 模型加载节点模板

## 功能

- 通过 COMBO 下拉列表选择 checkpoint、VAE、LoRA
- 路径安全校验（防止路径穿越）
- 设备自动管理（GPU 优先，CPU 回退）
- 内置模型缓存避免重复加载
- 可选加载独立 VAE 和 LoRA，支持强度调节

## 文件结构

```
模型加载节点/
├── __init__.py        # 节点注册入口
├── model_loader.py    # 节点实现
└── README.md          # 说明文档
```

## 使用方式

1. 将此目录复制到 `ComfyUI/custom_nodes/` 下
2. 重启 ComfyUI 或刷新页面
3. 在节点搜索中输入 `ModelLoaderNode` 即可使用

## 自定义指南

- 修改 `model_loader.py` 中的 `load_model` 方法扩展加载逻辑
- 在 `_get_available_models` 中添加自定义模型类型
- 调整 `_MODEL_CACHE` 策略以控制内存使用
