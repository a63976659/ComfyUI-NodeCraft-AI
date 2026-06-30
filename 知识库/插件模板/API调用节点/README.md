# APICallNode — API 调用节点模板

## 功能

- 异步 HTTP 请求（GET / POST）
- HIDDEN 输入传递 api_key（不在节点 UI 上显示）
- 超时处理（可配置超时秒数）
- 自动重试机制（最多 3 次，指数退避）
- 错误恢复与友好错误信息
- 返回响应体、响应头、状态码

## 文件结构

```
API调用节点/
├── __init__.py        # 节点注册入口
├── api_node.py        # 节点实现
└── README.md          # 说明文档
```

## 使用方式

1. 将此目录复制到 `ComfyUI/custom_nodes/` 下
2. 重启 ComfyUI 或刷新页面
3. 在节点搜索中输入 `APICallNode` 即可使用

## HIDDEN 输入说明

`api_key` 使用 HIDDEN 输入类型，不会在节点 UI 上显示。
可通过以下方式注入：
- 从其他节点连接输出到 api_key 端口
- 通过 ComfyUI 的工作流 API 注入

## 自定义指南

- 修改 `_async_call` 方法添加自定义认证逻辑
- 调整 `MAX_RETRIES` 和 `RETRY_DELAY` 控制重试策略
- 添加新的 HTTP 方法（PUT / DELETE 等）
- 在响应解析中添加特定格式处理（XML / GraphQL 等）
