// ═══════════════════════════════════════════════════════════════
// 消息渲染/欢迎页.js — 欢迎页与快捷操作
// NodeCraft AI — Luxury Terminal / Neo-Noir Hacker
// 从 消息渲染器.js 拆分：无消息状态耦合的独立视图
// ═══════════════════════════════════════════════════════════════

import { el, LOGO_SVG } from "../工具函数.js";
import { t } from "../i18n.js";

export function 渲染欢迎页(container, refs) {
    container.innerHTML = "";
    const welcome = el("div", { class: "nca-welcome" }, [
        el("div", { class: "nca-welcome-icon", html: LOGO_SVG }),
        el("h3", { text: t("brand.name") }),
        el("p", { text: t("chat.welcome_subtitle") }),
        el("div", { class: "nca-quick-actions" }, [
            // 新建项目走「从模板创建」：新手先挑入口类型（节点/侧边栏/菜单/状态栏），比空目录更容易上手
            创建快捷操作("⚡", t("chat.qa_new_project"), () => {
                if (refs.显示模板市场对话框) refs.显示模板市场对话框();
            }),
            创建快捷操作("◇", t("chat.qa_first_node"), () => 快捷输入(refs, t("chat.qa_first_node_prompt"))),
            创建快捷操作("▸", t("chat.qa_howto"), () => 快捷输入(refs, t("chat.qa_howto_prompt"))),
            创建快捷操作("⬡", t("chat.qa_io_types"), () => 快捷输入(refs, t("chat.qa_io_types_prompt"))),
            创建快捷操作("⧉", t("chat.qa_image_loader"), () => 快捷输入(refs, t("chat.qa_image_loader_prompt"))),
            // 新手引导入口：prompt 内嵌预筛选直接触发词（规划一下/plan），可靠命中规划机制
            创建快捷操作("📋", t("chat.qa_planning"), () => 快捷输入(refs, t("chat.qa_planning_prompt"))),
        ]),
    ]);
    container.appendChild(welcome);
}

function 创建快捷操作(icon, label, onClick) {
    const btn = el("button", { class: "nca-quick-action" }, [
        el("span", { class: "qa-icon", text: icon }),
        el("span", { text: label }),
    ]);
    btn.addEventListener("click", onClick);
    return btn;
}

function 快捷输入(refs, text) {
    refs.输入框.value = text;
    refs.输入框.dispatchEvent(new Event("input"));
    refs.输入框.focus();
}
