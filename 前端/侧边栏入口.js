// ═══════════════════════════════════════════════════════════════
// 侧边栏入口.js — ComfyUI 扩展注册点
// NodeCraft AI — Luxury Terminal Edition
// ═══════════════════════════════════════════════════════════════

import { app } from "../../scripts/app.js";
import { renderSidebarUI } from "./会话视图管理器.js";
import { 迁移旧数据 } from "./存储引擎.js";
import { t } from "./i18n.js";
import { 加载Marked库 } from "./工具函数.js";

app.registerExtension({
    name: "com.nodecraft.ai",
    async setup() {
        // 首次运行：将 localStorage 中 NodeCraftAI_ 前缀键迁移至 IndexedDB（失败自动降级）
        迁移旧数据().catch(() => {});

        // 异步加载非关键CSS（不阻塞首屏渲染）
        const 非关键样式 = [
            '关于页面.css', '文件夹选择器.css', '可视化面板.css',
            'GitHub同步.css', '监控与警告.css', '登录面板.css'
        ];
        非关键样式.forEach(name => {
            const link = document.createElement('link');
            link.rel = 'stylesheet';
            link.href = new URL(`./样式/${name}`, import.meta.url).href;
            document.head.appendChild(link);
        });

        // 预加载 marked.js（不阻塞渲染）
        加载Marked库().catch(() => {});

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
