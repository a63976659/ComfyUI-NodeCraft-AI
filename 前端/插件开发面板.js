// ═══════════════════════════════════════════════════════════════
// 插件开发面板.js — 聚合入口（模型切换栏副本 + 子模块再导出）
// NodeCraft AI — Luxury Terminal / Neo-Noir Hacker
// ═══════════════════════════════════════════════════════════════

import { el, Toast } from "./工具函数.js";
import {
    事件总线, 事件, 状态,
    推导平台名称, 更新模型选择,
    请求,
} from "./交互与状态.js";

// 子模块导入
import { 构建优化面板 } from "./优化面板.js";
import { 构建可视化面板 } from "./可视化面板.js";

// Re-export 公共 API
export {
    构建优化面板,
    构建可视化面板,
};

// ═══════════════════════════════════════════════════════════════
// 复用模型切换栏（面板通用）
// ═══════════════════════════════════════════════════════════════

export function 创建模型切换栏副本(ctx) {
    const bar = el("div", { class: "nca-model-switcher", style: { flexShrink: "0" } });
    const grp = el("div", { class: "nca-switch-group" });
    const lBtn = el("button", { class: `nca-switch-btn ${状态.模型来源 === "local" ? "active" : ""}`, text: "本地" });
    const aBtn = el("button", { class: `nca-switch-btn ${状态.模型来源 === "api" ? "active" : ""}`, text: "API" });
    grp.appendChild(lBtn); grp.appendChild(aBtn); bar.appendChild(grp);
    const lSel = el("select", { class: "nca-model-select" }); lSel.style.display = 状态.模型来源 === "local" ? "" : "none";
    // 释放显存按钮：仅本地模式可见，调用 REST 卸载模型接口以释放本地模型占用的显存/内存
    const unloadBtn = el("button", { class: "nca-unload-btn", text: "释放显存", title: "卸载本地模型，释放显存/内存" });
    unloadBtn.style.display = 状态.模型来源 === "local" ? "" : "none";
    const aInfo = el("span", { class: "nca-api-info" }); aInfo.style.display = 状态.模型来源 === "api" ? "" : "none";
    bar.appendChild(lSel); bar.appendChild(unloadBtn); bar.appendChild(aInfo);

    function refresh() {
        lBtn.className = `nca-switch-btn ${状态.模型来源 === "local" ? "active" : ""}`;
        aBtn.className = `nca-switch-btn ${状态.模型来源 === "api" ? "active" : ""}`;
        lSel.style.display = 状态.模型来源 === "local" ? "" : "none";
        unloadBtn.style.display = 状态.模型来源 === "local" ? "" : "none";
        aInfo.style.display = 状态.模型来源 === "api" ? "" : "none";
        lSel.innerHTML = "";
        if (状态.本地模型列表.length === 0) { const o = el("option", { value: "", text: "未检测到本地模型" }); o.disabled = true; lSel.appendChild(o); }
        else { 状态.本地模型列表.forEach(m => { const o = el("option", { value: m.name, text: m.name }); if (m.name === 状态.选中本地模型) o.selected = true; lSel.appendChild(o); }); }
        const p = 推导平台名称(状态.设置.base_url); const md = 状态.设置.model_name || "未配置"; aInfo.textContent = `${p} / ${md}`; aInfo.title = `${p} / ${md}`;
    }
    lBtn.addEventListener("click", async () => { await 更新模型选择({ 模型来源: "local" }); });
    aBtn.addEventListener("click", async () => { await 更新模型选择({ 模型来源: "api" }); });
    lSel.addEventListener("change", async () => { await 更新模型选择({ 选中本地模型: lSel.value }); });
    aInfo.addEventListener("click", () => { if (ctx && ctx.显示设置面板Fn) ctx.显示设置面板Fn(); }); aInfo.style.cursor = "pointer";
    unloadBtn.addEventListener("click", async () => {
        if (unloadBtn.disabled) return;
        const 原文本 = unloadBtn.textContent;
        unloadBtn.disabled = true;
        unloadBtn.classList.add("loading");
        unloadBtn.textContent = "释放中…";
        try {
            const 结果 = await 请求("POST", "/unload-model", {});
            if (结果 && 结果.success === true) {
                unloadBtn.textContent = "已释放";
                try { Toast && Toast.success && Toast.success("已释放本地模型显存"); } catch (_) {}
            } else {
                unloadBtn.textContent = 原文本;
                try { Toast && Toast.error && Toast.error("释放显存失败"); } catch (_) {}
            }
        } catch (e) {
            unloadBtn.textContent = 原文本;
            try { Toast && Toast.error && Toast.error(`释放显存失败: ${e && e.message ? e.message : e}`); } catch (_) {}
        } finally {
            unloadBtn.classList.remove("loading");
            setTimeout(() => {
                unloadBtn.disabled = false;
                if (unloadBtn.textContent === "已释放") unloadBtn.textContent = 原文本;
            }, 1500);
        }
    });
    refresh();
    // 任一面板修改模型选择、或加载/保存设置时，此面板随之同步刷新
    事件总线.on(事件.模型选择变更, () => refresh());
    事件总线.on(事件.设置已加载, () => refresh());
    事件总线.on(事件.设置已保存, () => refresh());
    return bar;
}
