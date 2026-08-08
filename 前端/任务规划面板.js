// ═══════════════════════════════════════════════════════════════
// 任务规划面板.js — AI 消息气泡内的执行计划确认面板
// NodeCraft AI — Luxury Terminal / Neo-Noir Hacker
// ═══════════════════════════════════════════════════════════════
//
// 在 AI 消息气泡中渲染任务规划面板，让用户确认执行计划或提供关键决策。
// 纯 DOM 操作，不依赖外部框架；所有类名统一使用 nca-planning- 前缀。

import { el, Toast } from "./工具函数.js";

// ─── 常量 ────────────────────────────────────────────────────────
// 复杂度中文映射
const 复杂度文字 = {
    simple: "简单",
    moderate: "中等",
    complex: "复杂",
};

// ═══════════════════════════════════════════════════════════════
//  导出：渲染规划面板
// ═══════════════════════════════════════════════════════════════

/**
 * 渲染规划面板到指定容器
 * @param {HTMLElement} 容器 - AI 消息气泡容器
 * @param {Object} 规划数据 - 后端返回的 planning_result
 * @param {Object} 选项
 * @param {string} 选项.会话id
 * @param {Object} 选项.原始请求体 - 原始的 chat-stream 请求体
 * @param {Function} 选项.确认后回调 - (userChoices: Object) => void
 * @param {Function} 选项.跳过回调 - () => void
 */
