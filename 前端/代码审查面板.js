// ═══════════════════════════════════════════════════════════════
// 代码审查面板.js — AI 驱动的代码审查功能
// NodeCraft AI — Luxury Terminal / Neo-Noir Hacker
// ═══════════════════════════════════════════════════════════════

import { el, NCA_API_BASE, Toast, 安全存储读, NCA_STORAGE_KEYS, _转义HTML } from "./工具函数.js";

// ─── 常量 ────────────────────────────────────────────────────────
const 审查深度标签 = { quick: "快速", standard: "标准", deep: "深度" };
const 严重程度图标 = { high: "🔴", medium: "🟡", low: "🟢" };
const 严重程度文字 = { high: "高", medium: "中", low: "低" };
const 分类标签 = {
    security: "安全性",
    performance: "性能",
    style: "代码规范",
    comfyui: "ComfyUI规范",
    maintenance: "可维护性",
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
        // 按严重程度排序
        const severityOrder = { high: 0, medium: 1, low: 2 };
        const sortedIssues = [...issues].sort((a, b) => {
            return (severityOrder[a.severity] || 3) - (severityOrder[b.severity] || 3);
        });

        for (const issue of sortedIssues) {
            issuesBox.appendChild(创建问题卡片(issue));
        }
    }
    container.appendChild(issuesBox);

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
    const depths = [
        { value: "quick", label: "快速（核心文件）" },
        { value: "standard", label: "标准（所有 Python）" },
        { value: "deep", label: "深度（Python + JS）" },
    ];
    depths.forEach(d => {
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
//  内部辅助函数
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
