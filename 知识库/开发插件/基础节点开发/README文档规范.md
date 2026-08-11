# README 文档规范

插件文件夹根目录的 `README.md` 是插件的说明书，用户在 ComfyUI Manager、GitHub 页面上第一眼看到的就是它。
编写或补全 README.md 时，统一按以下结构组织内容。

## 整体结构模板

```markdown
# 插件名称

[![Bilibili](https://img.shields.io/badge/bilibili-猪的飞行梦-00A1D6?logo=bilibili&logoColor=white)](https://space.bilibili.com/2114638644)
[![GitHub](https://img.shields.io/github/stars/a63976659/ComfyUI-Chinese-Translation?style=flat&logo=github)](https://github.com/a63976659/ComfyUI-Chinese-Translation)

一句话介绍这个插件是做什么的、能帮用户解决什么问题。

## 目录
- [设计理念](#设计理念)
- [功能特点](#功能特点)
- [安装方法](#安装方法)
- [使用方法](#使用方法)
- [项目结构](#项目结构)
- [技术架构](#技术架构)
- [更新记录](#更新记录)

## 设计理念

（为什么做这个插件、设计取舍，2~4 句话即可）

## 功能特点

- 特点一
- 特点二
- 特点三

## 安装方法

1. 将插件文件夹放入 `ComfyUI/custom_nodes/` 目录（或 `git clone` 到该目录）
2. 安装依赖：`pip install -r requirements.txt`（如无额外依赖可省略此步）
3. 重启 ComfyUI

## 使用方法

（在节点菜单什么分类下找到节点、基本用法、典型工作流，可配截图）

## 项目结构

（目录树代码块，与插件实际结构一致）

## 技术架构

（基于 ComfyUI V3 节点规范 / WEB_DIRECTORY 前端扩展等简要说明，没有前端扩展可省略）

## 更新记录

- 首次发布
```

## 各模块说明

| 顺序 | 模块 | 必需性 | 编写要点 |
|------|------|--------|---------|
| 1 | 插件名称标题 | 必需 | 一级标题，与插件文件夹名称一致 |
| 2 | 徽章链接行 | 可选 | 紧跟标题下一行，见下方「徽章」小节 |
| 3 | 一句话介绍 | 必需 | 一句大白话说清插件用途，不堆术语 |
| 4 | 目录 | 推荐 | 内容较长时提供，锚点链接到各章节 |
| 5 | 设计理念 | 必需 | 简明交代设计初衷与取舍 |
| 6 | 功能特点 | 必需 | 无序列表，每条一个特点 |
| 7 | 安装方法 | 必需 | 编号步骤，写清放置目录与重启要求 |
| 8 | 使用方法 | 必需 | 告诉用户怎么找到、怎么用，可配截图 |
| 9 | 项目结构 | 推荐 | 目录树代码块，目录名与实际一致（中文项目 `节点/`+`网页资源/`，英文项目 `nodes/`+`web/`） |
| 10 | 技术架构 | 可选 | 有前端扩展或多模块设计时说明，纯节点插件可省略 |
| 11 | 更新记录 | 必需 | 每次更新在此模块顶部追加一条，一条一句大白话，禁用专业术语 |
| 12 | 其他模块 | 可选 | 按需添加：界面预览（截图/GIF）、开源协议、致谢 |

## 徽章（标题下一行）

徽章行放在插件名称标题的下一行，两个常用徽章：

**B 站账号链接徽章**（替换账号名、ID 与主页地址）：

```markdown
[![Bilibili](https://img.shields.io/badge/bilibili-猪的飞行梦-00A1D6?logo=bilibili&logoColor=white)](https://space.bilibili.com/2114638644)
```

**GitHub 仓库收藏数徽章**（替换仓库 `作者/仓库名`，外层链接指向仓库主页）：

```markdown
[![GitHub](https://img.shields.io/github/stars/a63976659/ComfyUI-Chinese-Translation?style=flat&logo=github)](https://github.com/a63976659/ComfyUI-Chinese-Translation)
```

注意事项：
- 徽章只放真实存在的账号/仓库，没有就不放，不要编造链接
- 徽章行与一句话介绍之间空一行
- shields.io 徽章在国内可能加载慢，属于可选装饰，不影响文档主体

## 更新记录的维护规则

- 每次完成功能后只在「更新记录」模块**追加**，不重写历史条目
- 每条是一句用户能看懂的大白话，禁用专业术语（如"重构""依赖注入"）
- 新条目追加在该模块标题之后、已有条目之前（最新在上）

## 英文项目对应标题

英文界面创建的插件（`nodes/` + `web/`）使用英文版 README，模块标题对应关系：

| 中文 | 英文 |
|------|------|
| 一句话介绍 | 标题下的简介段落 |
| 目录 | Table of Contents |
| 设计理念 | Design Philosophy |
| 功能特点 | Features |
| 安装方法 | Installation |
| 使用方法 | Usage |
| 项目结构 | Project Structure |
| 技术架构 | Technical Architecture |
| 更新记录 | Changelog |

## 常见错误

- ❌ 只有安装说明，没有功能介绍和使用方法，用户不知道插件能干什么
- ❌ 更新记录写成开发日志（"修复 NPE""重构管道"），用户看不懂
- ❌ 项目结构里的目录名与插件实际结构不一致
- ❌ 徽章链接编造或指向无关页面
