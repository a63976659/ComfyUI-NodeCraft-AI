// ═══════════════════════════════════════════════════════════════
// 思考按钮组.js — 模型切换栏的思考类循环按钮（会话视图与各面板共用）
// NodeCraft AI — Luxury Terminal / Neo-Noir Hacker
// ═══════════════════════════════════════════════════════════════

import { el } from "./工具函数.js";
import { t } from "./i18n.js";
import {
    状态,
    思考档位表, 当前思考档位, 切换思考档位,
} from "./交互与状态.js";

/**
 * 循环按钮规格表 — 新增一个"点击循环切换"的模型参数按钮只需在此加一行，
 * 无需在各面板重复写 el() / 刷新函数 / click 处理器。
 * 按钮外观统一复用 .nca-unload-btn（详见 样式/输入与附件.css）。
 *
 * 字段语义：
 *   类名   附加类名，仅用于个别按钮的差异化样式
 *   标题键/前缀键  i18n key（title 悬浮说明 / 标签前缀）
 *   档位   当前模型可循环的档位列表（空数组 = 该按钮隐藏）
 *   读取   读当前档位值（"" = 跟随服务端默认）
 *   切换   循环到下一档位并持久化
 *   文案   档位值 → 显示文本
 *   高亮   （可选）是否加 .active（accent 色，提示已偏离默认）
 *   警示   （可选）是否加 .off（warning 色，用于"显式关闭"这类需区分的档位）
 */
const 循环按钮规格表 = [
    {
        类名: "nca-thinking-btn",
        标题键: "model.thinking_title",
        前缀键: "model.thinking",
        // 开关与深度合并为一条档位链，同一模型只出现一个思考按钮
        档位: () => 思考档位表(状态.设置.model_name),
        读取: () => 当前思考档位(状态.设置.model_name),
        切换: () => 切换思考档位(状态.设置.model_name),
        文案: (值) => (值 === "on" ? t("model.thinking_on")
            : 值 === "off" ? t("model.thinking_off")
                : 值 || t("model.thinking_default")),
        // 偏离默认即高亮，显式关闭单独用 warning 色区分
        高亮: (值) => !!值 && 值 !== "off",
        警示: (值) => 值 === "off",
    },
];

/**
 * 创建思考类循环按钮组
 *
 * 按钮文字经 data-nca-label + CSS 伪元素渲染，免疫外部翻译插件改写。
 * 任一按钮点击后整组一起刷新（档位可能联动影响其他按钮的可见性）。
 *
 * @returns {{节点: HTMLElement[], 刷新: () => void}}
 *   节点 — 按序 append 到模型切换栏即可；刷新 — 在切换栏的统一刷新函数中调用一次
 */
export function 创建思考按钮组() {
    const 项目列表 = 循环按钮规格表.map((规格) => ({
        规格,
        按钮: el("button", {
            class: `nca-unload-btn ${规格.类名}`,
            title: t(规格.标题键),
        }),
    }));

    function 刷新() {
        for (const { 规格, 按钮 } of 项目列表) {
            // 仅 API 模式可用：本地模型链路不走这些参数
            const 支持 = 状态.模型来源 === "api" && 规格.档位().length > 0;
            按钮.style.display = 支持 ? "" : "none";
            if (!支持) continue;
            const 值 = 规格.读取();
            按钮.dataset.ncaLabel = `${t(规格.前缀键)}: ${规格.文案(值)}`;
            按钮.classList.toggle("active", !!(规格.高亮 && 规格.高亮(值)));
            按钮.classList.toggle("off", !!(规格.警示 && 规格.警示(值)));
        }
    }

    for (const { 规格, 按钮 } of 项目列表) {
        按钮.addEventListener("click", async () => {
            if (按钮.disabled) return;
            按钮.disabled = true;
            try { await 规格.切换(); } finally { 按钮.disabled = false; }
            刷新();  // 设置已保存事件也会刷新，此处确保持久化失败时标签也即时回显
        });
    }

    return { 节点: 项目列表.map((项) => 项.按钮), 刷新 };
}
