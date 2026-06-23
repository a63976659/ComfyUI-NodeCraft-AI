// ═══════════════════════════════════════════════════════════════
// 附件上传组件.js — 优化/可视化面板共用的附件上传组件
// （复用开发插件的 .nca-attach-* 样式类）
// NodeCraft AI — Luxury Terminal / Neo-Noir Hacker
// ═══════════════════════════════════════════════════════════════

import {
    el, Toast, 显示视觉能力警告, 移除视觉能力警告,
} from "./工具函数.js";
import { 查询模型能力, 事件总线, 事件 } from "./交互与状态.js";

const 附件允许的图片类型 = ["image/png", "image/jpeg", "image/gif", "image/webp"];
const 附件最大数 = 6;
const 附件接受类型 = "image/png,image/jpeg,image/gif,image/webp,.txt,.py,.js,.json,.md,.css,.html,.yaml,.yml,.toml,.cfg,.ini,.sh,.bat";

/**
 * 创建附件组件工厂函数
 * @param {HTMLElement} rootContainer - 根容器（用于显示提示）
 * @param {Function} onChange - 附件变化时的回调
 * @returns {Object} 组件接口 { 预览区, 文件按钮, 文件输入, 获取附件, 有附件, 清空, 设置输入区 }
 */
export function 创建附件组件(rootContainer, onChange) {
    const 文件列表 = [];
    let _ownerInputArea = null;

    const 预览区 = el("div", { class: "nca-attachments-preview" });
    预览区.style.display = "none";

    const 文件输入 = el("input", {
        type: "file",
        multiple: "true",
        accept: 附件接受类型,
        style: { display: "none" },
    });

    const 文件按钮 = el("button", { class: "nca-attach-btn", html: "+", title: "添加文件" });
    文件按钮.addEventListener("click", () => 文件输入.click());

    // ─── 模型能力联动：动态控制按钮启用/禁用 ───
    async function 更新附件按钮状态() {
        try {
            const 能力 = await 查询模型能力();
            if (!能力.supports_vision && !能力.supports_file_content) {
                文件按钮.disabled = true;
                文件按钮.title = "当前模型不支持附件";
                文件按钮.classList.add("nca-attach-disabled");
            } else {
                文件按钮.disabled = false;
                文件按钮.title = "添加文件";
                文件按钮.classList.remove("nca-attach-disabled");
            }
        } catch (_) {
            // 降级：查询异常时保持启用
            文件按钮.disabled = false;
            文件按钮.title = "添加文件";
            文件按钮.classList.remove("nca-attach-disabled");
        }
    }
    // 初始化时查询一次
    更新附件按钮状态();
    // 模型切换时重新查询
    事件总线.on(事件.模型选择变更, () => 更新附件按钮状态());

    function 渲染预览() {
        预览区.innerHTML = "";
        if (文件列表.length === 0) {
            预览区.style.display = "none";
            // 没有附件时移除视觉警告
            if (_ownerInputArea) 移除视觉能力警告(_ownerInputArea);
            if (onChange) onChange();
            return;
        }
        预览区.style.display = "flex";
        文件列表.forEach((file, idx) => {
            const card = el("div", { class: "nca-attach-card" });
            const 是图片 = 附件允许的图片类型.includes(file.type);
            if (是图片) {
                card.appendChild(el("img", { class: "nca-attach-thumb", src: file.data }));
            } else {
                const ext = (file.name.split(".").pop() || "file").toUpperCase();
                card.appendChild(el("div", { class: "nca-attach-icon", text: ext }));
            }
            card.appendChild(el("span", { class: "nca-attach-name", text: file.name, title: file.name }));
            const removeBtn = el("button", { class: "nca-attach-remove", text: "×" });
            removeBtn.addEventListener("click", () => {
                文件列表.splice(idx, 1);
                渲染预览();
                // 移除后如果没有图片了，移除警告
                const 还有图片 = 文件列表.some(f => 附件允许的图片类型.includes(f.type));
                if (!还有图片 && _ownerInputArea) 移除视觉能力警告(_ownerInputArea);
            });
            card.appendChild(removeBtn);
            预览区.appendChild(card);
        });
        if (onChange) onChange();
    }

    文件输入.addEventListener("change", (e) => {
        const files = Array.from(e.target.files || []);
        if (!files.length) return;
        if (文件列表.length + files.length > 附件最大数) {
            Toast.warning(`最多只能添加 ${附件最大数} 个文件`);
            文件输入.value = "";
            return;
        }
        // 检测是否含图片，触发视觉能力警告
        const 含图片 = files.some(f => 附件允许的图片类型.includes(f.type));
        if (含图片 && _ownerInputArea) {
            显示视觉能力警告(_ownerInputArea);
        }
        let 已处理 = 0;
        files.forEach(file => {
            const reader = new FileReader();
            reader.onload = (ev) => {
                文件列表.push({
                    name: file.name,
                    type: file.type || "application/octet-stream",
                    size: file.size,
                    data: ev.target.result,
                });
                已处理++;
                if (已处理 === files.length) 渲染预览();
            };
            reader.readAsDataURL(file);
        });
        文件输入.value = "";
    });

    return {
        预览区, 文件按钮, 文件输入,
        获取附件: () => [...文件列表],
        有附件: () => 文件列表.length > 0,
        清空: () => { 文件列表.length = 0; 渲染预览(); },
        设置输入区: (area) => { _ownerInputArea = area; },
    };
}
