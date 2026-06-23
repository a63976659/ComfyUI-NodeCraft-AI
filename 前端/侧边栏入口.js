// ═══════════════════════════════════════════════════════════════
// 侧边栏入口.js — ComfyUI 扩展注册点
// NodeCraft AI — Luxury Terminal Edition
// ═══════════════════════════════════════════════════════════════

import { app } from "../../scripts/app.js";
import { renderSidebarUI } from "./会话视图管理器.js";
import { 迁移旧数据 } from "./存储引擎.js";
import { t } from "./i18n.js";

app.registerExtension({
    name: "com.nodecraft.ai",
    async setup() {
        // 首次运行：将 localStorage 中 NodeCraftAI_ 前缀键迁移至 IndexedDB（失败自动降级）
        迁移旧数据().catch(() => {});

        app.extensionManager.registerSidebarTab({
            id: "nodecraft-ai-sidebar",
            icon: "pi pi-code",
            title: t("brand.name"),
            tooltip: t("brand.tooltip"),
            type: "custom",
            render: (container) => {
                renderSidebarUI(container);
            },
        });
    },
});
