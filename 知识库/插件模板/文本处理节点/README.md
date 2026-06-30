# TextProcessNode — 文本处理节点模板

## 功能

- 多行文本输入（multiline STRING widget）
- 四种处理模式：
  - `template`：模板变量替换（`{key}` 占位符）
  - `encode`：文本编码（Base64 / URL）
  - `decode`：文本解码（Base64 / URL）
  - `concat`：文本拼接
- 可选附加文本输入

## 文件结构

```
文本处理节点/
├── __init__.py        # 节点注册入口
├── text_node.py       # 节点实现
└── README.md          # 说明文档
```

## 使用方式

1. 将此目录复制到 `ComfyUI/custom_nodes/` 下
2. 重启 ComfyUI 或刷新页面
3. 在节点搜索中输入 `TextProcessNode` 即可使用

## 自定义指南

- 在 `process` 方法中添加更多处理模式
- 扩展 `_apply_template` 支持更复杂的模板语法
- 添加新的编码类型（如 hex、rot13 等）
