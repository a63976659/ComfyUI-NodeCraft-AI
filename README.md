# 🚀 NodeCraft AI — ComfyUI 智能编程助手

> **让 AI 帮你写 ComfyUI 插件，从想法到代码只需一次对话。**

NodeCraft AI 是一个集成在 ComfyUI 侧边栏的 AI 编程工具。它内置完整的 ComfyUI V3 开发知识库，能够理解你的需求并直接生成可运行的自定义节点代码。无需翻阅文档，无需从零开始——打开侧边栏，描述你想要的节点功能，剩下的交给 AI。

📌 **GitHub**：https://github.com/a63976659/ComfyUI-NodeCraft-AI

---

## ✨ 功能特点

| | 功能 | 说明 |
|---|---|---|
| 💬 | **智能对话** | 用自然语言描述需求，AI 帮你生成节点代码 |
| 🔄 | **双模型支持** | 同时支持在线 API 和本地模型，一键切换 |
| 📚 | **知识库驱动** | 内置 ComfyUI V3 开发规范，生成代码符合标准 |
| 📂 | **会话管理** | 每个插件项目独立会话，可随时暂停/继续 |
| 🧠 | **智能压缩** | 自动管理长对话上下文，保留关键信息不丢失 |
| 📁 | **一键脚手架** | 快速创建标准插件项目目录结构 |
| 🎨 | **专业界面** | Neo-Noir 暗黑主题，编程工具级视觉体验 |

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
3. **创建项目** — 点击「新建项目」，输入你的插件名称
4. **描述需求** — 用自然语言告诉 AI 你想要什么功能的节点
5. **获取代码** — AI 结合知识库生成符合规范的节点代码

💡 **提示**：描述越具体，生成的代码越精准。比如「帮我写一个图片缩放节点，支持按百分比和固定尺寸两种模式」。

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
├── 前端/          # 界面渲染与交互
├── 后端/          # API 路由与数据操作
├── 智能体/        # AI 模型调用与知识检索
├── 知识库/        # ComfyUI 开发规范文档
├── 数据/          # 会话记录与配置存储
└── __init__.py    # 插件入口
```

---

## 🏗️ 技术架构

NodeCraft AI 采用清晰的三层架构设计：

```
┌─────────────────────────────────┐
│  表现层 — 前端 JS/CSS           │  侧边栏界面与用户交互
├─────────────────────────────────┤
│  服务层 — Python REST API       │  路由处理与数据管理
├─────────────────────────────────┤
│  智能层 — LLM + 知识库检索      │  模型调用与知识增强生成
└─────────────────────────────────┘
```

---

## 📋 更新记录

| 日期 | 版本 | 更新内容 |
|------|------|----------|
| 2026-06-12 | v0.1.0 | 🎉 首个版本发布 |
| 2026-06-12 | v0.1.0 | 新增本地模型和在线 API 一键切换 |
| 2026-06-12 | v0.1.0 | 全新暗黑主题界面设计 |
| 2026-06-12 | v0.1.0 | 新增工具介绍页 |
| 2026-06-12 | v0.1.0 | 界面完全中文化 |

---

## 🗺️ 后续计划

- 🔜 流式响应，打字机效果实时输出
- 🔜 代码自动写入项目文件
- 🔜 知识库内容持续完善

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
