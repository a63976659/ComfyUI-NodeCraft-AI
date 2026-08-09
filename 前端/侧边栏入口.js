// ═══════════════════════════════════════════════════════════════
// 侧边栏入口.js — ComfyUI 扩展注册点
// NodeCraft AI — Luxury Terminal Edition
// ═══════════════════════════════════════════════════════════════

import { app } from "../../scripts/app.js";
import { renderSidebarUI } from "./会话视图管理器.js";
import { 迁移旧数据 } from "./存储引擎.js";
import { t } from "./i18n.js";
import { 加载Marked库 } from "./工具函数.js";
import { preconnectWebSocket } from "./流式聊天管理器.js";

// 版本信标：浏览器端读 window.__NCA_BUILD 即可确定执行的是否为最新模块
window.__NCA_BUILD = "r11-20260809-1500";
console.log("[NodeCraft] build", window.__NCA_BUILD);

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

        // 延迟预连接 WebSocket
        setTimeout(preconnectWebSocket, 500);

        app.extensionManager.registerSidebarTab({
            id: "nodecraft-ai-sidebar",
            order: 10, // 侧边栏排序规则：数字小的排前面（见 侧边栏排序器.js）
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

// ── 侧边栏 Tab 排序规则（各插件入口文件内嵌同一段代码，全局标记保证只执行一次） ──
// 规则：registerSidebarTab 声明 order 数字，小的排前面；未声明的（含官方 Tab）一律不动
// 实现：每秒轮询一次（最长 30 秒），等声明 order 的 Tab 集合连续两轮稳定后重排，兼容插件多时的慢加载
if (!window.__comfySidebarTabSorterInstalled) {
    window.__comfySidebarTabSorterInstalled = true;
    console.debug("[侧边栏排序] 规则已安装，开始轮询等待各插件 Tab 注册…");
    let 上次指纹 = null;
    let 已耗时 = 0;
    const 轮询间隔 = 1000;
    const 定时器 = setInterval(() => {
        已耗时 += 轮询间隔;
        if (已耗时 > 30000) {
            clearInterval(定时器);
            console.debug("[侧边栏排序] 轮询结束（30秒）");
            return;
        }
        const 管理器 = app.extensionManager;
        if (!管理器?.getSidebarTabs || !管理器.unregisterSidebarTab || !管理器.registerSidebarTab) return;
        const 全部 = 管理器.getSidebarTabs();
        if (!Array.isArray(全部)) return;
        const 参与者 = 全部.filter((t) => typeof t?.order === "number");
        // 集合有变化说明还有插件在陆续注册，等下一轮稳定后再排
        const 指纹 = 参与者.map((t) => t.id).sort().join();
        if (指纹 !== 上次指纹) {
            上次指纹 = 指纹;
            return;
        }
        if (参与者.length < 2) return;
        // 若用户已点开某个参与排序的 Tab，本轮跳过，避免注销激活面板
        const 状态 = 管理器.sidebarTab?.value ?? 管理器.sidebarTab;
        if (状态?.activeSidebarTabId && 参与者.some((t) => t.id === 状态.activeSidebarTabId)) return;
        const 排序后 = [...参与者].sort((a, b) => a.order - b.order || String(a.id).localeCompare(String(b.id)));
        const 已有序 = 参与者.map((t) => t.id).join() === 排序后.map((t) => t.id).join()
            && 全部.slice(-参与者.length).every((t) => typeof t?.order === "number");
        if (已有序) return;
        for (const t of 排序后) 管理器.unregisterSidebarTab(t.id);
        for (const t of 排序后) 管理器.registerSidebarTab(t);
        console.debug("[侧边栏排序] 已按 order 固定 Tab 顺序:", 排序后.map((t) => `${t.id}(${t.order})`));
    }, 轮询间隔);
}
