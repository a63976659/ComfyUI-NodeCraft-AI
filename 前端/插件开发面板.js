// ═══════════════════════════════════════════════════════════════
// 插件开发面板.js — 聚合入口（模型切换栏副本 + 子模块再导出）
// NodeCraft AI — Luxury Terminal / Neo-Noir Hacker
// ═══════════════════════════════════════════════════════════════

import { el, Toast } from "./工具函数.js";
import {
    事件总线, 事件, 状态,
    更新模型选择,
    请求,
} from "./交互与状态.js";
import { 加载云端模型列表 } from "./设置面板.js";

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
    const aSel = el("select", { class: "nca-model-select nca-api-model-select" }); aSel.style.display = 状态.模型来源 === "api" ? "" : "none"; aSel.title = "选择 API 模型";
    // 免费模型徽章：淡蓝色图标，select 框右侧
    const freeBadge = el("span", { class: "nca-free-badge", text: "免费" });
    freeBadge.style.display = "none";
    bar.appendChild(lSel); bar.appendChild(unloadBtn); bar.appendChild(aSel); bar.appendChild(freeBadge);

    // 用占位选项设置 API 下拉框的单一提示态（加载中 / 未配置 / 云端不可达）
    function 设置API占位(文本) {
        aSel.innerHTML = "";
        const o = el("option", { value: "", text: 文本 }); o.disabled = true; o.selected = true; aSel.appendChild(o);
        freeBadge.style.display = "none";
    }
    // 从云端加载 API 模型列表并填充下拉框，处理未配置/加载/错误三种状态
    async function 刷新API下拉() {
        const cloudUrl = 状态.设置.cloud_url || "";
        if (!cloudUrl) { 设置API占位("未配置云端地址"); return; }
        const 有真实选项 = Array.from(aSel.options).some(o => o.value);
        if (!有真实选项) 设置API占位("加载中…");
        try { await 加载云端模型列表(aSel, 状态.设置); } catch (_) {}
        const 加载成功 = Array.from(aSel.options).some(o => o.value);
        if (!加载成功) 设置API占位("云端不可达");
        else if (状态.设置.model_name) aSel.value = 状态.设置.model_name;
        刷新免费徽章();
    }
    // 根据当前选中选项切换免费徽章显示
    function 刷新免费徽章() {
        const sel = aSel.options[aSel.selectedIndex];
        freeBadge.style.display = (sel && sel.dataset.isFree === "true") ? "" : "none";
    }

    function refresh() {
        lBtn.className = `nca-switch-btn ${状态.模型来源 === "local" ? "active" : ""}`;
        aBtn.className = `nca-switch-btn ${状态.模型来源 === "api" ? "active" : ""}`;
        lSel.style.display = 状态.模型来源 === "local" ? "" : "none";
        unloadBtn.style.display = 状态.模型来源 === "local" ? "" : "none";
        aSel.style.display = 状态.模型来源 === "api" ? "" : "none";
        freeBadge.style.display = 状态.模型来源 === "api" ? freeBadge.style.display : "none";
        lSel.innerHTML = "";
        if (状态.本地模型列表.length === 0) { const o = el("option", { value: "", text: "未检测到本地模型" }); o.disabled = true; lSel.appendChild(o); }
        else { 状态.本地模型列表.forEach(m => { const o = el("option", { value: m.name, text: m.name }); if (m.name === 状态.选中本地模型) o.selected = true; lSel.appendChild(o); }); }
        // 仅在 API 模式下拉取/刷新云端模型列表，避免本地模式下无谓请求
        if (状态.模型来源 === "api") 刷新API下拉();
    }
    lBtn.addEventListener("click", async () => { await 更新模型选择({ 模型来源: "local" }); });
    aBtn.addEventListener("click", async () => { await 更新模型选择({ 模型来源: "api" }); });
    lSel.addEventListener("change", async () => { await 更新模型选择({ 选中本地模型: lSel.value }); });
    aSel.addEventListener("change", async () => { if (!aSel.value) return; await 更新模型选择({ API模型名: aSel.value }); 刷新免费徽章(); });
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
