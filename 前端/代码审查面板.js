// ═══════════════════════════════════════════════════════════════
// 代码审查面板.js — AI 驱动的代码审查功能
// NodeCraft AI — Luxury Terminal / Neo-Noir Hacker
// ═══════════════════════════════════════════════════════════════

import { el, NCA_API_BASE, Toast, NCA_STORAGE_KEYS, _转义HTML, 标记禁止翻译 } from "./工具函数.js";
import { 显示知识库通知 } from "./知识库通知条.js";

// ─── 常量 ────────────────────────────────────────────────────────
const 审查深度标签 = { quick: "快速", standard: "标准", deep: "深度" };

// 审查深度选项（共享常量，供下拉菜单和弹窗复用）
export const 审查深度选项 = [
    { value: "quick", label: "快速审查（核心文件）" },
    { value: "standard", label: "标准审查（所有 Python + 前端 JS）" },
    { value: "deep", label: "深度审查（Python + JS）" },
];
const 严重程度图标 = { high: "🔴", medium: "🟡", low: "🟢" };
const 严重程度文字 = { high: "高", medium: "中", low: "低" };
const 分类标签 = {
    security: "安全性",
    performance: "性能",
    style: "代码规范",
    comfyui: "ComfyUI规范",
    maintenance: "可维护性",
    frontend_layout: "前端布局",
};

// ═══════════════════════════════════════════════════════════════
//  纯函数导出（不涉及弹窗DOM，可被优化面板等外部模块复用）
// ═══════════════════════════════════════════════════════════════

/**
 * 执行代码审查请求（纯 API 调用，不创建任何 DOM）
 * @param {string} pluginPath - 插件路径
 * @param {string} depth - 审查深度：'quick'|'standard'|'deep'
 * @returns {Promise<Object>} 审查结果对象（review data）
 */
export async function 执行代码审查(pluginPath, depth) {
    const resp = await fetch(`${NCA_API_BASE}/code-review`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            plugin_path: pluginPath,
            review_depth: depth || "standard",
        }),
    });

    if (!resp.ok) {
        let errorMsg = `HTTP ${resp.status}`;
        try {
            const errData = await resp.json();
            if (errData.error) errorMsg = errData.error;
        } catch (_) {}
        throw new Error(errorMsg);
    }

    const data = await resp.json();
    if (!data.success) {
        throw new Error(data.error || "审查失败");
    }

    // 知识库检索降级：后端下发提醒时，在侧边栏常驻通知条展示（文案由后端提供）
    if (data.review && data.review.kb_retrieval_status === 'degraded' && data.review.kb_message) {
        显示知识库通知(data.review.kb_message);
    }

    return data.review;
}

/**
 * 渲染审查结果到指定容器（纯渲染函数，不耦合弹窗逻辑）
 * @param {Object} review - 审查结果对象
 * @param {HTMLElement} container - 目标 DOM 容器
 */
