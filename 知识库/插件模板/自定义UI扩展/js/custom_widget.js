// ═══════════════════════════════════════════════════════════════
// custom_widget.js — 自定义 UI 扩展模板
// 演示：自定义 widget、侧边栏按钮、Python-JS 通信、生命周期钩子
// ═══════════════════════════════════════════════════════════════

import { app } from "../../../scripts/app.js";
import { api } from "../../../scripts/api.js";

// ─── 1. 自定义 Widget（在节点上添加自定义 UI 控件） ─────────
const CUSTOM_WIDGET_TYPE = "custom_ui:button_widget";

function createButtonWidget(node, inputName, inputData) {
    const widget = {
        type: CUSTOM_WIDGET_TYPE,
        name: inputName,
        value: "",
        draw: function (ctx, node, widgetWidth, widgetY, height) {
            // 绘制按钮背景
            ctx.fillStyle = "#333";
            ctx.fillRect(0, widgetY, widgetWidth, height);
            // 绘制按钮文字
            ctx.fillStyle = "#fff";
            ctx.font = "12px sans-serif";
            ctx.textAlign = "center";
            ctx.textBaseline = "middle";
            ctx.fillText(inputData[1]?.label || inputName, widgetWidth / 2, widgetY + height / 2);
        },
        mouse: function (event, pos, node) {
            if (event.type === "pointerdown") {
                console.log(`[CustomWidget] 按钮 ${inputName} 被点击`);
                // 发送请求到后端
                api.fetchApi("/custom_ui_example/action", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ action: "button_click", node_id: node.id }),
                });
            }
            return true;
        },
    };
    return widget;
}

// ─── 2. 注册扩展 ────────────────────────────────────────────
app.registerExtension({
    name: "Custom.UIExtension",

    // 注册自定义 widget 工厂
    async getCustomWidgets() {
        return {
            [CUSTOM_WIDGET_TYPE]: createButtonWidget,
        };
    },

    // 节点定义注册前钩子
    async beforeRegisterNodeDef(nodeType, nodeData, app) {
        // 示例：为特定节点添加自定义行为
        if (nodeData.name && nodeData.name.startsWith("Custom")) {
            const onNodeCreated = nodeType.prototype.onNodeCreated;
            nodeType.prototype.onNodeCreated = function () {
                if (onNodeCreated) onNodeCreated.apply(this, arguments);
                console.log(`[UIExtension] 节点已创建: ${nodeData.name}`);
            };
        }
    },

    // 应用启动后钩子：添加侧边栏按钮
    async setup() {
        // === 添加侧边栏按钮 ===
        const sidebar = document.querySelector(".comfy-menu");
        if (sidebar) {
            const btn = document.createElement("button");
            btn.textContent = "自定义面板";
            btn.style.cssText = "width: 100%; margin-top: 4px; padding: 4px; cursor: pointer;";
            btn.addEventListener("click", () => {
                openCustomPanel();
            });
            sidebar.appendChild(btn);
        }

        // === 监听后端 WebSocket 消息 ===
        api.addEventListener("custom_ui_event", (event) => {
            console.log("[UIExtension] 收到后端事件:", event.detail);
        });
    },
});

// ─── 3. 自定义面板（浮动弹窗） ──────────────────────────────
function openCustomPanel() {
    // 检查是否已打开
    const existing = document.getElementById("custom-ui-panel");
    if (existing) {
        existing.remove();
        return;
    }

    const panel = document.createElement("div");
    panel.id = "custom-ui-panel";
    panel.style.cssText = `
        position: fixed; top: 50%; left: 50%; transform: translate(-50%, -50%);
        width: 400px; background: #1a1a2e; color: #e0e0e0;
        border: 1px solid rgba(0,212,255,0.3); border-radius: 8px;
        padding: 16px; z-index: 10000; box-shadow: 0 8px 32px rgba(0,0,0,0.5);
    `;

    panel.innerHTML = `
        <h3 style="margin: 0 0 12px 0; color: #00d4ff;">自定义面板</h3>
        <div id="custom-panel-content">加载中...</div>
        <button id="custom-panel-close" style="margin-top: 12px; width: 100%; padding: 6px; cursor: pointer;">关闭</button>
    `;

    document.body.appendChild(panel);

    // 关闭按钮
    panel.querySelector("#custom-panel-close").addEventListener("click", () => panel.remove());

    // 从后端加载数据
    fetch("/custom_ui_example/data")
        .then((r) => r.json())
        .then((data) => {
            if (data.success) {
                panel.querySelector("#custom-panel-content").innerHTML = `
                    <p>消息: ${data.data.message}</p>
                    <p>输出目录: ${data.data.output_dir}</p>
                `;
            } else {
                panel.querySelector("#custom-panel-content").textContent = "加载失败";
            }
        })
        .catch((e) => {
            panel.querySelector("#custom-panel-content").textContent = `错误: ${e.message}`;
        });
}
