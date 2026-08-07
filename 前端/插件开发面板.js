// ═══════════════════════════════════════════════════════════════
// 插件开发面板.js — 聚合入口（模型切换栏副本 + 子模块再导出）
// NodeCraft AI — Luxury Terminal / Neo-Noir Hacker
// ═══════════════════════════════════════════════════════════════

import { el, Toast } from "./工具函数.js";
import { t } from "./i18n.js";
import {
    事件总线, 事件, 状态,
    更新模型选择,
    切换API配置,
    请求,
} from "./交互与状态.js";
import { 创建思考按钮组 } from "./思考按钮组.js";

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
    // 按钮/徽章文字经 data-nca-label + CSS 伪元素渲染，外部翻译插件碰不到 CSS content，免疫改写
    const lBtn = el("button", { class: `nca-switch-btn ${状态.模型来源 === "local" ? "active" : ""}`, "data-nca-label": t("settings.local") });
    const aBtn = el("button", { class: `nca-switch-btn ${状态.模型来源 === "api" ? "active" : ""}`, "data-nca-label": t("settings.api") });
    grp.appendChild(lBtn); grp.appendChild(aBtn); bar.appendChild(grp);
    const lSel = el("select", { class: "nca-model-select" }); lSel.style.display = 状态.模型来源 === "local" ? "" : "none";
    // 释放显存按钮：仅本地模式可见，调用 REST 卸载模型接口以释放本地模型占用的显存/内存
    const unloadBtn = el("button", { class: "nca-unload-btn", "data-nca-label": t("model.release_vram"), title: t("model.release_vram_title") });
    unloadBtn.style.display = 状态.模型来源 === "local" ? "" : "none";
    const aSel = el("select", { class: "nca-model-select nca-api-model-select" }); aSel.style.display = 状态.模型来源 === "api" ? "" : "none"; aSel.title = "选择 API 模型";
    // 思考档位按钮：规格表驱动的共用按钮组（与会话视图同一实现，见 思考按钮组.js）
    const 思考按钮组 = 创建思考按钮组();
    bar.appendChild(lSel); bar.appendChild(unloadBtn); bar.appendChild(aSel);
    思考按钮组.节点.forEach((按钮) => bar.appendChild(按钮));

    // 用占位选项设置 API 下拉框的单一提示态（加载中 / 未配置 / 云端不可达）
    function 设置API占位(文本) {
        aSel.innerHTML = "";
        const o = el("option", { value: "", text: 文本 }); o.disabled = true; o.selected = true; aSel.appendChild(o);
    }
    // 从设置中的 API 配置方案列表填充下拉框（value=配置 id，选中项为激活配置）
    function 刷新API下拉() {
        const 配置列表 = Array.isArray(状态.设置.api_profiles) ? 状态.设置.api_profiles : [];
        if (配置列表.length === 0) { 设置API占位("请到设置 > 模型 > API 服务中添加配置"); return; }
        aSel.innerHTML = "";
        配置列表.forEach(p => {
            const o = el("option", { value: p.id, text: p.name || p.model_name || "未命名配置" });
            if (p.id === 状态.设置.active_api_profile_id) o.selected = true;
            aSel.appendChild(o);
        });
    }

    function refresh() {
        lBtn.className = `nca-switch-btn ${状态.模型来源 === "local" ? "active" : ""}`;
        aBtn.className = `nca-switch-btn ${状态.模型来源 === "api" ? "active" : ""}`;
        lSel.style.display = 状态.模型来源 === "local" ? "" : "none";
        unloadBtn.style.display = 状态.模型来源 === "local" ? "" : "none";
        aSel.style.display = 状态.模型来源 === "api" ? "" : "none";
        lSel.innerHTML = "";
        if (状态.本地模型列表.length === 0) { const o = el("option", { value: "", text: "未检测到本地模型" }); o.disabled = true; lSel.appendChild(o); }
        else { 状态.本地模型列表.forEach(m => { const o = el("option", { value: m.name, text: m.name }); if (m.name === 状态.选中本地模型) o.selected = true; lSel.appendChild(o); }); }
        // 仅在 API 模式下拉取/刷新云端模型列表，避免本地模式下无谓请求
        if (状态.模型来源 === "api") 刷新API下拉();
        思考按钮组.刷新();
    }
    lBtn.addEventListener("click", async () => { await 更新模型选择({ 模型来源: "local" }); });
    aBtn.addEventListener("click", async () => { await 更新模型选择({ 模型来源: "api" }); });
    lSel.addEventListener("change", async () => { await 更新模型选择({ 选中本地模型: lSel.value }); });
    aSel.addEventListener("change", async () => { if (!aSel.value) return; await 切换API配置(aSel.value); });
    unloadBtn.addEventListener("click", async () => {
        if (unloadBtn.disabled) return;
        // 按钮文字由 CSS 伪元素渲染，状态文本同步改为读写 dataset.ncaLabel
        const 原文本 = unloadBtn.dataset.ncaLabel;
        unloadBtn.disabled = true;
        unloadBtn.classList.add("loading");
        unloadBtn.dataset.ncaLabel = "释放中…";
        try {
            const 结果 = await 请求("POST", "/unload-model", {});
            if (结果 && 结果.success === true) {
                unloadBtn.dataset.ncaLabel = "已释放";
                try { Toast && Toast.success && Toast.success("已释放本地模型显存"); } catch (_) {}
            } else {
                unloadBtn.dataset.ncaLabel = 原文本;
                try { Toast && Toast.error && Toast.error("释放显存失败"); } catch (_) {}
            }
        } catch (e) {
            unloadBtn.dataset.ncaLabel = 原文本;
            try { Toast && Toast.error && Toast.error(`释放显存失败: ${e && e.message ? e.message : e}`); } catch (_) {}
        } finally {
            unloadBtn.classList.remove("loading");
            setTimeout(() => {
                unloadBtn.disabled = false;
                if (unloadBtn.dataset.ncaLabel === "已释放") unloadBtn.dataset.ncaLabel = 原文本;
            }, 1500);
        }
    });
    refresh();

    // ── 事件总线订阅（自愈式退订）──
    // 本函数在语言切换等场景会被重复调用（renderSidebarUI 以 innerHTML="" 重建整棵 UI，
    // 面板已构建标志随之归零，再次访问优化/可视化面板就会重新走到这里），而事件总线订阅
    // 不在 cleanupAllEvents 注册表内。副本栏由三个面板共用且无卸载钩子可挂退订，故采用自愈式
    // 清理：监听器触发时若发现自己的切换栏已脱离文档树（旧 DOM 已被丢弃），就立即退订全部
    // 并跳过刷新，避免监听器随重建次数累积、闭包抱住废弃 DOM 无法回收。
    // 注意：不能把该判定放进 refresh()，上方首次调用时 bar 尚未被调用方 append 到文档。
    const _退订列表 = [];
    function _退订全部() {
        _退订列表.splice(0).forEach(fn => { try { fn(); } catch (_) {} });
    }
    function _订阅(事件名) {
        const 回调 = () => {
            if (!bar.isConnected) { _退订全部(); return; }
            refresh();
        };
        事件总线.on(事件名, 回调);
        _退订列表.push(() => 事件总线.off(事件名, 回调));
    }
    // 任一面板修改模型选择、或加载/保存设置时，此面板随之同步刷新
    _订阅(事件.模型选择变更);
    _订阅(事件.设置已加载);
    _订阅(事件.设置已保存);
    return bar;
}