export function 渲染审查结果(review, container) {
    container.innerHTML = "";

    // ─── 评分卡片 ────────────────────────────────
    const score = review.score || 0;
    const scoreLevel = score >= 90 ? "excellent" : score >= 70 ? "good" : score >= 60 ? "pass" : "fail";
    const scoreText = score >= 90 ? "优秀" : score >= 70 ? "良好" : score >= 60 ? "及格" : "不合格";

    const scoreCard = el("div", { class: `nca-review-score-card nca-review-score-${scoreLevel}` });
    const scoreNum = el("div", { class: "nca-review-score-num", text: String(score) });
    const scoreLabel = el("div", { class: "nca-review-score-label", text: scoreText });
    const scoreMeta = el("div", { class: "nca-review-score-meta" });
    scoreMeta.appendChild(el("span", { text: `审查 ${review.files_reviewed || 0} 个文件` }));
    scoreMeta.appendChild(el("span", { class: "nca-review-score-sep", text: "·" }));
    scoreMeta.appendChild(el("span", { text: 审查深度标签[review.review_depth] || "标准" }));
    scoreCard.appendChild(scoreNum);
    scoreCard.appendChild(scoreLabel);
    scoreCard.appendChild(scoreMeta);
    container.appendChild(scoreCard);

    // ─── 统计指标卡片 ───────────────────────
    container.appendChild(渲染统计指标卡片(review));

    // ─── 总体评价 ────────────────────────────────
    if (review.summary) {
        const summaryBox = el("div", { class: "nca-review-section" });
        summaryBox.appendChild(el("div", { class: "nca-review-section-title", text: "总体评价" }));
        summaryBox.appendChild(el("div", { class: "nca-review-summary-text", text: review.summary }));
        container.appendChild(summaryBox);
    }

    // ─── 问题列表 ────────────────────────────────
    const issues = review.issues || [];
    const issuesBox = el("div", { class: "nca-review-section" });
    issuesBox.appendChild(el("div", {
        class: "nca-review-section-title",
        html: `发现问题 <span class="nca-review-count">(${issues.length})</span>`,
    }));

    if (issues.length === 0) {
        issuesBox.appendChild(el("div", {
            class: "nca-review-no-issues",
            text: "✓ 未发现明显问题",
        }));
    } else {
        // 筛选栏
        const filterBar = 创建筛选栏();
        issuesBox.appendChild(filterBar);

        // 按严重程度排序
        const severityOrder = { high: 0, medium: 1, low: 2 };
        const sortedIssues = [...issues].sort((a, b) => {
            return (severityOrder[a.severity] || 3) - (severityOrder[b.severity] || 3);
        });

        const issueCards = [];
        for (const issue of sortedIssues) {
            const card = 创建问题卡片(issue);
            card.dataset.severity = issue.severity || "low";
            card.dataset.category = issue.category || "style";
            issueCards.push(card);
            issuesBox.appendChild(card);
        }

        // 绑定筛选逻辑
        绑定筛选事件(filterBar, issueCards);
    }
    container.appendChild(issuesBox);

    // ─── 性能模式区域 ─────────────────────────────
    const perfPatterns = review.performance_patterns || [];
    if (perfPatterns.length > 0) {
        container.appendChild(渲染性能模式区域(perfPatterns));
    }

    // ─── 架构建议区域 ─────────────────────────────
    const archSuggestions = review.architecture_suggestions || [];
    if (archSuggestions.length > 0) {
        container.appendChild(渲染架构建议区域(archSuggestions));
    }

    // ─── 优点列表 ────────────────────────────────
    const strengths = review.strengths || [];
    if (strengths.length > 0) {
        const strengthsBox = el("div", { class: "nca-review-section" });
        strengthsBox.appendChild(el("div", {
            class: "nca-review-section-title",
            html: `代码优点 <span class="nca-review-count">(${strengths.length})</span>`,
        }));
        const strengthsList = el("div", { class: "nca-review-strengths-list" });
        for (const s of strengths) {
            strengthsList.appendChild(el("div", {
                class: "nca-review-strength-item",
                html: `<span class="nca-review-strength-icon">✓</span><span>${_转义HTML(s)}</span>`,
            }));
        }
        strengthsBox.appendChild(strengthsList);
        container.appendChild(strengthsBox);
    }
}

/**
 * 将审查结果格式化为结构化 Markdown 字符串（供发送到聊天对话）
 * @param {Object} review - 审查结果对象
 * @returns {string} Markdown 格式的审查报告
 */
export function 格式化审查为消息内容(review) {
    const score = review.score || 0;
    const filesReviewed = review.files_reviewed || 0;
    const depthLabel = 审查深度标签[review.review_depth] || "标准";
    const issues = review.issues || [];
    const strengths = review.strengths || [];

    const lines = [];

    // ─── 头部 ────────────────────────────────
    lines.push("## 代码审查报告");
    lines.push(`**评分**: ${score}/100`);
    lines.push(`**审查范围**: ${filesReviewed}个文件 · ${depthLabel}深度`);
    lines.push("");

    // ─── 总体评价 ────────────────────────────────
    if (review.summary) {
        lines.push(review.summary);
        lines.push("");
    }

    // ─── 问题列表 ────────────────────────────────
    lines.push(`### 发现问题 (${issues.length})`);

    if (issues.length === 0) {
        lines.push("- ✓ 未发现明显问题");
    } else {
        const severityOrder = { high: 0, medium: 1, low: 2 };
        const sortedIssues = [...issues].sort((a, b) => {
            return (severityOrder[a.severity] || 3) - (severityOrder[b.severity] || 3);
        });

        for (const issue of sortedIssues) {
            const icon = 严重程度图标[issue.severity] || "🟢";
            const sevText = 严重程度文字[issue.severity] || "低";
            const catLabel = 分类标签[issue.category] || issue.category || "其他";
            const fileLine = issue.line && issue.line > 0
                ? `${issue.file}:${issue.line}`
                : (issue.file || "未知文件");
            const desc = issue.description || "无描述";

            lines.push(`- ${icon} ${sevText} [${catLabel}] ${fileLine} — ${desc}`);

            if (issue.suggestion) {
                lines.push(`  💡 建议：${issue.suggestion}`);
            }
        }
    }
    lines.push("");

    // ─── 优点列表 ────────────────────────────────
    lines.push("### 代码优点");
    if (strengths.length === 0) {
        lines.push("- ✓ 暂无突出优点");
    } else {
        for (const s of strengths) {
            lines.push(`- ✓ ${s}`);
        }
    }
    lines.push("");

    // ─── 尾部分隔与指令 ────────────────────────────────
    lines.push("---");
    lines.push("请基于以上审查结果，对插件进行优化。");

    return lines.join("\n");
}