export function 渲染规划面板(容器, 规划数据, 选项) {
    if (!容器 || !规划数据) return;
    选项 = 选项 || {};
    const 确认后回调 = 选项.确认后回调;
    const 跳过回调 = 选项.跳过回调;

    // ─── 解析数据（防御性处理，字段缺失时降级为空）───────────────
    const 复杂度 = 规划数据.complexity || "moderate";
    const 步骤 = Array.isArray(规划数据.plan_steps) ? 规划数据.plan_steps : [];
    const 风险 = Array.isArray(规划数据.risk_notes) ? 规划数据.risk_notes : [];
    const 问题列表 = Array.isArray(规划数据.questions) ? 规划数据.questions : [];
    const 有问题 = 问题列表.length > 0;

    // ─── 内部状态 ────────────────────────────────────────────────
    let 已完成 = false;                 // 是否已确认/跳过（防重复）
    const 问题状态 = [];                 // 每个问题的选中选项索引（null=未选）

    // ─── 主面板容器 ──────────────────────────────────────────────
    const 面板 = el("div", { class: "nca-planning-panel" });

    // ─── 标题栏 ──────────────────────────────────────────────────
    const 标题栏 = el("div", { class: "nca-planning-header" }, [
        el("span", { class: "nca-planning-header-icon", text: "📋" }),
        el("span", { class: "nca-planning-header-title", text: "执行计划" }),
        el("span", {
            class: "nca-planning-complexity",
            text: `复杂度：${复杂度文字[复杂度] || 复杂度}`,
        }),
    ]);
    面板.appendChild(标题栏);

    // ─── 步骤列表 ────────────────────────────────────────────────
    if (步骤.length) {
        const 步骤区 = el("ol", { class: "nca-planning-steps" });
        步骤.forEach((s) => {
            步骤区.appendChild(el("li", { class: "nca-planning-step", text: String(s) }));
        });
        面板.appendChild(步骤区);
    }

    // ─── 风险提示 ────────────────────────────────────────────────
    if (风险.length) {
        const 风险区 = el("div", { class: "nca-planning-risks" });
        风险.forEach((r) => {
            风险区.appendChild(el("div", { class: "nca-planning-risk", text: `⚠ ${String(r)}` }));
        });
        面板.appendChild(风险区);
    }

    // ─── 问题区域（需要用户确认）─────────────────────────────────
    if (有问题) {
        面板.appendChild(el("div", { class: "nca-planning-divider", text: "需要你的确认" }));

        const 问题区 = el("div", { class: "nca-planning-questions" });
        问题列表.forEach((q, qi) => {
            const 选项组 = Array.isArray(q.options) ? q.options : [];
            const 默认索引 = Number.isInteger(q.default) ? q.default : null;
            问题状态[qi] = null; // 初始未选，由用户选择

            const 问题块 = el("div", { class: "nca-planning-question" });
            问题块.appendChild(el("div", {
                class: "nca-planning-question-text",
                text: `❓ ${String(q.question || "")}`,
            }));

            // 同一问题的单选按钮共用一个 name，保证互斥
            const 组名 = `nca-planning-q-${qi}-${Math.random().toString(36).slice(2, 8)}`;
            选项组.forEach((opt, oi) => {
                const 单选 = el("input", {
                    type: "radio",
                    name: 组名,
                    class: "nca-planning-radio",
                    value: String(oi),
                });
                // 默认项标注“（推荐）”，但不预先选中，促使用户主动决策
                const 显示文字 = 默认索引 === oi ? `${String(opt)}（推荐）` : String(opt);

                单选.addEventListener("change", () => {
                    问题状态[qi] = oi;
                });

                const 选项标签 = el("label", { class: "nca-planning-option" }, [
                    单选,
                    el("span", { class: "nca-planning-option-text", text: 显示文字 }),
                ]);
                问题块.appendChild(选项标签);
            });

            问题区.appendChild(问题块);
        });
        面板.appendChild(问题区);
    }

    // ─── 等待确认提示（确认面板一律停下等待用户操作，不再倒计时自动执行）─────
    const 等待提示区 = el("div", { class: "nca-planning-timer" }, [
        el("div", {
            class: "nca-planning-timer-text",
            text: "⏸ 已暂停执行：点击下方按钮后才会开始",
        }),
    ]);
    面板.appendChild(等待提示区);

    // ─── 按钮区域 ────────────────────────────────────────────────
    const 确认按钮 = el("button", {
        class: "nca-planning-btn nca-planning-btn-confirm",
        type: "button",
        text: 有问题 ? "确认执行" : "开始执行",
    });
    let 跳过按钮 = null;
    const 操作区 = el("div", { class: "nca-planning-actions" }, [确认按钮]);
    if (有问题) {
        // 无问题时不显示跳过按钮（无需用户决策）
        跳过按钮 = el("button", {
            class: "nca-planning-btn nca-planning-btn-skip",
            type: "button",
            text: "跳过规划，直接执行",
        });
        操作区.appendChild(跳过按钮);
    }
    面板.appendChild(操作区);

    // ─── 内部逻辑 ────────────────────────────────────────────────

    // 禁用所有按钮，防止重复点击
    function 禁用按钮() {
        确认按钮.disabled = true;
        if (跳过按钮) 跳过按钮.disabled = true;
    }

    // 收集用户选择，组装为 {问题文本: 选中选项文本}
    function 收集选择() {
        const choices = {};
        问题列表.forEach((q, qi) => {
            const 选项组 = Array.isArray(q.options) ? q.options : [];
            const idx = 问题状态[qi];
            if (idx !== null && idx !== undefined && 选项组[idx] !== undefined) {
                choices[q.question] = 选项组[idx];
            }
        });
        return choices;
    }

    // 确认执行
    function 处理确认() {
        if (已完成) return;

        // 校验必选问题是否已全部选择
        if (有问题) {
            const 未选 = 问题列表.some((q, qi) => 问题状态[qi] === null || 问题状态[qi] === undefined);
            if (未选) {
                Toast.warning("请先选择所有需要确认的选项");
                return;
            }
        }

        const choices = 收集选择();
        已完成 = true;
        禁用按钮();
        面板.classList.add("nca-planning-done");
        try {
            if (typeof 确认后回调 === "function") 确认后回调(choices);
        } catch (e) {
            console.warn("[节点梦工厂] 规划面板确认回调异常:", e);
        }
    }

    // 跳过规划，直接执行
    function 处理跳过() {
        if (已完成) return;
        已完成 = true;
        禁用按钮();
        面板.classList.add("nca-planning-done");
        try {
            if (typeof 跳过回调 === "function") 跳过回调();
        } catch (e) {
            console.warn("[节点梦工厂] 规划面板跳过回调异常:", e);
        }
    }

    // ─── 绑定按钮事件 ────────────────────────────────────────────
    确认按钮.addEventListener("click", 处理确认);
    if (跳过按钮) 跳过按钮.addEventListener("click", 处理跳过);

    // ─── 挂载（确认面板一律停下等待用户点击，不自动执行）──────────
    容器.appendChild(面板);
}
