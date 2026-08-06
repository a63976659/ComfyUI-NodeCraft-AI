// ═══════════════════════════════════════════════════════════════
// example.js — ImageProcessNode 前端扩展示例
// 演示如何通过 registerExtension 自定义节点行为
// ═══════════════════════════════════════════════════════════════

import { app } from "../../../scripts/app.js";
import { api } from "../../../scripts/api.js";

// 注册前端扩展
app.registerExtension({
    name: "Custom.ImageProcessNode",

    // 节点定义注册前的钩子，可修改节点行为
    async beforeRegisterNodeDef(nodeType, nodeData, app) {
        if (nodeData.name === "图像处理") {
            // 示例：节点创建时添加自定义逻辑
            const onNodeCreated = nodeType.prototype.onNodeCreated;
            nodeType.prototype.onNodeCreated = function () {
                if (onNodeCreated) onNodeCreated.apply(this, arguments);
                console.log("[ImageProcessNode] 节点已创建");
            };

            // 示例：拦截节点执行，添加日志
            const onExecuted = nodeType.prototype.onExecuted;
            nodeType.prototype.onExecuted = function (message) {
                if (onExecuted) onExecuted.apply(this, arguments);
                console.log("[ImageProcessNode] 节点执行完成", message);
            };
        }
    },

    // 可选：注册自定义设置面板
    async getCustomWidgets() {
        return {
            // 示例：自定义 widget 类型
            // INTENSITY_SLIDER: (node, inputName, inputData) => { ... }
        };
    },
});
