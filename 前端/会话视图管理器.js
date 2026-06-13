// ═══════════════════════════════════════════════════════════════
// 会话视图管理器.js — UI 渲染 · 事件绑定 · 动画控制
// NodeCraft AI — Luxury Terminal / Neo-Noir Hacker
// ═══════════════════════════════════════════════════════════════

import {
    事件总线, 事件, 状态,
    获取会话列表, 创建会话, 删除会话, 切换会话,
    发送消息, 加载设置, 保存设置, 创建插件文件夹,
    获取当前模型名称, 获取会话序号, 格式化时间,
    获取本地模型列表, 推导平台名称,
} from "./交互与状态.js";

// ─── 工具函数 ─────────────────────────────────────────────────
function el(tag, attrs = {}, children = []) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
        if (k === "class") node.className = v;
        else if (k === "style" && typeof v === "object") Object.assign(node.style, v);
        else if (k.startsWith("on") && k.length > 2) node.addEventListener(k.slice(2).toLowerCase(), v);
        else if (k === "html") node.innerHTML = v;
        else if (k === "text") node.textContent = v;
        else node.setAttribute(k, v);
    }
    for (const child of Array.isArray(children) ? children : [children]) {
        if (!child) continue;
        node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
    }
    return node;
}

function 简易Markdown渲染(text) {
    if (!text) return "";
    let html = text
        .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

    // 代码块 — 带语言标签和复制按钮
    html = html.replace(/```(\w*)\n?([\s\S]*?)```/g, (_, lang, code) => {
        const langLabel = lang ? `<span class="nca-code-lang">${lang}</span>` : "";
        return `<pre>${langLabel}<code class="lang-${lang}">${code.trim()}</code><button class="nca-code-copy" title="复制">复制</button></pre>`;
    });

    // 行内代码
    html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
    // 粗体
    html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    // 斜体
    html = html.replace(/\*(.+?)\*/g, "<em>$1</em>");
    // 链接
    html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank">$1</a>');
    // 段落
    html = html.replace(/\n\n/g, "</p><p>");
    // 单换行
    html = html.replace(/\n/g, "<br>");
    html = `<p>${html}</p>`;

    return html;
}

// SVG 图标 — 菱形品牌标志
const LOGO_SVG = `<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><path d="M12 2L22 12L12 22L2 12Z"/></svg>`;

