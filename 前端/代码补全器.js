// ═══════════════════════════════════════════════════════════════
// 代码补全器.js — AI 代码补全核心模块（ghost text 风格）
// NodeCraft AI — Luxury Terminal / Neo-Noir Hacker
// ═══════════════════════════════════════════════════════════════

import { 请求, 状态, 事件总线, 事件 } from "./交互与状态.js";

// ─── 常量 ────────────────────────────────────────────────────────
let 补全超时 = 8000;           // 请求超时 8 秒（比默认 30 秒短）—— 从设置动态更新
const 防抖延迟 = 400;           // 输入防抖 400ms
const 行高 = 19.2;              // 12px * 1.6
const 字符宽度 = 7.2;           // 等宽字体近似宽度
const 触发字符正则 = /[a-zA-Z0-9_.()\[\]{}]/;  // 有效触发字符
const 移动键集合 = new Set([
    "ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown",
    "Home", "End", "PageUp", "PageDown",
]);

// 监听设置加载/保存，动态更新补全超时
事件总线.on(事件.设置已加载, (设置) => {
    补全超时 = 设置?.completion_timeout || 8000;
});
事件总线.on(事件.设置已保存, (设置) => {
    if (设置?.completion_timeout !== undefined) {
        补全超时 = 设置.completion_timeout || 8000;
    }
});

/**
 * 创建代码补全器
 * @param {HTMLTextAreaElement} textareaEl - 绑定的 textarea 元素
 * @param {Object} options - 配置项
 * @param {Function} options.当前文件路径获取 - 返回当前文件路径
 * @param {Function} options.插件上下文获取 - 返回插件上下文名称
 * @param {Function} options.模型来源获取 - 返回模型来源（"api" 或 "local"）
 * @returns {{ destroy: () => void }}
 */
