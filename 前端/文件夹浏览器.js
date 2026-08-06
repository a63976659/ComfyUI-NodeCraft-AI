// ═══════════════════════════════════════════════════════════════
// 文件夹浏览器.js — 文件夹浏览器对话框（基于 browse-folder 端点）
// NodeCraft AI — Luxury Terminal / Neo-Noir Hacker
// ═══════════════════════════════════════════════════════════════

import { el, _转义HTML } from "./工具函数.js";

// ═══════════════════════════════════════════════════════════════
// 文件夹浏览器对话框（基于 browse-folder 端点，浏览器环境可用）
// ═══════════════════════════════════════════════════════════════

export function 显示文件夹浏览器(rootContainer, 初始路径, onSelect) {
    const overlay = el("div", { class: "nca-overlay center-modal" });
    const modal = el("div", { class: "nca-modal", style: { maxWidth: "480px" } });

    const closeBtn = el("button", { class: "nca-icon-btn", text: "✕" });
    const 关闭 = () => overlay.remove();
    closeBtn.addEventListener("click", 关闭);

    modal.appendChild(el("div", { class: "nca-modal-header" }, [
        el("h3", { text: "📂 选择文件夹" }),
        closeBtn,
    ]));

    const body = el("div", { class: "nca-modal-body" });

    // 路径栏：上级按钮 + 当前路径显示
    const 上级按钮 = el("button", { class: "nca-btn nca-btn-sm", text: "↑", title: "上级目录" });
    const 当前路径 = el("div", {
        style: {
            flex: "1", fontSize: "11px", fontFamily: "var(--nca-font-mono)",
            color: "var(--nca-fg-dim)", overflow: "hidden", textOverflow: "ellipsis",
            whiteSpace: "nowrap", padding: "4px 8px", background: "var(--nca-bg-primary)",
            border: "1px solid var(--nca-border)", borderRadius: "var(--nca-radius-sm)",
        }
    });
    body.appendChild(el("div", { style: { display: "flex", gap: "6px", marginBottom: "8px", alignItems: "center" } }, [
        上级按钮, 当前路径,
    ]));

    // 文件夹列表
    const 列表 = el("div", {
        style: {
            maxHeight: "300px", overflowY: "auto",
            border: "1px solid var(--nca-border)", borderRadius: "var(--nca-radius-sm)",
        }
    });
    body.appendChild(列表);

    // 操作按钮
    const 取消按钮 = el("button", { class: "nca-btn nca-btn-sm", text: "取消" });
    取消按钮.addEventListener("click", 关闭);
    const 确认按钮 = el("button", { class: "nca-btn nca-btn-primary", text: "选择此目录" });
    body.appendChild(el("div", { style: { display: "flex", gap: "8px", marginTop: "12px", justifyContent: "flex-end" } }, [
        取消按钮, 确认按钮,
    ]));

    modal.appendChild(body);
    overlay.appendChild(modal);
    overlay.addEventListener("click", (e) => { if (e.target === overlay) 关闭(); });

    let _当前目录 = "";

    async function 加载目录(path) {
        列表.innerHTML = '<div style="color:#6b7280; padding:16px; text-align:center; font-size:11px;">加载中...</div>';
        确认按钮.disabled = true;
        try {
            const headers = { "Content-Type": "application/json" };
            try {
                const token = localStorage.getItem("ComfyCommunity_Token") || sessionStorage.getItem("ComfyCommunity_Token");
                if (token) headers["Authorization"] = `Bearer ${token}`;
            } catch (_) {}
            const resp = await fetch("/ai-coder/browse-folder", {
                method: "POST",
                headers,
                body: JSON.stringify({ initial_dir: path || "" }),
            });
            const data = await resp.json().catch(() => ({}));
            if (resp.ok && data && data.status === "success") {
                _当前目录 = data.current_dir || "";
                当前路径.textContent = _当前目录;
                const folders = data.folders || [];
                列表.innerHTML = "";
                if (folders.length === 0) {
                    列表.innerHTML = '<div style="color:#6b7280; padding:16px; text-align:center; font-size:11px;">没有子文件夹</div>';
                } else {
                    folders.forEach(f => {
                        const item = el("div", {
                            text: "📁 " + f.name,
                            style: {
                                padding: "6px 12px", cursor: "pointer", fontSize: "12px",
                                borderBottom: "1px solid rgba(255,255,255,0.05)",
                                transition: "background 0.15s",
                            }
                        });
                        item.addEventListener("mouseenter", () => { item.style.background = "rgba(0,212,255,0.08)"; });
                        item.addEventListener("mouseleave", () => { item.style.background = ""; });
                        item.addEventListener("click", () => 加载目录(f.path));
                        列表.appendChild(item);
                    });
                }
                确认按钮.disabled = false;
            } else {
                列表.innerHTML = `<div style="color:#ef4444; padding:16px; text-align:center; font-size:11px;">${_转义HTML((data && data.error) || "加载失败")}</div>`;
            }
        } catch (e) {
            列表.innerHTML = `<div style="color:#ef4444; padding:16px; text-align:center; font-size:11px;">网络错误: ${_转义HTML(e.message || String(e))}</div>`;
        }
    }

    上级按钮.addEventListener("click", () => {
        if (!_当前目录) return;
        const trimmed = _当前目录.replace(/[\\/]+$/, "");
        const parent = trimmed.replace(/[\\/][^\\/]*$/, "");
        if (parent && parent !== trimmed) 加载目录(parent);
    });

    确认按钮.addEventListener("click", () => {
        if (_当前目录) {
            onSelect(_当前目录);
            关闭();
        }
    });

    rootContainer.appendChild(overlay);
    加载目录(初始路径 || "");
}