// ═══════════════════════════════════════════════════════════════
//  共享 UI 函数（审查深度下拉菜单，供优化面板和可视化面板复用）
// ═══════════════════════════════════════════════════════════════

/**
 * 显示审查深度下拉菜单（定位到指定按钮下方）
 * @param {HTMLElement} anchor - 锚点元素（按钮）
 * @param {Function} onSelect - 选择深度后的回调 (depth: string) => void
 */
export function 显示审查深度下拉菜单(anchor, onSelect) {
    if (!anchor) return;
    // 关闭已有菜单
    document.querySelectorAll(".nca-review-depth-menu").forEach(m => m.remove());

    // 主题变量作用域在 .nca-sidebar-root.nca-theme-light 上；
    // 菜单若挂到作用域之外，需从作用域节点的 computed style 提取变量内联，
    // 否则浅色主题下回退到 :root 深色默认值
    const sidebarRoot = anchor.closest(".nca-sidebar-root");
    const 继承主题 = 元素 => {
        if (!sidebarRoot) return;
        const cs = getComputedStyle(sidebarRoot);
        for (const name of ["--nca-bg-elevated", "--nca-border", "--nca-shadow-md", "--nca-fg-dim", "--nca-hover-overlay", "--nca-accent", "--nca-font-mono"]) {
            const v = cs.getPropertyValue(name).trim();
            if (v) 元素.style.setProperty(name, v);
        }
    };

    const menu = el("div", {
        class: "nca-review-depth-menu",
        style: {
            display: "flex",
            position: "fixed",
            zIndex: "10000",
            flexDirection: "column",
            gap: "2px",
            background: "var(--nca-bg-elevated)",
            border: "1px solid var(--nca-border)",
            borderRadius: "6px",
            padding: "4px",
            boxShadow: "var(--nca-shadow-md)",
            minWidth: "140px",
        },
    });
    继承主题(menu);
    const rect = anchor.getBoundingClientRect();
    menu.style.top = (rect.bottom + 4) + "px";
    menu.style.left = rect.left + "px";

    审查深度选项.forEach(d => {
        const opt = el("button", {
            text: d.label,
            style: {
                padding: "6px 12px",
                background: "transparent",
                border: "none",
                color: "var(--nca-fg-dim)",
                fontSize: "12px",
                cursor: "pointer",
                borderRadius: "4px",
                textAlign: "left",
                whiteSpace: "nowrap",
                fontFamily: "var(--nca-font-mono)",
            },
        });
        opt.addEventListener("mouseenter", () => {
            opt.style.background = "var(--nca-hover-overlay)";
            opt.style.color = "var(--nca-accent)";
        });
        opt.addEventListener("mouseleave", () => {
            opt.style.background = "transparent";
            opt.style.color = "var(--nca-fg-dim)";
        });
        opt.addEventListener("click", (e) => {
            e.stopPropagation();
            menu.remove();
            onSelect(d.value);
        });
        menu.appendChild(opt);
    });

    // 优先挂到侧边栏根容器内部，继承 .nca-theme-light 主题变量（对标会话视图管理器下拉菜单做法）；
    // 兼容旧入口：侧边栏未就绪时才退回 document.body（配合内联变量继承）
    标记禁止翻译(menu);
    (sidebarRoot || document.body).appendChild(menu);
    requestAnimationFrame(() => {
        document.addEventListener("click", function closeMenu() {
            menu.remove();
            document.removeEventListener("click", closeMenu);
        });
    });
}

// ═══════════════════════════════════════════════════════════════
//  兼容入口（弹窗模式，内部调用纯函数）
// ═══════════════════════════════════════════════════════════════

/**
 * 显示代码审查对话框（兼容入口，内部调用纯函数）
 * @param {string} pluginPath - 插件路径
 */
