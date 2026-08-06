# ImageProcessNode — 基础图像处理节点模板

## 功能

- 接收 `IMAGE` 输入，支持批量处理
- 通过 `intensity` 滑块控制处理强度（0.0 ~ 10.0）
- 可选 `MASK` 输入实现局部遮罩处理
- 包含前端 JS 扩展示例（节点生命周期钩子）

## 文件结构

```
基础图像处理节点/
├── __init__.py        # 节点注册入口
├── image_node.py      # 节点实现
├── js/
│   └── example.js     # 前端扩展示例
└── README.md          # 说明文档
```

## 使用方式

1. 将此目录复制到 `ComfyUI/custom_nodes/` 下
2. 重启 ComfyUI 或刷新页面
3. 在节点搜索中输入 `ImageProcessNode` 即可使用

## 自定义指南

- 修改 `image_node.py` 中的 `process` 方法实现你的图像处理逻辑
- 修改 `CATEGORY` 属性调整节点在菜单中的分类
- 在 `js/example.js` 中添加自定义 UI 交互