export function 创建代码补全器(textareaEl, options = {}) {
    const {
        当前文件路径获取 = () => "",
        插件上下文获取 = () => "",
        模型来源获取 = () => "api",
    } = options;

    // ─── 内部状态 ────────────────────────────────────────────
    let 防抖定时器 = null;
    let 当前补全文本 = "";
    let 浮层可见 = false;
    let 请求序号 = 0;          // 请求计数器，用于丢弃过期响应（逻辑取消）
    let 已销毁 = false;

    // ─── 创建浮层 DOM ────────────────────────────────────────
    const 父元素 = textareaEl.parentElement;
    if (父元素) 父元素.style.position = "relative";

    const 浮层 = document.createElement("div");
    浮层.className = "nca-completion-overlay";
    浮层.style.display = "none";
    if (父元素) 父元素.appendChild(浮层);

    // ─── 光标像素位置计算 ────────────────────────────────────
    // 基于 selectionStart 近似计算光标在 textarea 内的像素坐标
    function 计算光标位置() {
        const text = textareaEl.value.substring(0, textareaEl.selectionStart);
        const lines = text.split("\n");
        const lineNum = lines.length - 1;
        const colNum = lines[lines.length - 1].length;

        const style = window.getComputedStyle(textareaEl);
        const paddingLeft = parseFloat(style.paddingLeft) || 0;
        const paddingTop = parseFloat(style.paddingTop) || 0;

        const x = paddingLeft + colNum * 字符宽度 - textareaEl.scrollLeft;
        const y = paddingTop + lineNum * 行高 - textareaEl.scrollTop;

        return { x, y };
    }

    // ─── 显示 / 隐藏浮层 ──────────────────────────────────────
    function 显示浮层(文本) {
        当前补全文本 = 文本;
        浮层.textContent = 文本;
        浮层.classList.remove("nca-completion-loading");
        定位浮层();
        浮层.style.display = "";
        浮层可见 = true;
    }

    function 显示加载() {
        浮层.textContent = "...";
        浮层.classList.add("nca-completion-loading");
        定位浮层();
        浮层.style.display = "";
        浮层可见 = true;
    }

    function 隐藏浮层() {
        浮层.style.display = "none";
        浮层.classList.remove("nca-completion-loading");
        浮层.textContent = "";
        当前补全文本 = "";
        浮层可见 = false;
    }

    function 定位浮层() {
        const { x, y } = 计算光标位置();
        浮层.style.left = `${x}px`;
        浮层.style.top = `${y}px`;
    }

    // ─── 发送补全请求 ────────────────────────────────────────
    async function 请求补全() {
        if (已销毁) return;

        const 文件路径 = 当前文件路径获取();
        if (!文件路径) {
            隐藏浮层();
            return;
        }

        // 检查光标前最后一个字符是否为触发字符
        const pos = textareaEl.selectionStart;
        if (pos === 0 || !触发字符正则.test(textareaEl.value[pos - 1])) {
            隐藏浮层();
            return;
        }

        const 本次序号 = ++请求序号;
        显示加载();

        const 请求体 = {
            file_path: 文件路径,
            file_content: textareaEl.value,
            cursor_offset: pos,
            plugin_context: 插件上下文获取(),
            model_source: 模型来源获取() || 状态.模型来源 || "api",
            local_model_name: 状态.选中本地模型 || "",
        };

        事件总线.emit(事件.代码补全请求, { 文件路径, 光标位置: pos });

        try {
            const data = await 请求("POST", "/code-completion", 请求体, 补全超时);

            // 丢弃过期响应（新输入已触发新的请求）
            if (本次序号 !== 请求序号 || 已销毁) return;

            if (data.status === "success" && data.completion) {
                显示浮层(data.completion);
                事件总线.emit(事件.代码补全结果, { 成功: true, 补全文本: data.completion });
            } else {
                隐藏浮层();
            }
        } catch (e) {
            if (本次序号 !== 请求序号 || 已销毁) return;
            // 静默处理失败，不弹 Toast，不打断编辑
            隐藏浮层();
            事件总线.emit(事件.代码补全状态, { 状态: "error", 错误: e.message });
        }
    }

    // ─── 防抖触发 ──────────────────────────────────────────────
    function 防抖触发() {
        if (防抖定时器) clearTimeout(防抖定时器);
        防抖定时器 = setTimeout(() => {
            防抖定时器 = null;
            请求补全();
        }, 防抖延迟);
    }

    // ─── 接受补全（在光标位置插入补全文本）─────────────────────
    function 接受补全() {
        if (!浮层可见 || !当前补全文本) return false;

        const start = textareaEl.selectionStart;
        const end = textareaEl.selectionEnd;
        textareaEl.value =
            textareaEl.value.substring(0, start) +
            当前补全文本 +
            textareaEl.value.substring(end);
        const newPos = start + 当前补全文本.length;
        textareaEl.selectionStart = textareaEl.selectionEnd = newPos;
        隐藏浮层();

        // 触发 input 事件以更新 dirty 状态
        textareaEl.dispatchEvent(new Event("input", { bubbles: true }));
        return true;
    }

    // ─── 事件处理函数 ──────────────────────────────────────────
    function onInput() {
        if (已销毁) return;
        隐藏浮层();
        防抖触发();
    }

    function onKeydown(e) {
        if (已销毁) return;

        // Tab 键：接受补全（拦截在文件编辑器的 Tab 缩进处理之前）
        if (e.key === "Tab" && 浮层可见 && 当前补全文本) {
            e.preventDefault();
            e.stopImmediatePropagation();
            接受补全();
            return;
        }

        // Escape 键：取消补全
        if (e.key === "Escape" && 浮层可见) {
            e.preventDefault();
            e.stopImmediatePropagation();
            隐藏浮层();
            return;
        }

        // 光标移动键：取消当前补全
        if (移动键集合.has(e.key) && 浮层可见) {
            隐藏浮层();
        }
    }

    function onClick() {
        if (已销毁) return;
        if (浮层可见) 隐藏浮层();
    }

    function onScroll() {
        if (已销毁) return;
        if (浮层可见) 隐藏浮层();
    }

    function onBlur() {
        if (已销毁) return;
        隐藏浮层();
    }

    // ─── 绑定事件 ──────────────────────────────────────────────
    // keydown 使用 capture 阶段，确保在文件编辑器的 Tab 处理之前执行
    textareaEl.addEventListener("input", onInput);
    textareaEl.addEventListener("keydown", onKeydown, true);
    textareaEl.addEventListener("click", onClick);
    textareaEl.addEventListener("scroll", onScroll);
    textareaEl.addEventListener("blur", onBlur);

    // ─── 销毁 ──────────────────────────────────────────────────
    function destroy() {
        已销毁 = true;
        if (防抖定时器) {
            clearTimeout(防抖定时器);
            防抖定时器 = null;
        }
        隐藏浮层();
        textareaEl.removeEventListener("input", onInput);
        textareaEl.removeEventListener("keydown", onKeydown, true);
        textareaEl.removeEventListener("click", onClick);
        textareaEl.removeEventListener("scroll", onScroll);
        textareaEl.removeEventListener("blur", onBlur);
        if (浮层.parentElement) {
            浮层.parentElement.removeChild(浮层);
        }
    }

    return { destroy };
}