// ─── 主渲染函数 ──────────────────────────────────────────────
export function renderSidebarUI(container) {
    // 注入样式表
    注入样式资源();

    // 确保父容器撑满
    container.style.height = '100%';
    container.style.display = 'flex';
    container.style.flexDirection = 'column';

    // 创建主容器
    const 根容器 = el("div", { class: "nca-container" });
    container.innerHTML = "";
    container.appendChild(根容器);

    // 引用存储
    const refs = {};

    // ─── 构建 UI 结构 ──────────────────────────────────────
    根容器.appendChild(渲染顶部品牌栏());

    // 主内容区（用于介绍页切换时的显示/隐藏）
    refs.主内容区 = el("div", { class: "nca-main-content" });
    refs.主内容区.appendChild(渲染会话列表区域());
    refs.消息区域 = el("div", { class: "nca-messages" });
    refs.主内容区.appendChild(refs.消息区域);
    渲染欢迎页(refs.消息区域);
    refs.主内容区.appendChild(渲染模型切换栏());
    refs.主内容区.appendChild(渲染输入区域());
    refs.主内容区.appendChild(渲染状态栏());
    根容器.appendChild(refs.主内容区);

    // 工具介绍页
    refs.介绍页 = 渲染工具介绍页();
    根容器.appendChild(refs.介绍页);

    // 底部版本信息
    根容器.appendChild(渲染底部版本栏());

    // 绑定事件 + 初始化
    绑定全局事件();
    初始化();

    // ═══════════════════════════════════════════════════════════
    // 样式与字体注入
    // ═══════════════════════════════════════════════════════════

    function 注入样式资源() {
        // CSS 样式表
        if (!document.querySelector('link[data-nca-style]')) {
            const link = document.createElement("link");
            link.rel = "stylesheet";
            link.type = "text/css";
            link.dataset.ncaStyle = "true";
            link.href = new URL("./样式表.css", import.meta.url).href;
            document.head.appendChild(link);
        }
        // Google Fonts — JetBrains Mono
        if (!document.querySelector('link[data-nca-font]')) {
            const fontLink = document.createElement("link");
            fontLink.rel = "stylesheet";
            fontLink.dataset.ncaFont = "true";
            fontLink.href = "https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600;700&display=swap";
            document.head.appendChild(fontLink);
        }
    }

    // ═══════════════════════════════════════════════════════════
    // 顶部品牌栏
    // ═══════════════════════════════════════════════════════════

    function 渲染顶部品牌栏() {
        const brand = el("div", { class: "nca-header-brand" }, [
            el("div", { class: "nca-logo", html: LOGO_SVG }),
            el("span", { class: "nca-brand-text", text: "NodeCraft AI" }),
        ]);
        // 点击标题切换介绍页
        brand.style.cursor = "pointer";
        brand.addEventListener("click", () => 切换介绍页(true));

        return el("div", { class: "nca-header" }, [
            brand,
            el("div", { class: "nca-header-actions" }, [
                el("button", {
                    class: "nca-icon-btn settings-btn",
                    title: "设置",
                    html: "⚙",
                    onClick: () => 显示设置面板(),
                }),
                el("button", {
                    class: "nca-icon-btn new-session-btn",
                    title: "新建会话",
                    html: "✦",
                    onClick: () => 显示创建项目对话框(),
                }),
            ]),
        ]);
    }

    // ═══════════════════════════════════════════════════════════
    // 工具介绍页
    // ═══════════════════════════════════════════════════════════

    function 渲染工具介绍页() {
        const page = el("div", { class: "nca-about-page" });

        // 品牌区
        const brandSection = el("div", { class: "nca-about-brand" }, [
            el("div", { class: "nca-about-logo", html: LOGO_SVG }),
            el("h2", { class: "nca-about-title", text: "NodeCraft AI" }),
            el("span", { class: "nca-about-version", text: "v0.1.0" }),
        ]);
        page.appendChild(brandSection);

        // 分割线
        page.appendChild(el("div", { class: "nca-about-divider" }));

        // 描述
        page.appendChild(el("p", { class: "nca-about-desc", text: "🤖 AI 驱动的 ComfyUI 插件开发助手" }));

        // 特性卡片网格
        const features = [
            { icon: "🧠", title: "智能 AI 对话", desc: "编程辅助" },
            { icon: "📚", title: "知识库检索", desc: "精准参考" },
            { icon: "📁", title: "项目一键创建", desc: "标准结构" },
            { icon: "💬", title: "多会话管理", desc: "持久保存" },
            { icon: "🔌", title: "双模式接入", desc: "本地+API" },
            { icon: "🖥️", title: "跨平台支持", desc: "Win+Mac" },
        ];

        const grid = el("div", { class: "nca-about-grid" });
        features.forEach(f => {
            grid.appendChild(el("div", { class: "nca-about-card" }, [
                el("span", { class: "nca-about-card-icon", text: f.icon }),
                el("span", { class: "nca-about-card-title", text: f.title }),
                el("span", { class: "nca-about-card-desc", text: f.desc }),
            ]));
        });
        page.appendChild(grid);

        // 分割线
        page.appendChild(el("div", { class: "nca-about-divider" }));

        // GitHub 链接
        const ghLink = el("a", {
            class: "nca-about-github",
            href: "https://github.com/a63976659/ComfyUI-NodeCraft-AI",
            target: "_blank",
            text: "GitHub: a63976659/ComfyUI-NodeCraft-AI",
        });
        page.appendChild(ghLink);

        // 返回按钮
        const backBtn = el("button", { class: "nca-btn nca-about-back-btn", text: "← 返回" });
        backBtn.addEventListener("click", () => 切换介绍页(false));
        page.appendChild(backBtn);

        return page;
    }

    function 切换介绍页(show) {
        if (show) {
            refs.主内容区.style.display = "none";
            refs.介绍页.classList.add("visible");
        } else {
            refs.主内容区.style.display = "flex";
            refs.介绍页.classList.remove("visible");
        }
    }

    // ═══════════════════════════════════════════════════════════
    // 底部版本信息
    // ═══════════════════════════════════════════════════════════

    function 渲染底部版本栏() {
        return el("div", { class: "nca-footer-version" }, [
            el("span", { text: "NodeCraft AI v0.1.0 | MIT License" }),
        ]);
    }

    // ═══════════════════════════════════════════════════════════
    // 会话列表
    // ═══════════════════════════════════════════════════════════

    function 渲染会话列表区域() {
        const wrap = el("div", { class: "nca-sessions" });

        const toggle = el("button", { class: "nca-sessions-toggle" }, [
            el("span", { class: "arrow", text: "▼" }),
            el("span", { text: "会话" }),
        ]);
        refs.会话计数 = el("span", { class: "session-count", text: "0" });
        toggle.appendChild(refs.会话计数);
        toggle.addEventListener("click", () => wrap.classList.toggle("collapsed"));
        wrap.appendChild(toggle);

        refs.会话列表容器 = el("div", { class: "nca-session-list" });
        wrap.appendChild(refs.会话列表容器);

        const newBtn = el("button", { class: "nca-new-session-btn", text: "＋ 创建项目" });
        newBtn.addEventListener("click", () => 显示创建项目对话框());
        wrap.appendChild(newBtn);

        return wrap;
    }

    // ═══════════════════════════════════════════════════════════
    // 输入区域
    // ═══════════════════════════════════════════════════════════

    function 渲染模型切换栏() {
        const 切换栏 = el("div", { class: "nca-model-switcher" });

        // 本地/API 切换按钮组
        const 按钮组 = el("div", { class: "nca-switch-group" });
        const 本地按钮 = el("button", {
            class: `nca-switch-btn ${状态.模型来源 === "local" ? "active" : ""}`,
            text: "本地"
        });
        const API按钮 = el("button", {
            class: `nca-switch-btn ${状态.模型来源 === "api" ? "active" : ""}`,
            text: "API"
        });
        按钮组.appendChild(本地按钮);
        按钮组.appendChild(API按钮);
        切换栏.appendChild(按钮组);

        // 模型选择下拉（本地模式）
        const 本地下拉 = el("select", { class: "nca-model-select" });
        本地下拉.style.display = 状态.模型来源 === "local" ? "" : "none";

        // API 信息显示
        const API信息 = el("span", { class: "nca-api-info" });
        API信息.style.display = 状态.模型来源 === "api" ? "" : "none";

        切换栏.appendChild(本地下拉);
        切换栏.appendChild(API信息);

        // 更新本地模型下拉列表
        function 刷新本地下拉() {
            本地下拉.innerHTML = "";
            if (状态.本地模型列表.length === 0) {
                const opt = el("option", { value: "", text: "未检测到本地模型" });
                opt.disabled = true;
                本地下拉.appendChild(opt);
            } else {
                状态.本地模型列表.forEach(m => {
                    const opt = el("option", { value: m.name, text: m.name });
                    if (m.name === 状态.选中本地模型) opt.selected = true;
                    本地下拉.appendChild(opt);
                });
            }
        }

        // 更新 API 信息显示
        function 刷新API信息() {
            const platform = 推导平台名称(状态.设置.base_url);
            const model = 状态.设置.model_name || "未配置";
            API信息.textContent = `${platform} / ${model}`;
            API信息.title = `${platform} / ${model}`;
        }

        // 更新显示状态
        function 更新模型选择UI() {
            本地按钮.className = `nca-switch-btn ${状态.模型来源 === "local" ? "active" : ""}`;
            API按钮.className = `nca-switch-btn ${状态.模型来源 === "api" ? "active" : ""}`;
            本地下拉.style.display = 状态.模型来源 === "local" ? "" : "none";
            API信息.style.display = 状态.模型来源 === "api" ? "" : "none";
            刷新本地下拉();
            刷新API信息();
        }

        // 切换到本地
        本地按钮.addEventListener("click", async () => {
            状态.模型来源 = "local";
            状态.本地模型列表 = await 获取本地模型列表();
            更新模型选择UI();
            保存设置({ ...状态.设置, model_source: "local", local_model_name: 状态.选中本地模型 });
            更新状态栏("就绪");
        });

        // 切换到 API
        API按钮.addEventListener("click", () => {
            状态.模型来源 = "api";
            更新模型选择UI();
            保存设置({ ...状态.设置, model_source: "api" });
            更新状态栏("就绪");
        });

        // 本地模型下拉选择事件
        本地下拉.addEventListener("change", () => {
            状态.选中本地模型 = 本地下拉.value;
            保存设置({ ...状态.设置, local_model_name: 本地下拉.value });
            更新状态栏("就绪");
        });

        // API 信息点击进入设置
        API信息.addEventListener("click", () => 显示设置面板());
        API信息.style.cursor = "pointer";

        // 初始化显示
        更新模型选择UI();

        // 监听设置变更后刷新
        事件总线.on(事件.设置已加载, () => {
            状态.模型来源 = 状态.设置.model_source || "api";
            状态.选中本地模型 = 状态.设置.local_model_name || "";
            更新模型选择UI();
        });
        事件总线.on(事件.设置已保存, () => 更新模型选择UI());

        return 切换栏;
    }

    function 渲染输入区域() {
        const area = el("div", { class: "nca-input-area" });

        // 文件预览区
        refs.附件预览区 = el("div", { class: "nca-attachments-preview" });
        refs.附件预览区.style.display = "none";
        area.appendChild(refs.附件预览区);

        const wrapper = el("div", { class: "nca-input-wrapper" });

        // "+"按钮——文件选择
        refs.文件按钮 = el("button", { class: "nca-attach-btn", html: "+", title: "添加文件" });
        refs.文件输入 = el("input", {
            type: "file",
            multiple: "true",
            accept: "image/png,image/jpeg,image/gif,image/webp,.txt,.py,.js,.json,.md,.css,.html,.yaml,.yml,.toml,.cfg,.ini,.sh,.bat",
            style: { display: "none" },
        });
        refs.文件按钮.addEventListener("click", () => refs.文件输入.click());
        refs.文件输入.addEventListener("change", 处理文件选择);

        refs.输入框 = el("textarea", {
            rows: "1",
            placeholder: "描述你想创建的节点功能...",
        });

        refs.发送按钮 = el("button", { class: "nca-send-btn", html: "▶" });

        // 自适应高度
        refs.输入框.addEventListener("input", () => {
            refs.输入框.style.height = "auto";
            refs.输入框.style.height = Math.min(refs.输入框.scrollHeight, 100) + "px";
            更新发送按钮状态();
        });

        // 键盘事件
        refs.输入框.addEventListener("keydown", (e) => {
            if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                执行发送();
            }
        });

        refs.发送按钮.addEventListener("click", 执行发送);

        wrapper.appendChild(refs.文件按钮);
        wrapper.appendChild(refs.文件输入);
        wrapper.appendChild(refs.输入框);
        wrapper.appendChild(refs.发送按钮);
        area.appendChild(wrapper);
        return area;
    }

    // ─── 文件附件处理 ────────────────────────────────────────
    const 允许的图片类型 = ["image/png", "image/jpeg", "image/gif", "image/webp"];
    const 最大附件数 = 6;

    function 处理文件选择(e) {
        const files = Array.from(e.target.files || []);
        if (!files.length) return;

        const 当前数量 = 状态.待发送附件.length;
        if (当前数量 + files.length > 最大附件数) {
            显示提示(`最多只能添加 ${最大附件数} 个文件`);
            // 重置 input 以便重新选择
            refs.文件输入.value = "";
            return;
        }

        let 已处理 = 0;
        files.forEach(file => {
            const reader = new FileReader();
            reader.onload = (ev) => {
                状态.待发送附件.push({
                    name: file.name,
                    type: file.type || "application/octet-stream",
                    size: file.size,
                    data: ev.target.result,  // base64 data URL
                });
                已处理++;
                if (已处理 === files.length) {
                    渲染附件预览();
                    更新发送按钮状态();
                }
            };
            reader.readAsDataURL(file);
        });

        // 重置 input
        refs.文件输入.value = "";
    }

    function 移除附件(index) {
        状态.待发送附件.splice(index, 1);
        渲染附件预览();
        更新发送按钮状态();
    }

    function 渲染附件预览() {
        const container = refs.附件预览区;
        container.innerHTML = "";

        if (状态.待发送附件.length === 0) {
            container.style.display = "none";
            return;
        }

        container.style.display = "flex";

        状态.待发送附件.forEach((file, idx) => {
            const 是图片 = 允许的图片类型.includes(file.type);
            const card = el("div", { class: "nca-attach-card" });

            if (是图片) {
                const thumb = el("img", { class: "nca-attach-thumb", src: file.data });
                card.appendChild(thumb);
            } else {
                // 文件图标
                const ext = file.name.split(".").pop() || "file";
                const icon = el("div", { class: "nca-attach-icon", text: ext.toUpperCase() });
                card.appendChild(icon);
            }

            const name = el("span", { class: "nca-attach-name", text: file.name, title: file.name });
            card.appendChild(name);

            // 删除按钮
            const removeBtn = el("button", { class: "nca-attach-remove", text: "×" });
            removeBtn.addEventListener("click", () => 移除附件(idx));
            card.appendChild(removeBtn);

            container.appendChild(card);
        });
    }

    function 更新发送按钮状态() {
        const 有内容 = refs.输入框.value.trim().length > 0 || 状态.待发送附件.length > 0;
        refs.发送按钮.classList.toggle("active", 有内容);
    }

    // ═══════════════════════════════════════════════════════════
    // 状态栏
    // ═══════════════════════════════════════════════════════════

    function 渲染状态栏() {
        refs.状态栏 = el("div", { class: "nca-status-bar" }, [
            el("div", { class: "status-left" }, [
                el("span", { class: "status-dot" }),
                el("span", { class: "status-text", text: "就绪" }),
            ]),
            el("div", { class: "status-right" }, [
                el("span", { class: "status-model", text: "" }),
                el("span", { class: "status-separator", text: "•" }),
                el("span", { class: "status-session", text: "" }),
            ]),
        ]);
        return refs.状态栏;
    }

    // ═══════════════════════════════════════════════════════════
    // 交互逻辑
    // ═══════════════════════════════════════════════════════════

    async function 执行发送() {
        const text = refs.输入框.value.trim();
        const attachments = [...状态.待发送附件];
        if (!text && attachments.length === 0) return;
        if (状态.正在发送) return;

        refs.输入框.value = "";
        refs.输入框.style.height = "auto";
        refs.发送按钮.classList.remove("active");

        // 清空附件
        状态.待发送附件 = [];
        渲染附件预览();

        await 发送消息(text, attachments);
    }

    // ─── 欢迎页 ──────────────────────────────────────────────
    function 渲染欢迎页(container) {
        container.innerHTML = "";
        const welcome = el("div", { class: "nca-welcome" }, [
            el("div", { class: "nca-welcome-icon", html: LOGO_SVG }),
            el("h3", { text: "NODECRAFT AI" }),
            el("p", { text: "高级 ComfyUI 插件开发助手。选择已有项目或创建新项目开始。" }),
            el("div", { class: "nca-quick-actions" }, [
                创建快捷操作("⚡", "新建插件项目", () => 显示创建项目对话框()),
                创建快捷操作("◇", "帮我写一个图像缩放节点", () => 快捷输入("帮我写一个图像缩放节点")),
                创建快捷操作("▸", "如何开发 ComfyUI 节点？", () => 快捷输入("如何开发 ComfyUI 自定义节点？请给我一个完整的教程。")),
            ]),
        ]);
        container.appendChild(welcome);
    }

    function 创建快捷操作(icon, label, onClick) {
        const btn = el("button", { class: "nca-quick-action" }, [
            el("span", { class: "qa-icon", text: icon }),
            el("span", { text: label }),
        ]);
        btn.addEventListener("click", onClick);
        return btn;
    }

    function 快捷输入(text) {
        refs.输入框.value = text;
        refs.输入框.dispatchEvent(new Event("input"));
        refs.输入框.focus();
    }

    // ─── 消息渲染 ────────────────────────────────────────────
    function 渲染所有消息(messages) {
        refs.消息区域.innerHTML = "";
        if (!messages || messages.length === 0) {
            渲染欢迎页(refs.消息区域);
            return;
        }
        messages.forEach(msg => 追加消息DOM(msg));
        滚动到底部();
    }

    function 追加消息DOM(msg) {
        // 清除欢迎页
        if (refs.消息区域.querySelector(".nca-welcome")) {
            refs.消息区域.innerHTML = "";
        }

        const timeStr = 格式化时间(msg.timestamp);
        const roleLabel = msg.role === "user" ? "用户" : msg.role === "assistant" ? "AI" : "系统";

        const msgEl = el("div", { class: `nca-msg ${msg.role}` }, [
            el("div", { class: "nca-msg-header" }, [
                el("span", { class: "msg-role", text: `─ ${roleLabel}` }),
                timeStr ? el("span", { class: "msg-time", text: timeStr }) : null,
            ]),
            el("div", { class: "nca-msg-body", html: 简易Markdown渲染(msg.content) }),
        ]);

        refs.消息区域.appendChild(msgEl);

        // 绑定代码块复制按钮
        msgEl.querySelectorAll(".nca-code-copy").forEach(btn => {
            btn.addEventListener("click", () => {
                const pre = btn.closest("pre");
                const code = pre?.querySelector("code")?.textContent || "";
                navigator.clipboard.writeText(code).then(() => {
                    btn.classList.add("copied");
                    btn.textContent = "✓ 已复制";
                    setTimeout(() => {
                        btn.classList.remove("copied");
                        btn.textContent = "复制";
                    }, 1500);
                });
            });
        });

        滚动到底部();
    }

    function 显示加载动画() {
        const loader = el("div", { class: "nca-msg assistant", id: "nca-loader" }, [
            el("div", { class: "nca-msg-header" }, [
                el("span", { class: "msg-role", text: "─ AI" }),
            ]),
            el("div", { class: "nca-loading" }, [
                el("span", { class: "nca-loading-dot" }),
                el("span", { class: "nca-loading-dot" }),
                el("span", { class: "nca-loading-dot" }),
            ]),
        ]);
        refs.消息区域.appendChild(loader);
        滚动到底部();
    }

    function 移除加载动画() {
        const loader = refs.消息区域.querySelector("#nca-loader");
        if (loader) loader.remove();
    }

    function 滚动到底部() {
        requestAnimationFrame(() => {
            refs.消息区域.scrollTop = refs.消息区域.scrollHeight;
        });
    }

    // ─── 会话列表渲染 ────────────────────────────────────────
    function 渲染会话列表(sessions) {
        refs.会话列表容器.innerHTML = "";
        refs.会话计数.textContent = sessions?.length || 0;

        if (!sessions || sessions.length === 0) {
            refs.会话列表容器.appendChild(el("div", { class: "nca-session-empty", text: "— 暂无会话 —" }));
            return;
        }

        sessions.forEach(session => {
            const isActive = session.id === 状态.当前会话ID;
            const item = el("div", {
                class: `nca-session-item ${isActive ? "active" : ""}`,
            }, [
                el("span", { class: "session-title", text: session.title || "未命名", title: session.title }),
                el("span", { class: "session-time", text: 格式化时间(session.updated_at || session.created_at) }),
                (() => {
                    const btn = el("button", { class: "session-delete", text: "⊘", title: "删除" });
                    btn.addEventListener("click", (e) => {
                        e.stopPropagation();
                        显示删除确认(session);
                    });
                    return btn;
                })(),
            ]);
            item.addEventListener("click", () => 切换会话(session.id));
            refs.会话列表容器.appendChild(item);
        });
    }

    // ─── 状态栏更新 ─────────────────────────────────────────
    function 更新状态栏(text) {
        const statusText = refs.状态栏.querySelector(".status-text");
        const statusDot = refs.状态栏.querySelector(".status-dot");
        const modelText = refs.状态栏.querySelector(".status-model");
        const sessionText = refs.状态栏.querySelector(".status-session");

        if (statusText) statusText.textContent = text || "就绪";

        if (statusDot) {
            statusDot.classList.remove("busy", "error");
            if (状态.连接状态 === "busy") statusDot.classList.add("busy");
            else if (状态.连接状态 === "error") statusDot.classList.add("error");
        }

        if (modelText) modelText.textContent = `模型: ${获取当前模型名称()}`;

        const seq = 获取会话序号();
        if (sessionText) sessionText.textContent = seq ? `会话 #${seq}` : "";
    }

    // ═══════════════════════════════════════════════════════════
    // 设置面板（右侧滑入）
    // ═══════════════════════════════════════════════════════════

    async function 显示设置面板() {
        await 加载设置();
        const s = 状态.设置;

        const overlay = el("div", { class: "nca-overlay" });
        const panel = el("div", { class: "nca-panel" });

        // Header
        const closeBtn = el("button", { class: "nca-icon-btn", text: "✕" });
        closeBtn.addEventListener("click", () => overlay.remove());

        panel.appendChild(el("div", { class: "nca-panel-header" }, [
            el("h3", { text: "⚙ 设置" }),
            closeBtn,
        ]));

        // Body
        const body = el("div", { class: "nca-panel-body" });
        let 当前来源 = s.model_source || "api";

        // 模型来源切换
        body.appendChild(el("div", { class: "nca-form-section" }, [
            el("div", { class: "nca-form-label", text: "模型来源" }),
            (() => {
                const group = el("div", { class: "nca-radio-group" });
                const localBtn = el("button", { class: `nca-radio-btn ${当前来源 === "local" ? "selected" : ""}`, text: "本地" });
                const apiBtn = el("button", { class: `nca-radio-btn ${当前来源 === "api" ? "selected" : ""}`, text: "API" });

                localBtn.addEventListener("click", () => {
                    当前来源 = "local";
                    localBtn.classList.add("selected");
                    apiBtn.classList.remove("selected");
                    localSection.style.display = "block";
                    apiSection.style.display = "none";
                });
                apiBtn.addEventListener("click", () => {
                    当前来源 = "api";
                    apiBtn.classList.add("selected");
                    localBtn.classList.remove("selected");
                    localSection.style.display = "none";
                    apiSection.style.display = "block";
                });
                group.appendChild(localBtn);
                group.appendChild(apiBtn);
                return group;
            })(),
        ]));

        // 本地模型
        const localPathInput = el("input", { type: "text", value: s.local_path || "", placeholder: "ComfyUI/models/LLM" });
        const localSection = el("div", { class: "nca-form-section", style: { display: 当前来源 === "local" ? "block" : "none" } }, [
            el("div", { class: "nca-form-label", text: "本地模型" }),
            el("div", { class: "nca-field" }, [
                el("label", { text: "模型路径" }),
                localPathInput,
            ]),
            (() => {
                const btn = el("button", { class: "nca-btn nca-btn-sm nca-btn-ghost", text: "↺ 默认路径" });
                btn.addEventListener("click", () => { localPathInput.value = "models/LLM"; });
                return btn;
            })(),
        ]);
        body.appendChild(localSection);

        // API 模型
        const baseUrlInput = el("input", { type: "text", value: s.base_url || "", placeholder: "https://api.openai.com/v1" });
        const modelNameInput = el("input", { type: "text", value: s.model_name || "", placeholder: "qwen2.5-coder-32b" });
        const apiKeyInput = el("input", { type: "password", value: s.api_key || "", placeholder: "sk-..." });
        const apiSection = el("div", { class: "nca-form-section", style: { display: 当前来源 === "api" ? "block" : "none" } }, [
            el("div", { class: "nca-form-label", text: "API 配置" }),
            el("div", { class: "nca-field" }, [el("label", { text: "接口地址" }), baseUrlInput]),
            el("div", { class: "nca-field" }, [el("label", { text: "模型" }), modelNameInput]),
            el("div", { class: "nca-field" }, [el("label", { text: "API 密钥" }), apiKeyInput]),
        ]);
        body.appendChild(apiSection);

        // 生成参数
        const tempInput = el("input", { type: "number", value: String(s.temperature ?? 0.2), step: "0.1", min: "0", max: "2" });
        const maxTokensInput = el("input", { type: "number", value: String(s.max_tokens ?? 4096), step: "256", min: "256" });
        body.appendChild(el("div", { class: "nca-form-section" }, [
            el("div", { class: "nca-form-label", text: "生成参数" }),
            el("div", { class: "nca-field-row" }, [
                el("div", { class: "nca-field" }, [el("label", { text: "温度" }), tempInput]),
                el("div", { class: "nca-field" }, [el("label", { text: "最大令牌" }), maxTokensInput]),
            ]),
        ]));

        // 保存按钮
        const saveBtn = el("button", { class: "nca-btn nca-btn-primary", text: "保存设置" });
        saveBtn.addEventListener("click", async () => {
            saveBtn.textContent = "保存中...";
            saveBtn.disabled = true;
            const success = await 保存设置({
                model_source: 当前来源,
                local_path: localPathInput.value,
                base_url: baseUrlInput.value,
                model_name: modelNameInput.value,
                api_key: apiKeyInput.value,
                temperature: parseFloat(tempInput.value) || 0.2,
                max_tokens: parseInt(maxTokensInput.value) || 4096,
            });
            if (success) {
                显示提示("✓ 设置已保存");
                overlay.remove();
                更新状态栏("就绪");
            } else {
                saveBtn.textContent = "重试";
                saveBtn.disabled = false;
            }
        });
        body.appendChild(saveBtn);

        panel.appendChild(body);
        overlay.appendChild(panel);
        overlay.addEventListener("click", (e) => { if (e.target === overlay) overlay.remove(); });
        根容器.appendChild(overlay);
    }

    // ═══════════════════════════════════════════════════════════
    // 创建项目对话框（居中弹窗）
    // ═══════════════════════════════════════════════════════════

    function 显示创建项目对话框() {
        const overlay = el("div", { class: "nca-overlay center-modal" });
        const modal = el("div", { class: "nca-modal" });

        const closeBtn = el("button", { class: "nca-icon-btn", text: "✕" });
        closeBtn.addEventListener("click", () => overlay.remove());

        modal.appendChild(el("div", { class: "nca-modal-header" }, [
            el("h3", { text: "⚡ 新建项目" }),
            closeBtn,
        ]));

        const body = el("div", { class: "nca-modal-body" });
        const nameInput = el("input", { type: "text", placeholder: "my-custom-node" });
        const preview = el("div", { class: "nca-create-preview", text: "ComfyUI/custom_nodes/..." });
        const errorMsg = el("div", { class: "nca-error-text", style: { display: "none" } });

        const validPattern = /^[a-zA-Z0-9_-]+$/;

        nameInput.addEventListener("input", () => {
            const val = nameInput.value;
            if (!val) {
                preview.textContent = "ComfyUI/custom_nodes/...";
                preview.classList.remove("valid");
                errorMsg.style.display = "none";
                nameInput.classList.remove("nca-input-error", "nca-input-valid");
            } else if (!validPattern.test(val)) {
                nameInput.classList.add("nca-input-error");
                nameInput.classList.remove("nca-input-valid");
                errorMsg.textContent = "仅允许英文字母、数字、下划线、短横线";
                errorMsg.style.display = "block";
                preview.textContent = "";
                preview.classList.remove("valid");
            } else {
                nameInput.classList.remove("nca-input-error");
                nameInput.classList.add("nca-input-valid");
                errorMsg.style.display = "none";
                preview.textContent = `ComfyUI/custom_nodes/${val}/`;
                preview.classList.add("valid");
            }
        });

        body.appendChild(el("div", { class: "nca-field" }, [
            el("label", { text: "插件名称" }),
            nameInput,
            errorMsg,
        ]));
        body.appendChild(preview);

        const createBtn = el("button", { class: "nca-btn nca-btn-primary", text: "创建" });
        createBtn.addEventListener("click", async () => {
            const name = nameInput.value.trim();
            if (!name || !validPattern.test(name)) {
                nameInput.classList.add("nca-input-error");
                errorMsg.textContent = "请输入合法的插件名称";
                errorMsg.style.display = "block";
                return;
            }
            createBtn.textContent = "创建中...";
            createBtn.disabled = true;

            const result = await 创建插件文件夹(name);
            if (result.success) {
                显示提示("✓ 项目已创建");
                overlay.remove();
                await 创建会话(name, name);
            } else {
                createBtn.textContent = "创建";
                createBtn.disabled = false;
                errorMsg.textContent = result.message || "创建失败";
                errorMsg.style.display = "block";
            }
        });
        body.appendChild(createBtn);

        modal.appendChild(body);
        overlay.appendChild(modal);
        overlay.addEventListener("click", (e) => { if (e.target === overlay) overlay.remove(); });
        根容器.appendChild(overlay);

        nameInput.focus();
    }

    // ═══════════════════════════════════════════════════════════
    // 删除确认
    // ═══════════════════════════════════════════════════════════

    function 显示删除确认(session) {
        const overlay = el("div", { class: "nca-overlay center-modal" });
        const modal = el("div", { class: "nca-modal" });

        const closeBtn = el("button", { class: "nca-icon-btn", text: "✕" });
        closeBtn.addEventListener("click", () => overlay.remove());

        modal.appendChild(el("div", { class: "nca-modal-header" }, [
            el("h3", { text: "⚠ 确认" }),
            closeBtn,
        ]));

        const body = el("div", { class: "nca-modal-body" });
        body.appendChild(el("p", {
            text: `确定删除「${session.title || "未命名"}」？此操作不可恢复。`,
            style: { margin: "0", fontSize: "12px", color: "var(--nca-fg-dim)", lineHeight: "1.6" },
        }));

        const actions = el("div", { class: "nca-confirm-actions" });
        const cancelBtn = el("button", { class: "nca-btn nca-btn-sm", text: "取消" });
        cancelBtn.addEventListener("click", () => overlay.remove());

        const deleteBtn = el("button", { class: "nca-btn nca-btn-sm nca-btn-danger", text: "删除" });
        deleteBtn.addEventListener("click", async () => {
            deleteBtn.textContent = "...";
            await 删除会话(session.id);
            overlay.remove();
            显示提示("会话已删除");
        });

        actions.appendChild(cancelBtn);
        actions.appendChild(deleteBtn);
        body.appendChild(actions);

        modal.appendChild(body);
        overlay.appendChild(modal);
        overlay.addEventListener("click", (e) => { if (e.target === overlay) overlay.remove(); });
        根容器.appendChild(overlay);
    }

    // ─── 提示条 ─────────────────────────────────────────────
    function 显示提示(text) {
        const toast = el("div", { class: "nca-toast", text });
        根容器.appendChild(toast);
        setTimeout(() => toast.remove(), 2500);
    }

    // ═══════════════════════════════════════════════════════════
    // 事件绑定
    // ═══════════════════════════════════════════════════════════

    function 绑定全局事件() {
        事件总线.on(事件.会话列表更新, (sessions) => 渲染会话列表(sessions));
        事件总线.on(事件.会话切换, () => {
            渲染会话列表(状态.会话列表);
            更新状态栏("就绪");
        });
        事件总线.on(事件.消息列表更新, (messages) => 渲染所有消息(messages));
        事件总线.on(事件.新消息追加, (msg) => {
            移除加载动画();
            追加消息DOM(msg);
        });
        事件总线.on(事件.发送状态变更, (isSending) => {
            refs.输入框.disabled = isSending;
            refs.发送按钮.disabled = isSending;
            if (isSending) 显示加载动画();
        });
        事件总线.on(事件.状态栏更新, (text) => 更新状态栏(text));
        事件总线.on(事件.连接状态变更, () => 更新状态栏());
        事件总线.on(事件.设置已保存, () => 更新状态栏("就绪"));
    }

    // ─── 初始化 ─────────────────────────────────────────────
    async function 初始化() {
        await Promise.all([获取会话列表(), 加载设置()]);
        更新状态栏("就绪");
    }
}
