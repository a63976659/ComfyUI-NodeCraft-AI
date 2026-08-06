// ═══════════════════════════════════════════════════════════════
// 可视化演示.js — 演示数据与演示模式控制
// NodeCraft AI — Luxury Terminal / Neo-Noir Hacker
// ═══════════════════════════════════════════════════════════════

import { el } from "./工具函数.js";
import { t } from "./i18n.js";

// ─── 演示数据（含 normal/error/warning 三种状态） ──────────
export const 演示数据 = {
    nodes: [
        { id: "root", name: "我的插件", type: "root", size: 0, status: "normal", group: "核心" },
        { id: "init", name: "__init__.py", type: "entry", size: 2048, status: "normal", group: "核心" },
        { id: "nodes", name: "nodes/", type: "directory", size: 0, status: "normal", group: "节点" },
        { id: "node_img", name: "图片处理.py", type: "script", size: 5120, status: "normal", group: "节点" },
        { id: "node_text", name: "文本生成.py", type: "script", size: 3072, status: "warning", group: "节点" },
        { id: "node_filter", name: "滤镜节点.py", type: "script", size: 1024, status: "error", group: "节点" },
        { id: "utils", name: "utils/", type: "directory", size: 0, status: "normal", group: "工具" },
        { id: "util_io", name: "文件读写.py", type: "script", size: 2560, status: "normal", group: "工具" },
        { id: "util_cache", name: "缓存管理.py", type: "script", size: 1536, status: "warning", group: "工具" },
        { id: "config", name: "config.json", type: "config", size: 512, status: "normal", group: "配置" },
        { id: "readme", name: "README.md", type: "doc", size: 4096, status: "normal", group: "文档" },
        { id: "web", name: "web/", type: "directory", size: 0, status: "normal", group: "前端" },
        { id: "js_main", name: "主界面.js", type: "web", size: 8192, status: "normal", group: "前端" },
        { id: "css", name: "样式.css", type: "style", size: 2048, status: "error", group: "前端" },
    ],
    links: [
        { source: "root", target: "init", type: "containment", status: "normal" },
        { source: "root", target: "nodes", type: "containment", status: "normal" },
        { source: "root", target: "utils", type: "containment", status: "normal" },
        { source: "root", target: "config", type: "containment", status: "normal" },
        { source: "root", target: "readme", type: "containment", status: "normal" },
        { source: "root", target: "web", type: "containment", status: "normal" },
        { source: "nodes", target: "node_img", type: "containment", status: "normal" },
        { source: "nodes", target: "node_text", type: "containment", status: "normal" },
        { source: "nodes", target: "node_filter", type: "containment", status: "normal" },
        { source: "utils", target: "util_io", type: "containment", status: "normal" },
        { source: "utils", target: "util_cache", type: "containment", status: "normal" },
        { source: "web", target: "js_main", type: "containment", status: "normal" },
        { source: "web", target: "css", type: "containment", status: "normal" },
        { source: "node_img", target: "util_io", type: "dependency", status: "normal" },
        { source: "node_text", target: "util_io", type: "dependency", status: "normal" },
        { source: "node_filter", target: "util_cache", type: "dependency", status: "normal" },
        { source: "init", target: "node_img", type: "dependency", status: "normal" },
        { source: "init", target: "node_text", type: "dependency", status: "normal" },
        { source: "js_main", target: "css", type: "dependency", status: "normal" },
    ],
};

// ─── 演示模式标识 ────────────────────────────────────────────
let _演示模式中 = false;

export function 获取演示模式标识() { return _演示模式中; }
export function 设置演示模式标识(v) { _演示模式中 = v; }

// ─── 演示标签管理 ────────────────────────────────────────────
let _演示标签El = null;

export function 显示演示标签(vizContainer) {
    if (_演示标签El) return;
    _演示标签El = el("div", {
        class: "nca-viz-demo-badge",
        style: {
            position: "absolute", top: "8px", left: "50%", transform: "translateX(-50%)",
            zIndex: "10", padding: "5px 14px", borderRadius: "6px",
            fontSize: "11px", whiteSpace: "nowrap",
            backdropFilter: "blur(6px)", pointerEvents: "none",
        },
        html: t("visual.demo_badge"),
    });
    vizContainer.appendChild(_演示标签El);
}

export function 隐藏演示标签() {
    if (_演示标签El) {
        _演示标签El.remove();
        _演示标签El = null;
    }
}

// ─── 加载演示可视化 ──────────────────────────────────────────
/**
 * 加载演示数据到图形中
 * @param {Object} deps - 依赖注入
 * @param {Function} deps.渲染图形 - 渲染图形函数
 * @param {HTMLElement} deps.vizStatus - 状态栏元素
 * @param {Function} deps.显示分析引导 - 显示引导提示函数
 */
export async function 加载演示可视化({ 渲染图形, vizStatus, 显示分析引导 }) {
    if (_演示模式中) return;
    _演示模式中 = true;
    try {
        await 渲染图形(演示数据, 'file');
        vizStatus.textContent = t('visual.demo_mode');
    } catch (e) {
        console.warn('[可视化面板] 演示数据加载失败:', e);
        显示分析引导();
    }
    _演示模式中 = false;
}
