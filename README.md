# 🚀 NodeCraft AI — ComfyUI 全栈 AI 编程助手

> **从开发到优化再到可视化，让 AI 陪你走完 ComfyUI 插件全流程。**

NodeCraft AI 是一个深度集成在 ComfyUI 侧边栏的全栈 AI 编程平台。它内置三大智能界面（开发 / 优化 / 可视化），结合按场景隔离的知识库与可云端同步的踩坑记录系统，让 AI 真正理解你的项目并直接生成、优化、解读插件代码。无需翻文档、无需切窗口——一个侧边栏，搞定 ComfyUI 插件开发全流程。

📌 **GitHub**：https://github.com/a63976659/ComfyUI-NodeCraft-AI

---

## ✨ 功能特点

| | 功能 | 说明 |
|---|---|---|
| 🧩 | **三Tab多界面** | 开发插件 / 优化插件 / 功能可视化，按场景独立工作流 |
| 📚 | **场景化知识库** | 按 Tab 隔离的 BM25 多实例检索 + RAG 增强生成 |
| 🪤 | **踩坑记录系统** | 本地结构化存储 + ModelScope 云端双向同步，AI 自动检索注入上下文 |
| 💬 | **流式智能对话** | SSE 打字机效果 + Function Calling 直接读写插件文件（API 15 轮循环） |
| 🔗 | **互斥文件夹选择** | 插件列表与会话文件夹自动互斥，防止操作冲突 |
| 🔄 | **双模型支持** | 在线 API（OpenAI 兼容）与本地模型一键切换，支持图片多模态 |
| 🧠 | **上下文压缩** | 长对话自动 Token 裁剪，保留关键记忆不丢失 |
| 🎨 | **双主题界面** | Neo-Noir 暗黑 + 优雅浅色，响应式适配移动端 |
| 📂 | **多会话管理** | 独立历史、标题编辑、文件/图片附件上传 |
| ☁️ | **云端集成** | ModelScope 数据集同步、GitHub 断点续传、用户认证与会员订阅 |
| 🛠️ | **开发者工具** | GraphQL（Strawberry）+ REST + WebSocket，含 GraphiQL IDE 与性能监控 |

---

## 📸 界面预览

![界面预览](./assets/preview.png)

---

## 📦 安装

### 方式一：Git 安装（推荐）

1. 打开终端，进入 ComfyUI 的 `custom_nodes` 目录：

```bash
cd ComfyUI/custom_nodes
```

2. 克隆项目：

```bash
git clone https://github.com/a63976659/ComfyUI-NodeCraft-AI.git
```

3. 安装依赖：

```bash
cd ComfyUI-NodeCraft-AI
pip install -r requirements.txt
```

4. 重启 ComfyUI，在侧边栏即可看到 NodeCraft AI 图标。

### 方式二：ComfyUI Manager

在 ComfyUI Manager 中搜索 `NodeCraft AI`，点击安装即可。

### 方式三：手动下载

