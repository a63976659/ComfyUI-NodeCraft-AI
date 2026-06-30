# 自定义 UI 扩展模板

## 功能

- 自定义 Widget（在节点上绘制自定义 UI 控件）
- 侧边栏按钮 + 浮动面板
- Python-JS 通信（GET/POST API + WebSocket 事件监听）
- 生命周期钩子（beforeRegisterNodeDef / setup）
- 不注册任何节点类，纯前端 + 后端 API 扩展

## 文件结构

```
自定义UI扩展/
├── __init__.py            # 后端 API 端点 + WEB_DIRECTORY 注册
├── js/
│   └── custom_widget.js   # 前端扩展（widget、面板、通信）
└── README.md              # 说明文档
```

## 使用方式

1. 将此目录复制到 `ComfyUI/custom_nodes/` 下
2. 重启 ComfyUI 或刷新页面
3. 侧边栏会出现"自定义面板"按钮
4. 后端 API 端点：
   - `GET /custom_ui_example/data` — 获取自定义数据
   - `POST /custom_ui_example/action` — 发送操作请求

## 自定义指南

- 在 `__init__.py` 中添加更多 API 端点
- 在 `custom_widget.js` 中扩展自定义 widget 类型
- 修改 `openCustomPanel` 函数自定义面板 UI
- 通过 `api.addEventListener` 监听后端 WebSocket 事件
