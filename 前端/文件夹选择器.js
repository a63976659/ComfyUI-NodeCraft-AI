// ═══════════════════════════════════════════════════════════════
// 文件夹选择器.js — 插件目录选择组件
// NodeCraft AI — Luxury Terminal / Neo-Noir Hacker
// ═══════════════════════════════════════════════════════════════

import {
    el, NCA_STORAGE_KEYS, registerCleanup, trackPanelTimer, removePanelTimer, 安全存储读,
} from "./工具函数.js";
import { 获取本地插件列表, 事件总线, 事件, 设置插件文件夹 } from "./交互与状态.js";
import { 创建代码审查按钮 } from "./代码审查面板.js";

/**
 * 创建文件夹选择器组件
 * @param {Function} onSelect - 选中插件后的回调 (pluginName: string) => void
 * @param {Function} [获取插件路径] - 返回当前选中的插件路径的回调
 * @param {Function} [onReviewClick] - 自定义审查按钮点击回调 (pluginPath: string) => void；传入时覆盖默认弹窗行为
 * @returns {HTMLElement} 组件 DOM 元素
 */
export function 创建文件夹选择器(onSelect, 获取插件路径, onReviewClick) {
    let 当前列表 = [];
    let 当前显示列表 = [];
    let 选中项 = 安全存储读(NCA_STORAGE_KEYS.plugin);

    // 外层包装：默认折叠（每次刷新回到折叠状态，不持久化）
    const 容器 = el("div", { class: "nc-folder-selector-wrapper collapsed" });

    // 折叠标题栏
    const 箭头 = el("span", { class: "nc-folder-selector-arrow", text: "▶" });
    const 标题文本 = el("span", { class: "nc-folder-selector-title", text: "插件选择" });
    const 已选名称 = el("span", { class: "nc-selected-name", text: 选中项 || "" });
    const 标题栏 = el("div", { class: "nc-folder-selector-toggle" });
    标题栏.appendChild(箭头);
    标题栏.appendChild(标题文本);
    标题栏.appendChild(已选名称);

    // 代码审查按钮（放置在标题栏右侧）
    const 路径回调 = 获取插件路径 || (() => 选中项);
    let reviewBtn;
    if (typeof onReviewClick === "function") {
        // 优化面板覆盖默认行为：显示深度选择菜单并内嵌审查视图
        reviewBtn = el("button", {
            class: "nca-review-btn nc-folder-review-btn",
            title: "AI 代码审查",
            html: '<span class="nca-review-icon">🔍</span> 代码审查',
        });
        reviewBtn.addEventListener("click", (e) => {
            e.stopPropagation();
            onReviewClick(路径回调());
        });
    } else {
        reviewBtn = 创建代码审查按钮(路径回调);
        reviewBtn.classList.add("nc-folder-review-btn");
        // 阻止点击冒泡到标题栏，避免触发折叠/展开
        reviewBtn.addEventListener("click", (e) => e.stopPropagation());
    }
    // 初始禁用状态：未选择文件夹时禁用
    reviewBtn.disabled = !路径回调();
    标题栏.appendChild(reviewBtn);

    // 内容区（包含原有的搜索行、列表、外部装载按钮）
    const 内容区 = el("div", { class: "nc-folder-selector-content nc-folder-selector" });

    // 搜索行：搜索框 + 重置按钮
    const 搜索行 = el("div", { class: "nc-folder-search-row" });
    const 搜索框 = el("input", {
        class: "nc-folder-search",
        type: "text",
        placeholder: "🔍 检索关键词...",
    });
    const 重置按钮 = el("button", {
        class: "nc-folder-reset",
        html: '<span class="nc-folder-reset-icon">⟲</span>',
        title: "重置选择并刷新列表",
    });
    搜索行.appendChild(搜索框);
    搜索行.appendChild(重置按钮);

    // 列表容器
    const 列表容器 = el("div", { class: "nc-folder-list" });

    // 外部装载按钮
    const 外部按钮 = el("button", {
        class: "nc-folder-external",
        text: "📂 外部装载",
    });

    内容区.appendChild(搜索行);
    内容区.appendChild(列表容器);
    内容区.appendChild(外部按钮);

    容器.appendChild(标题栏);
    容器.appendChild(内容区);

    // 点击标题栏切换折叠状态
    标题栏.addEventListener("click", () => {
        容器.classList.toggle("collapsed");
    });

    // 更新标题栏的已选文件夹名
    function 更新已选名称(name) {
        已选名称.textContent = name || "";
        // 同步更新审查按钮的禁用状态
        reviewBtn.disabled = !路径回调();
    }

    // 渲染列表
    function 渲染列表(items) {
        当前显示列表 = items || [];
        列表容器.innerHTML = '';
        当前显示列表.forEach(name => {
            const item = el("div", {
                class: `nc-folder-item${name === 选中项 ? ' selected' : ''}`,
                text: name,
                onClick: () => {
                    选中项 = name;
                    设置插件文件夹(name);
                    更新已选名称(name);
                    渲染列表(当前显示列表);
                    if (onSelect) onSelect(name);
                }
            });
            列表容器.appendChild(item);
        });
    }

    // 搜索防抖
    let 防抖定时器 = null;
    const 搜索输入处理 = () => {
        clearTimeout(防抖定时器);
        removePanelTimer(防抖定时器);
        防抖定时器 = setTimeout(() => {
            removePanelTimer(防抖定时器);
            const keyword = 搜索框.value.trim().toLowerCase();
            const filtered = 当前列表.filter(n => n.toLowerCase().includes(keyword));
            渲染列表(filtered);
        }, 150);
        trackPanelTimer(防抖定时器);
    };
    registerCleanup(搜索框, 'input', 搜索输入处理);

    // 重置按钮：清空选择并刷新列表
    const 重置处理 = async () => {
        选中项 = '';
        设置插件文件夹('');
        更新已选名称('');
        搜索框.value = '';
        // 同步通知消费者清除外部"已选择"标签等派生 UI（传空字符串表示重置）
        if (onSelect) onSelect('');
        重置按钮.disabled = true;
        try {
            const plugins = await 获取本地插件列表();
            当前列表 = plugins;
            渲染列表(plugins);
        } finally {
            重置按钮.disabled = false;
        }
    };
    registerCleanup(重置按钮, 'click', 重置处理);

    // 外部装载按钮
    const 外部装载处理 = () => {
        const path = prompt("请输入外部插件目录的完整路径：");
        if (path && path.trim()) {
            选中项 = path.trim();
            设置插件文件夹(选中项);
            更新已选名称(选中项);
            if (onSelect) onSelect(选中项);
        }
    };
    registerCleanup(外部按钮, 'click', 外部装载处理);

    // 订阅全局插件选择变更事件：同步高亮状态（仅更新选中样式，不触发 onSelect 避免走业务加载）
    const 插件变更处理 = (name) => {
        const 新值 = name || '';
        if (选中项 === 新值) return;
        选中项 = 新值;
        更新已选名称(新值);
        渲染列表(当前显示列表.length ? 当前显示列表 : 当前列表);
    };
    事件总线.on(事件.插件选择变更, 插件变更处理);

    // 初始加载
    获取本地插件列表().then(plugins => {
        当前列表 = plugins;
        渲染列表(plugins);
    });

    return 容器;
}