1. 前往 [GitHub 页面](https://github.com/a63976659/ComfyUI-NodeCraft-AI)，点击 **Code → Download ZIP**
2. 解压到 `ComfyUI/custom_nodes/` 目录下
3. 确保文件夹名称为 `ComfyUI-NodeCraft-AI`
4. 安装依赖后重启 ComfyUI

---

## 🎯 快速开始

1. **打开侧边栏** — 启动 ComfyUI 后，点击侧边栏中的 NodeCraft AI 图标
2. **配置模型** — 首次使用需在设置中配置 AI 模型（在线 API 或本地模型）
3. **选择界面** — 在顶部 Tab 切换：开发插件 / 优化插件 / 功能可视化
4. **新建会话** — 点击「新建会话」，描述你的需求或上传待分析的插件
5. **获取结果** — AI 结合对应知识库与踩坑记录，生成代码 / 优化建议 / 结构图

💡 **提示**：描述越具体，效果越精准。例如「帮我写一个图片缩放节点，支持按百分比和固定尺寸两种模式」。

---

## ⚙️ 模型配置说明

### API 模式（在线）

支持任何兼容 OpenAI 格式的 API 服务，只需填入：
- API 地址（如 `https://api.deepseek.com/v1`）
- 密钥（API Key）
- 模型名称

### 本地模式

将模型文件放入 `ComfyUI/models/LLM/` 目录，插件会自动识别可用模型。

### 默认推荐模型

`qwen2.5-coder-32b-instruct` — 编程能力强，中文理解好，适合节点代码生成。

---

## 📂 项目结构

```
ComfyUI-NodeCraft-AI/
├── 前端/              # 界面渲染与交互（模块化 JS/CSS、双主题、响应式）
├── 后端/              # REST + GraphQL + WebSocket（聊天、会话、同步、认证等）
├── 智能体/            # AI 引擎（模型客户端、工具路由、上下文压缩）
├── 知识库/            # 按 Tab 隔离的开发文档
│   ├── 开发插件/    # 插件开发知识（develop）
│   ├── 优化插件/    # 性能优化知识（optimize）
│   └── 功能可视化/  # 功能分析知识（visualize）
├── 数据/              # 会话记录、踩坑记录与配置存储
├── 技术文档/          # 项目技术文档（10 份）
└── __init__.py        # 插件入口
```

---

## 🏗️ 技术架构

NodeCraft AI 采用清晰的四层架构设计：

```
┌───────────────────────────────────────────────┐
│  表现层 — 前端 JS/CSS                          │  三Tab侧边栏 + 双主题 + 响应式
├───────────────────────────────────────────────┤
│  服务层 — Quart REST + GraphQL + WebSocket    │  路由、认证、同步、文件管理
├───────────────────────────────────────────────┤
│  智能层 — LLM + BM25 多实例知识库              │  RAG 检索、Function Calling、上下文压缩
├───────────────────────────────────────────────┤
│  数据层 — 本地 JSON + ModelScope 云端          │  会话、FAQ、知识库、踩坑记录、插件文件
└───────────────────────────────────────────────┘
```

---

## 📋 更新记录

| 日期 | 版本 | 更新内容 |
|------|------|----------|
| 2026-06-22 | v0.12.0 | 🤖 本地模型工具调用适配层（计划中）、API 工具调用上限提升至 15 轮 |
| 2026-06-21 | v0.11.0 | 🔧 后端认证体系清理（移除旧本地认证，RanKing 完全替代）、文件夹选择互斥机制 |
| 2026-06-20 | v0.10.0 | 📋 三界面独立会话列表、会话列表面板公共组件、释放显存按钮统一、取消按钮 |
| 2026-06-17 | v0.9.0 | 📚 知识库按 Tab 隔离、AI 自动检索踩坑记录、云端双向同步 |
| 2026-06-15 | v0.8.0 | 🎨 深色 / 浅色双主题、前端生产级 UI 升级 |
| 2026-06-14 | v0.7.0 | 🛰️ GraphQL API、代码审计与安全修复 |
| 2026-06-13 | v0.6.0 | 🧩 三 Tab 架构、Function Calling、ModelScope 云端同步 |
| 2026-06-12 | v0.5.0 | ⚡ SSE 流式响应、模块化拆分、JWT 认证 |
| 2026-06-12 | v0.1.0 | 🎉 首个版本发布 |

---

## 🗺️ 后续计划

- 🔜 本地模型工具调用适配层（文本标记解析）
- 🔜 向量检索（Embedding）增强知识库精度
- 🔜 插件模板市场
- 🔜 更多知识库内容持续完善（优化、可视化）

---

## 📄 开源协议

本项目基于 [MIT License](LICENSE) 开源。

## 🙏 致谢

- [ComfyUI](https://github.com/comfyanonymous/ComfyUI) — 强大的节点式 AI 图像生成平台
- [comfyui-custom-node-skills](https://github.com/comfyanonymous/comfyui-custom-node-skills) — ComfyUI 自定义节点开发知识来源

---

<p align="center">
  <b>如果觉得有用，欢迎给个 ⭐ Star！</b>
</p>
