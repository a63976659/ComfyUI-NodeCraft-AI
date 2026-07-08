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

// 有问题时倒计时更长（需用户决策）；无问题时更短（仅确认）
const 倒计时秒数 = {
    有问题: 30,
    无问题: 5,
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
    let 已完成 = false;                 // 是否已确认/跳过/自动执行（防重复）
    let 倒计时id = null;                 // setInterval 句柄
    const 总秒数 = 有问题 ? 倒计时秒数.有问题 : 倒计时秒数.无问题;
    let 剩余秒数 = 总秒数;
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
            问题状态[qi] = null; // 初始未选，由用户选择或倒计时结束时用默认值

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
                    用户取消倒计时(); // 用户操作 → 取消自动执行
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

    // ─── 倒计时进度条 ────────────────────────────────────────────
    const 计时填充 = el("div", { class: "nca-planning-timer-fill" });
    const 计时文字 = el("div", {
        class: "nca-planning-timer-text",
        text: `${剩余秒数}秒后自动执行...`,
    });
    const 计时器区 = el("div", { class: "nca-planning-timer" }, [
        计时文字,
        el("div", { class: "nca-planning-timer-bar" }, [计时填充]),
    ]);
    面板.appendChild(计时器区);

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

    // 停止并隐藏倒计时（确认/跳过/自动执行时调用）
    function 停止倒计时() {
        if (倒计时id) {
            clearInterval(倒计时id);
            倒计时id = null;
        }
    }

    // 用户主动操作时取消倒计时并移除进度条
    function 用户取消倒计时() {
        停止倒计时();
        if (计时器区.parentNode) 计时器区.remove();
    }

    // 禁用所有按钮，防止重复点击
    function 禁用按钮() {
        确认按钮.disabled = true;
        if (跳过按钮) 跳过按钮.disabled = true;
    }

    // 收集用户选择，组装为 {问题文本: 选中选项文本}
    // 使用默认=true 时，未选问题回退到 default（无 default 则取第 0 项）
    function 收集选择(使用默认) {
        const choices = {};
        问题列表.forEach((q, qi) => {
            const 选项组 = Array.isArray(q.options) ? q.options : [];
            let idx = 问题状态[qi];
            if (使用默认 && (idx === null || idx === undefined)) {
                idx = Number.isInteger(q.default) ? q.default : 0;
            }
            if (idx !== null && idx !== undefined && 选项组[idx] !== undefined) {
                choices[q.question] = 选项组[idx];
            }
        });
        return choices;
    }

    // 确认执行（使用默认=true 表示倒计时自动触发）
    function 处理确认(使用默认) {
        if (已完成) return;

        // 手动确认时校验必选问题是否已全部选择
        if (有问题 && !使用默认) {
            const 未选 = 问题列表.some((q, qi) => 问题状态[qi] === null || 问题状态[qi] === undefined);
            if (未选) {
                Toast.warning("请先选择所有需要确认的选项");
                return;
            }
        }

        const choices = 收集选择(使用默认);
        已完成 = true;
        停止倒计时();
        用户取消倒计时();
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
        停止倒计时();
        用户取消倒计时();
        禁用按钮();
        面板.classList.add("nca-planning-done");
        try {
            if (typeof 跳过回调 === "function") 跳过回调();
        } catch (e) {
            console.warn("[节点梦工厂] 规划面板跳过回调异常:", e);
        }
    }

    // 更新倒计时显示
    function 更新计时显示() {
        计时文字.textContent = `${剩余秒数}秒后自动执行...`;
        const 百分比 = Math.max(0, (剩余秒数 / 总秒数) * 100);
        计时填充.style.width = `${百分比}%`;
    }

    // 启动倒计时
    function 启动倒计时() {
        更新计时显示();
        倒计时id = setInterval(() => {
            剩余秒数 -= 1;
            if (剩余秒数 <= 0) {
                停止倒计时();
                处理确认(true); // 倒计时结束 → 使用默认选项自动确认
                return;
            }
            更新计时显示();
        }, 1000);
    }

    // ─── 绑定按钮事件 ────────────────────────────────────────────
    确认按钮.addEventListener("click", () => 处理确认(false));
    if (跳过按钮) 跳过按钮.addEventListener("click", 处理跳过);

    // ─── 挂载并启动倒计时 ────────────────────────────────────────
    容器.appendChild(面板);
    启动倒计时();
}