export function 显示代码审查对话框(pluginPath) {
    // 移除已有对话框
    const existing = document.querySelector(".nca-review-overlay");
    if (existing) existing.remove();

    const overlay = el("div", { class: "nca-review-overlay" });
    const dialog = el("div", { class: "nca-review-dialog" });

    // ─── 头部 ────────────────────────────────
    const header = el("div", { class: "nca-review-header" });
    const title = el("div", { class: "nca-review-title", text: "AI 代码审查" });
    const subtitle = el("div", { class: "nca-review-subtitle", text: pluginPath });
    const closeBtn = el("button", { class: "nca-review-close", html: "×", title: "关闭" });
    header.appendChild(title);
    header.appendChild(subtitle);
    header.appendChild(closeBtn);
    dialog.appendChild(header);

    // ─── 控制栏 ────────────────────────────────
    const toolbar = el("div", { class: "nca-review-toolbar" });

    const depthLabel = el("span", { class: "nca-review-depth-label", text: "审查深度：" });
    const depthSelect = el("select", { class: "nca-review-depth-select" });
    审查深度选项.forEach(d => {
        const opt = el("option", { value: d.value, text: d.label });
        depthSelect.appendChild(opt);
    });

    const reviewBtn = el("button", {
        class: "nca-review-start-btn",
        html: '<span class="nca-review-start-icon">🔍</span> 开始审查',
    });

    toolbar.appendChild(depthLabel);
    toolbar.appendChild(depthSelect);
    toolbar.appendChild(reviewBtn);
    dialog.appendChild(toolbar);

    // ─── 结果区域 ────────────────────────────────
    const resultArea = el("div", { class: "nca-review-result-area" });
    resultArea.appendChild(el("div", {
        class: "nca-review-placeholder",
        text: "选择审查深度后点击「开始审查」",
    }));
    dialog.appendChild(resultArea);

    overlay.appendChild(dialog);
    // 直挂 document.body（sidebarRoot 防护覆盖不到），单独加翻译豁免
    标记禁止翻译(overlay);
    document.body.appendChild(overlay);

    // ─── 事件绑定 ────────────────────────────────
    closeBtn.addEventListener("click", () => overlay.remove());
    overlay.addEventListener("click", (e) => {
        if (e.target === overlay) overlay.remove();
    });

    reviewBtn.addEventListener("click", async () => {
        const depth = depthSelect.value;

        // 显示加载状态
        reviewBtn.disabled = true;
        reviewBtn.innerHTML = '<span class="nca-review-loading-spinner"></span> 审查中...';
        resultArea.innerHTML = "";
        resultArea.appendChild(el("div", {
            class: "nca-review-loading",
            html: '<div class="nca-loading-dot"></div><div class="nca-loading-dot"></div><div class="nca-loading-dot"></div><span>正在分析代码，请稍候...</span>',
        }));

        try {
            // 调用纯函数获取审查数据
            const review = await 执行代码审查(pluginPath, depth);
            // 调用纯渲染函数
            渲染审查结果(review, resultArea);
        } catch (e) {
            resultArea.innerHTML = "";
            const errorBox = el("div", { class: "nca-review-error" });
            errorBox.appendChild(el("div", { class: "nca-review-error-icon", text: "⚠" }));
            errorBox.appendChild(el("div", { class: "nca-review-error-msg", text: `审查失败: ${e.message}` }));
            resultArea.appendChild(errorBox);
        } finally {
            reviewBtn.disabled = false;
            reviewBtn.innerHTML = '<span class="nca-review-start-icon">🔍</span> 开始审查';
        }
    });
}

/**
 * 创建代码审查按钮（用于优化面板工具栏）
 * @param {Function} 获取插件路径 - 返回当前选中的插件路径
 * @returns {HTMLElement} 按钮元素
 */
export function 创建代码审查按钮(获取插件路径) {
    const btn = el("button", {
        class: "nca-review-btn",
        title: "AI 代码审查",
        html: '<span class="nca-review-icon">🔍</span> 代码审查',
    });
    btn.addEventListener("click", () => {
        const pluginPath = 获取插件路径();
        if (!pluginPath) {
            Toast.warning("请先选择一个插件目录");
            return;
        }
        显示代码审查对话框(pluginPath);
    });
    return btn;
}

// ═══════════════════════════════════════════════════════════════
//  内部辅助函数 —— 统计指标卡片
// ═══════════════════════════════════════════════════════════════

/**
 * 渲染统计指标卡片行
 */
