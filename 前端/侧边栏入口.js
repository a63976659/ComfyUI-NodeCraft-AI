// ═══════════════════════════════════════════════════════════════
// 侧边栏入口.js — ComfyUI 扩展注册点
// NodeCraft AI — Luxury Terminal Edition
// ═══════════════════════════════════════════════════════════════

import { app } from "../../scripts/app.js";
import { renderSidebarUI } from "./会话视图管理器.js";

app.registerExtension({
    name: "com.nodecraft.ai",
    async setup() {
        app.extensionManager.registerSidebarTab({
            id: "nodecraft-ai-sidebar",
            icon: "pi pi-code",
            title: "NodeCraft AI",
            tooltip: "AI 编程助手 — 快速创建 ComfyUI 插件",
            type: "custom",
            render: (container) => {
                renderSidebarUI(container);
            },
        });
    },
});
