// ═══════════════════════════════════════════════════════════════
// 可视化/HUD覆盖层.js — 可视化容器 HUD 元素（搜索/标题/统计/全屏/图例/过滤/详情/Tooltip）
// NodeCraft AI — Luxury Terminal / Neo-Noir Hacker
// 从 可视化面板.js 拆分：HUD 覆盖层 DOM 构建与统计更新
// ═══════════════════════════════════════════════════════════════

import { el, createDebounce } from "../工具函数.js";
import { t } from "../i18n.js";

// 面板可能随 renderSidebarUI 重建（如语言切换）而多次构建；模块级退订句柄
// 保证 document 级全屏监听不随重建累积泄漏
let _上次全屏监听退订 = null;

/**
 * 构建 HUD 覆盖层元素并挂载到可视化容器
 * @param {HTMLElement} vizContainer - 可视化容器
 * @param {Function} getGraph - 获取当前图实例
 * @returns {Object} HUD 元素引用与统计更新函数
 */
export function 创建HUD覆盖层(vizContainer, getGraph) {
    // ─── HUD 覆盖层元素 ──────────────────────────────────────
    // 搜索框（左上角）
    const hudSearch = el("div", { class: "nc-viz-hud-search" });
    const hudSearchIcon = el("span", { class: "nc-viz-hud-search-icon", html: "🔍" });
    const hudSearchInput = el("input", { type: "text", placeholder: t("visual.hud_search_ph") });
    hudSearch.appendChild(hudSearchIcon);
    hudSearch.appendChild(hudSearchInput);
    vizContainer.appendChild(hudSearch);

    // 标题（顶部居中）
    const hudTitle = el("div", {
        class: "nc-viz-hud-title",
        html: `<h1>${t("visual.hud_title")}</h1><div class="nc-viz-hud-subtitle">STRUCTURAL DIAGNOSTICS</div>`,
    });
    vizContainer.appendChild(hudTitle);

    // 统计栏（右上角）
    const hudStats = el("div", { class: "nc-viz-hud-stats" });
    const statTotal = el("div", { class: "nc-viz-hud-stat", html: `<span class="nc-viz-hud-dot c"></span><span>${t("visual.hud_nodes")}</span> <strong>0</strong>` });
    const statNormal = el("div", { class: "nc-viz-hud-stat", html: `<span class="nc-viz-hud-dot g"></span><span>${t("visual.status_normal")}</span> <strong>0</strong>` });
    const statError = el("div", { class: "nc-viz-hud-stat", html: `<span class="nc-viz-hud-dot r"></span><span>${t("visual.status_error")}</span> <strong>0</strong>` });
    const statWarning = el("div", { class: "nc-viz-hud-stat", html: `<span class="nc-viz-hud-dot a"></span><span>${t("visual.status_warning")}</span> <strong>0</strong>` });
    hudStats.appendChild(statTotal);
    hudStats.appendChild(statNormal);
    hudStats.appendChild(statError);
    hudStats.appendChild(statWarning);
    vizContainer.appendChild(hudStats);

    // 全屏按钮（右上角统计栏下方）
    const hudFullscreen = el("button", {
        type: "button",
        class: "nc-viz-hud-fullscreen",
        html: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 8V4h6M20 8V4h-6M4 16v4h6M20 16v4h-6"/></svg>`,
        title: t("visual.fullscreen"),
    });
    hudFullscreen.addEventListener("click", () => {
        if (document.fullscreenElement === vizContainer) {
            document.exitFullscreen();
        } else {
            vizContainer.requestFullscreen();
        }
    });
    // 面板重建时先退订旧实例的 document 级全屏监听，避免重复注册
    if (_上次全屏监听退订) { try { _上次全屏监听退订(); } catch (_) {} }
    const _onFullscreenChange = () => {
        if (document.fullscreenElement === vizContainer) {
            hudFullscreen.classList.add("active");
            hudFullscreen.title = t("visual.exit_fullscreen");
            hudFullscreen.innerHTML = `<span style="font-size:20px;line-height:1">&times;</span>`;
        } else {
            hudFullscreen.classList.remove("active");
            hudFullscreen.title = t("visual.fullscreen");
            hudFullscreen.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 8V4h6M20 8V4h-6M4 16v4h6M20 16v4h-6"/></svg>`;
        }
    };
    document.addEventListener("fullscreenchange", _onFullscreenChange);
    _上次全屏监听退订 = () => document.removeEventListener("fullscreenchange", _onFullscreenChange);
    vizContainer.appendChild(hudFullscreen);

    // 图例（左下角）
    const hudLegend = el("div", {
        class: "nc-viz-hud-legend",
        html: `
            <h3>${t("visual.legend")}</h3>
            <div class="nc-viz-hud-legend-item"><span class="nc-viz-hud-legend-line green"></span>${t("visual.legend_link_ok")}</div>
            <div class="nc-viz-hud-legend-item"><span class="nc-viz-hud-legend-line red"></span>${t("visual.legend_link_err")}</div>
            <div class="nc-viz-hud-legend-item"><span class="nc-viz-hud-legend-node ok"></span>${t("visual.legend_node_ok")}</div>
            <div class="nc-viz-hud-legend-item"><span class="nc-viz-hud-legend-node err"></span>${t("visual.legend_node_err")}</div>
            <div class="nc-viz-hud-legend-item"><span class="nc-viz-hud-legend-node warn"></span>${t("visual.legend_node_warn")}</div>
        `,
    });
    vizContainer.appendChild(hudLegend);

    // 过滤按钮（底部居中）
    const hudFilters = el("div", { class: "nc-viz-hud-filters" });
    const filterButtons = [
        { label: t("visual.filter_all"), status: "all" },
        { label: t("visual.status_error"), status: "error" },
        { label: t("visual.status_warning"), status: "warning" },
        { label: t("visual.status_normal"), status: "normal" },
    ];
    let 当前过滤 = "all";
    filterButtons.forEach(cfg => {
        const btn = el("button", { type: "button", html: cfg.label });
        if (cfg.status === "all") btn.classList.add("active");
        btn.addEventListener("click", () => {
            当前过滤 = cfg.status;
            hudFilters.querySelectorAll("button").forEach(b => b.classList.remove("active"));
            btn.classList.add("active");
            const g = getGraph();
            if (g && typeof g.filterNodes === "function") {
                g.filterNodes(cfg.status);
            }
        });
        hudFilters.appendChild(btn);
    });
    vizContainer.appendChild(hudFilters);

    // 操作提示（右下角）
    const hudControls = el("div", {
        class: "nc-viz-hud-controls",
        html: `
            <div><kbd>${t("visual.ctrl_drag_left")}</kbd> ${t("visual.ctrl_rotate")}</div>
            <div><kbd>${t("visual.ctrl_wheel")}</kbd> ${t("visual.ctrl_zoom")}</div>
            <div><kbd>${t("visual.ctrl_drag_right")}</kbd> ${t("visual.ctrl_pan")}</div>
            <div><kbd>${t("visual.ctrl_click_node")}</kbd> ${t("visual.ctrl_detail")}</div>
        `,
    });
    vizContainer.appendChild(hudControls);

    // HUD 角标装饰
    const hudCorners = [];
    ['tl','tr','bl','br'].forEach(pos => {
        const corner = el("div", { class: `nc-viz-hud-corner ${pos}` });
        vizContainer.appendChild(corner);
        hudCorners.push(corner);
    });

    // 节点详情面板（右侧滑出）
    const detailPanel = el("div", { class: "nc-viz-detail-panel" });
    detailPanel.innerHTML = `
        <button class="nc-viz-detail-close">×</button>
        <div class="nc-viz-detail-name"></div>
        <div class="nc-viz-detail-type"></div>
        <div class="nc-viz-detail-status normal">${t("visual.status_normal")}</div>
        <div class="nc-viz-detail-desc"></div>
        <div class="nc-viz-detail-error-section" style="display:none">
            <div class="nc-viz-detail-error-title">${t("visual.detail_error_title")}</div>
            <div class="nc-viz-detail-error-content"></div>
        </div>
        <div class="nc-viz-detail-warning-section" style="display:none">
            <div class="nc-viz-detail-warning-title">${t("visual.detail_warning_title")}</div>
            <div class="nc-viz-detail-warning-content"></div>
        </div>
        <div class="nc-viz-detail-conn-title">${t("visual.detail_conn_title")}</div>
        <ul class="nc-viz-detail-conn"></ul>
    `;
    vizContainer.appendChild(detailPanel);
    detailPanel.querySelector('.nc-viz-detail-close').addEventListener('click', () => {
        detailPanel.classList.remove('open');
    });

    // Tooltip（鼠标悬停）
    const tooltip = el("div", { class: "nc-viz-tooltip" });
    vizContainer.appendChild(tooltip);

    // HUD 统计更新函数（数字动画）
    function 更新HUD统计(graph) {
        if (!graph || typeof graph.getStats !== "function") return;
        const s = graph.getStats();
        动画数字(statTotal.querySelector("strong"), s.total);
        动画数字(statNormal.querySelector("strong"), s.normal);
        动画数字(statError.querySelector("strong"), s.error);
        动画数字(statWarning.querySelector("strong"), s.warning);
    }

    function 动画数字(el, target) {
        if (el._animTimer) clearInterval(el._animTimer);
        const steps = 20;
        const interval = 30;
        let step = 0;
        el.textContent = '0';
        el._animTimer = setInterval(() => {
            step++;
            el.textContent = Math.round(target * (step / steps));
            if (step >= steps) {
                el.textContent = target;
                clearInterval(el._animTimer);
                el._animTimer = null;
            }
        }, interval);
    }

    // 搜索事件（防抖300ms）
    const _防抖搜索 = createDebounce(() => {
        const g = getGraph();
        if (g && typeof g.searchNodes === "function") {
            g.searchNodes(hudSearchInput.value);
        }
    }, 300);
    hudSearchInput.addEventListener("input", _防抖搜索);

    return {
        hudSearch, hudTitle, hudStats,
        statTotal, statNormal, statError, statWarning,
        hudFullscreen, hudLegend, hudFilters, hudControls, hudCorners,
        detailPanel, tooltip,
        更新HUD统计,
    };
}