function 渲染统计指标卡片(review) {
    const metrics = el("div", { class: "nca-review-metrics" });
    const complexity = review.complexity_notes || {};
    const issues = review.issues || [];

    // Lint 问题数
    const lintCount = issues.length > 0 ? String(issues.length) : "--";
    metrics.appendChild(创建metric卡片(lintCount, "Lint 问题"));

    // 平均复杂度
    const avgComplexity = complexity.avg_complexity || "--";
    metrics.appendChild(创建metric卡片(avgComplexity, "平均复杂度"));

    // CVE 漏洞数（从security类issue中统计）
    const cveCount = issues.filter(i => i.category === "security").length;
    metrics.appendChild(创建metric卡片(String(cveCount), "CVE 漏洞"));

    // 高风险函数数
    const highRisk = complexity.high_risk_functions || [];
    metrics.appendChild(创建metric卡片(String(highRisk.length), "高风险函数"));

    return metrics;
}

function 创建metric卡片(value, label) {
    const card = el("div", { class: "nca-review-metric-card" });
    card.appendChild(el("span", { class: "nca-review-metric-value", text: value }));
    card.appendChild(el("span", { class: "nca-review-metric-label", text: label }));
    return card;
}

// ═══════════════════════════════════════════════════════════════
//  内部辅助函数 —— 筛选栏
// ═══════════════════════════════════════════════════════════════

function 创建筛选栏() {
    const bar = el("div", { class: "nca-review-filter-bar" });

    const filters = [
        { key: "all", label: "全部" },
        { key: "high", label: "🔴 高" },
        { key: "medium", label: "🟡 中" },
        { key: "low", label: "🟢 低" },
        { key: "__divider__" },
        { key: "security", label: "安全" },
        { key: "performance", label: "性能" },
        { key: "style", label: "规范" },
        { key: "comfyui", label: "节点" },
        { key: "maintenance", label: "维护" },
        { key: "frontend_layout", label: "布局" },
    ];

    for (const f of filters) {
        if (f.key === "__divider__") {
            bar.appendChild(el("span", { class: "nca-review-filter-divider", text: "|" }));
            continue;
        }
        const btn = el("button", {
            class: `nca-review-filter-btn${f.key === "all" ? " active" : ""}`,
            text: f.label,
        });
        btn.dataset.filter = f.key;
        bar.appendChild(btn);
    }

    return bar;
}

const 严重度筛选键 = new Set(["high", "medium", "low"]);
const 分类筛选键 = new Set(["security", "performance", "style", "comfyui", "maintenance", "frontend_layout"]);

function 绑定筛选事件(filterBar, issueCards) {
    const btns = filterBar.querySelectorAll(".nca-review-filter-btn");

    btns.forEach(btn => {
        btn.addEventListener("click", () => {
            const key = btn.dataset.filter;

            if (key === "all") {
                // 重置所有
                btns.forEach(b => b.classList.remove("active"));
                btn.classList.add("active");
            } else {
                // 取消 "全部" 激活状态
                filterBar.querySelector('[data-filter="all"]').classList.remove("active");
                btn.classList.toggle("active");
            }

            // 收集当前激活的筛选条件
            const activeBtns = filterBar.querySelectorAll(".nca-review-filter-btn.active");
            const activeSeverities = [];
            const activeCategories = [];
            let showAll = false;

            activeBtns.forEach(ab => {
                const k = ab.dataset.filter;
                if (k === "all") showAll = true;
                else if (严重度筛选键.has(k)) activeSeverities.push(k);
                else if (分类筛选键.has(k)) activeCategories.push(k);
            });

            // 如果没有任何激活的筛选，等同“全部”
            if (activeSeverities.length === 0 && activeCategories.length === 0) {
                showAll = true;
                filterBar.querySelector('[data-filter="all"]').classList.add("active");
            }

            // 应用筛选
            for (const card of issueCards) {
                if (showAll) {
                    card.style.display = "";
                    continue;
                }
                const matchSev = activeSeverities.length === 0 || activeSeverities.includes(card.dataset.severity);
                const matchCat = activeCategories.length === 0 || activeCategories.includes(card.dataset.category);
                card.style.display = (matchSev && matchCat) ? "" : "none";
            }
        });
    });
}

// ═══════════════════════════════════════════════════════════════
//  内部辅助函数 —— 性能模式折叠区
// ═══════════════════════════════════════════════════════════════

function 渲染性能模式区域(patterns) {
    const section = el("div", { class: "nca-review-section nca-review-section-collapsible collapsed" });

    const title = el("div", {
        class: "nca-review-section-title",
        html: `⚡ 性能模式 <span class="nca-review-count">(${patterns.length})</span>`,
    });
    title.addEventListener("click", () => section.classList.toggle("collapsed"));
    section.appendChild(title);

    const content = el("div", { class: "nca-review-section-content" });
    for (const p of patterns) {
        const impact = (p.impact || "low").toLowerCase();
        const item = el("div", { class: `nca-review-pattern-item impact-${impact}` });

        // 头部
        const header = el("div", { class: "nca-review-pattern-header" });
        header.appendChild(el("span", { class: "nca-review-pattern-badge", text: p.pattern || "" }));
        header.appendChild(el("span", { class: `nca-review-impact-tag ${impact}`, text: impact }));
        item.appendChild(header);

        // 文件位置
        if (p.file) {
            const loc = p.line ? `${p.file}:${p.line}` : p.file;
            item.appendChild(el("div", { class: "nca-review-pattern-location", text: loc }));
        }

        // 描述
        if (p.description) {
            item.appendChild(el("div", { class: "nca-review-pattern-desc", text: p.description }));
        }

        // 修复建议
        if (p.fix_suggestion) {
            item.appendChild(el("div", { class: "nca-review-pattern-fix", text: `💡 ${p.fix_suggestion}` }));
        }

        content.appendChild(item);
    }
    section.appendChild(content);
    return section;
}

// ═══════════════════════════════════════════════════════════════
//  内部辅助函数 —— 架构建议折叠区
// ═══════════════════════════════════════════════════════════════

function 渲染架构建议区域(suggestions) {
    const section = el("div", { class: "nca-review-section nca-review-section-collapsible collapsed" });

    const title = el("div", {
        class: "nca-review-section-title",
        html: `🏗️ 架构建议 <span class="nca-review-count">(${suggestions.length})</span>`,
    });
    title.addEventListener("click", () => section.classList.toggle("collapsed"));
    section.appendChild(title);

    const content = el("div", { class: "nca-review-section-content" });
    for (const s of suggestions) {
        const severity = (s.severity || "low").toLowerCase();
        const item = el("div", { class: `nca-review-arch-item severity-${severity}` });

        // 头部
        const header = el("div", { class: "nca-review-arch-header" });
        header.appendChild(el("span", { class: "nca-review-arch-type-badge", text: s.type || "" }));
        header.appendChild(el("span", { class: `nca-review-severity-tag ${severity}`, text: severity }));
        item.appendChild(header);

        // 描述
        if (s.description) {
            item.appendChild(el("p", { class: "nca-review-arch-desc", text: s.description }));
        }

        // 建议
        if (s.suggestion) {
            item.appendChild(el("p", { class: "nca-review-arch-suggestion", text: `建议：${s.suggestion}` }));
        }

        content.appendChild(item);
    }
    section.appendChild(content);
    return section;
}

// ═══════════════════════════════════════════════════════════════
//  内部辅助函数 —— 问题卡片
// ═══════════════════════════════════════════════════════════════

/**
 * 创建单个问题卡片
 * @param {Object} issue - 问题对象
 * @returns {HTMLElement}
 */
function 创建问题卡片(issue) {
    const severity = issue.severity || "low";
    const category = issue.category || "style";

    const card = el("div", { class: `nca-review-issue nca-review-issue-${severity}` });

    // 头部：严重程度 + 分类 + 文件行号
    const header = el("div", { class: "nca-review-issue-header" });
    header.appendChild(el("span", {
        class: `nca-review-severity-badge nca-review-severity-${severity}`,
        text: 严重程度文字[severity] || "低",
    }));
    header.appendChild(el("span", {
        class: "nca-review-category-badge",
        text: 分类标签[category] || category,
    }));
    const fileLine = issue.line && issue.line > 0
        ? `${issue.file}:${issue.line}`
        : (issue.file || "未知文件");
    header.appendChild(el("span", { class: "nca-review-issue-file", text: fileLine }));
    card.appendChild(header);

    // 描述
    if (issue.description) {
        card.appendChild(el("div", {
            class: "nca-review-issue-desc",
            html: _转义HTML(issue.description),
        }));
    }

    // 建议
    if (issue.suggestion) {
        card.appendChild(el("div", {
            class: "nca-review-issue-suggestion",
            html: `<span class="nca-review-suggestion-prefix">💡 建议：</span>${_转义HTML(issue.suggestion)}`,
        }));
    }

    return card;
}
