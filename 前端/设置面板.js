// ═══════════════════════════════════════════════════════════════
// 设置面板.js — 设置界面（Tab 页签：模型设置 + GitHub 设置）
// NodeCraft AI — Luxury Terminal / Neo-Noir Hacker
// ═══════════════════════════════════════════════════════════════

import { el, Toast, _转义HTML } from "./工具函数.js";
import { NCA_STORAGE_KEYS } from "./工具函数.js";
import { 存储 } from "./存储引擎.js";
import {
    事件总线, 事件, 状态,
    加载设置, 保存设置, 创建插件文件夹, 创建会话,
} from "./交互与状态.js";
import { t } from "./i18n.js";

// 格式化下载数
function _formatCount(n) {
    if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M';
    if (n >= 1000) return (n / 1000).toFixed(1) + 'K';
    return String(n);
}

// ═══════════════════════════════════════════════════════════════
// 文件夹浏览器对话框（基于 browse-folder 端点，浏览器环境可用）
// ═══════════════════════════════════════════════════════════════

function 显示文件夹浏览器(rootContainer, 初始路径, onSelect) {
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

// ═══════════════════════════════════════════════════════════════
// 设置面板（右侧滑入）
// ═══════════════════════════════════════════════════════════════

export async function 显示设置面板(rootContainer, 更新状态栏Fn) {
    await 加载设置();
    const s = 状态.设置;

    const overlay = el("div", { class: "nca-overlay" });
    const panel = el("div", { class: "nca-panel" });

    // Header
    const closeBtn = el("button", { class: "nca-icon-btn", text: "✕" });
    closeBtn.addEventListener("click", () => overlay.remove());

    panel.appendChild(el("div", { class: "nca-panel-header" }, [
        el("h3", { text: t("settings.title") }),
        closeBtn,
    ]));

    // 页签导航栏
    const tabNav = el("div", { class: "nca-tab-nav" });
    const tabDefs = [
        { id: "nca-settings-model", label: t("settings.tab.model") },
        { id: "nca-settings-github", label: t("settings.tab.github") },
        { id: "nca-settings-memory", label: "🧠 记忆管理" },
        { id: "nca-settings-monitor", label: t("settings.tab.monitor") },
    ];
    let _monitorTimer = null;
    let _panelAbortController = null;
    const tabButtons = [];
    tabDefs.forEach((def, idx) => {
        const btn = el("button", { class: `nca-tab-btn${idx === 0 ? " active" : ""}`, text: def.label });
        btn.addEventListener("click", () => {
            panel.querySelectorAll('.nca-tab-content').forEach(el => {
                el.style.display = 'none';
            });
            panel.querySelector('#' + def.id).style.display = 'block';
            tabButtons.forEach(b => b.classList.remove("active"));
            btn.classList.add("active");
            // 监控 Tab 激活/离开时控制刷新
            if (def.id === 'nca-settings-monitor') {
                刷新监控数据();
                _monitorTimer = setInterval(刷新监控数据, 5000);
            } else {
                if (_monitorTimer) { clearInterval(_monitorTimer); _monitorTimer = null; }
            }
            // 记忆 Tab 激活时加载记忆数据
            if (def.id === 'nca-settings-memory') {
                加载记忆数据(panel);
            }
        });
        tabButtons.push(btn);
        tabNav.appendChild(btn);
    });
    panel.appendChild(tabNav);

    // Body
    const body = el("div", { class: "nca-panel-body" });
    let 当前来源 = s.model_source || "api";

    // ══════ 页签1: 模型设置 ══════
    const modelTab = el("div", { class: "nca-tab-content", id: "nca-settings-model" });
    modelTab.style.display = "block";

    const localPathInput = el("input", { type: "text", value: s.local_path || "", placeholder: "ComfyUI/models/LLM" });
    const baseUrlInput = el("input", { type: "text", value: s.base_url || "", placeholder: "https://api.openai.com/v1" });
    const modelNameInput = el("input", { type: "text", value: s.model_name || "", placeholder: "qwen2.5-coder-32b" });
    const apiKeyInput = el("input", { type: "password", value: s.api_key || "", placeholder: "sk-..." });

    // 📂 弹出文件夹浏览器对话框（基于 browse-folder 端点，浏览器环境可用）
    const browseBtn = el("button", {
        class: "nca-btn nca-btn-sm nca-path-browse",
        text: "📂",
        title: "浏览本地文件夹…",
    });
    browseBtn.addEventListener("click", () => {
        if (browseBtn.disabled) return;
        显示文件夹浏览器(
            rootContainer,
            localPathInput.value || s.default_local_path || "",
            (选中路径) => {
                localPathInput.value = 选中路径;
                localPathInput.dispatchEvent(new Event("change", { bubbles: true }));
                Toast && Toast.show && Toast.show("已选择路径", "success");
            }
        );
    });

    const 默认路径Btn = el("button", { class: "nca-btn nca-btn-sm nca-btn-ghost", text: "↺ 默认路径" });
    默认路径Btn.addEventListener("click", () => {
        localPathInput.value = s.default_local_path || "models/LLM";
        localPathInput.dispatchEvent(new Event("change", { bubbles: true }));
    });

    const localSection = el("div", { class: "nca-form-section", style: { display: 当前来源 === "local" ? "block" : "none" } }, [
        el("div", { class: "nca-form-label", text: "本地模型" }),
        el("div", { class: "nca-field" }, [
            el("label", { text: "模型路径" }),
            el("div", { class: "nca-path-row" }, [
                localPathInput,
                browseBtn,
            ]),
        ]),
        默认路径Btn,
    ]);

    const apiSection = el("div", { class: "nca-form-section", style: { display: 当前来源 === "api" ? "block" : "none" } }, [
        el("div", { class: "nca-form-label", text: "API 配置" }),
        el("div", { class: "nca-field" }, [el("label", { text: "接口地址" }), baseUrlInput]),
        el("div", { class: "nca-field" }, [el("label", { text: "模型" }), modelNameInput]),
        el("div", { class: "nca-field" }, [el("label", { text: "API 密钥" }), apiKeyInput]),
    ]);

    modelTab.appendChild(el("div", { class: "nca-form-section" }, [
        el("div", { class: "nca-form-label", text: t("settings.model_source") }),
        (() => {
            const group = el("div", { class: "nca-radio-group" });
            const localBtn = el("button", { class: `nca-radio-btn ${当前来源 === "local" ? "selected" : ""}`, text: t("settings.local") });
            const apiBtn = el("button", { class: `nca-radio-btn ${当前来源 === "api" ? "selected" : ""}`, text: t("settings.api") });

            localBtn.addEventListener("click", () => {
                当前来源 = "local";
                localBtn.classList.add("selected");
                apiBtn.classList.remove("selected");
                localSection.style.display = "block";
                apiSection.style.display = "none";
            });
            apiBtn.addEventListener("click", () => {
                当前来源 = "api";
                apiBtn.classList.add("selected");
                localBtn.classList.remove("selected");
                localSection.style.display = "none";
                apiSection.style.display = "block";
            });
            group.appendChild(localBtn);
            group.appendChild(apiBtn);
            return group;
        })(),
    ]));

    modelTab.appendChild(localSection);
    modelTab.appendChild(apiSection);

    // ── 语言切换已迁移至顶部品牌栏语言按钮，此处不再提供设置项 ──

    // ══════ 模型市场区域 ══════
    const marketSection = el("div", { class: "nca-form-section" });
    marketSection.innerHTML = `
        <h4 style="color:#00d4ff; margin:0 0 8px 0; font-size:12px; font-family:var(--nca-font-mono);">&#x1F3EA; 模型市场</h4>
        <div style="display:flex; gap:4px; margin-bottom:8px;">
            <button id="nca-market-src-hf" class="nca-btn nca-btn-xs nca-market-src-btn active" data-source="huggingface" style="font-size:11px; padding:4px 8px; background:var(--nca-accent); color:#000; border-color:var(--nca-accent);">🤗 HuggingFace</button>
            <button id="nca-market-src-ms" class="nca-btn nca-btn-xs nca-market-src-btn" data-source="modelscope" style="font-size:11px; padding:4px 8px;">🔮 魔搭</button>
        </div>
        <div style="display:flex; gap:6px; margin-bottom:8px;">
            <input type="text" id="nca-model-search" placeholder="搜索 HuggingFace 模型..."
                   style="flex:1; padding:8px 10px; border:1px solid var(--nca-border); background:var(--nca-bg-primary); color:var(--nca-fg); border-radius:var(--nca-radius-sm); font-size:12px; font-family:var(--nca-font-mono); outline:none; box-sizing:border-box;">
            <button id="nca-model-search-btn" class="nca-btn nca-btn-sm" style="white-space:nowrap;">搜索</button>
        </div>
        <div id="nca-model-results" style="max-height:200px; overflow-y:auto;"></div>
    `;
    modelTab.appendChild(marketSection);

    // 模型市场搜索逻辑
    setTimeout(() => {
        const searchInput = panel.querySelector('#nca-model-search');
        const searchBtn = panel.querySelector('#nca-model-search-btn');
        const resultsDiv = panel.querySelector('#nca-model-results');
        let _searchTimer = null;
        let currentSource = 'huggingface';
        _panelAbortController = new AbortController();

        // 源切换按钮逻辑
        panel.querySelectorAll('.nca-market-src-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                panel.querySelectorAll('.nca-market-src-btn').forEach(b => {
                    b.classList.remove('active');
                    b.style.background = '';
                    b.style.color = '';
                    b.style.borderColor = '';
                });
                btn.classList.add('active');
                btn.style.background = 'var(--nca-accent)';
                btn.style.color = '#000';
                btn.style.borderColor = 'var(--nca-accent)';
                currentSource = btn.dataset.source;
                searchInput.placeholder = currentSource === 'modelscope'
                    ? '搜索魔搭 ModelScope 模型...'
                    : '搜索 HuggingFace 模型...';
                resultsDiv.innerHTML = '';
            });
        });

        const doSearch = async () => {
            const q = searchInput.value.trim();
            if (!q) { resultsDiv.innerHTML = ''; return; }
            searchBtn.disabled = true;
            searchBtn.textContent = '搜索中...';
            resultsDiv.innerHTML = '<div style="color:#6b7280;font-size:11px;padding:8px;">正在搜索...</div>';
            try {
                const resp = await fetch(`/ai-coder/model-market/search?q=${encodeURIComponent(q)}&limit=10&source=${currentSource}`, { signal: _panelAbortController?.signal });
                if (!resultsDiv.parentNode) return;
                const json = await resp.json();
                if (!resultsDiv.parentNode) return;
                if (json.success && json.data && json.data.length > 0) {
                    resultsDiv.innerHTML = json.data.map(m => `
                        <div class="nca-model-item">
                            <div style="flex:1;min-width:0;">
                                <div class="nca-model-name">${m.name}</div>
                                <div class="nca-model-meta">${m.author} · ⬇ ${_formatCount(m.downloads)} · ❤ ${m.likes}${m.size_mb ? ' · ' + m.size_mb + 'MB' : ''}</div>
                            </div>
                            <button class="nca-model-download-btn" data-model-id="${m.id}">下载</button>
                        </div>
                    `).join('');
                    // 绑定下载按钮
                    resultsDiv.querySelectorAll('.nca-model-download-btn').forEach(btn => {
                        btn.addEventListener('click', async () => {
                            const modelId = btn.dataset.modelId;
                            btn.disabled = true;
                            btn.textContent = '启动中...';
                            try {
                                const r = await fetch('/ai-coder/model-market/download', {
                                    method: 'POST',
                                    headers: {'Content-Type': 'application/json'},
                                    body: JSON.stringify({model_id: modelId, source: currentSource})
                                });
                                const rj = await r.json();
                                if (rj.success) {
                                    btn.textContent = '✓ 已启动';
                                    btn.style.color = '#10b981';
                                    btn.style.borderColor = 'rgba(16,185,129,0.4)';
                                } else {
                                    btn.textContent = rj.message || '失败';
                                    btn.style.color = '#ef4444';
                                }
                            } catch (e) {
                                btn.textContent = '网络错误';
                                btn.style.color = '#ef4444';
                            }
                        });
                    });
                } else {
                    resultsDiv.innerHTML = '<div style="color:#6b7280;font-size:11px;padding:8px;text-align:center;">未找到相关模型</div>';
                }
            } catch (e) {
                if (e && e.name === 'AbortError') return;
                if (!resultsDiv.parentNode) return;
                resultsDiv.innerHTML = '<div style="color:#ef4444;font-size:11px;padding:8px;">无法连接模型市场</div>';
            } finally {
                if (resultsDiv.parentNode) {
                    searchBtn.disabled = false;
                    searchBtn.textContent = '搜索';
                }
            }
        };

        // 防抖 300ms
        searchInput.addEventListener('input', () => {
            clearTimeout(_searchTimer);
            _searchTimer = setTimeout(doSearch, 300);
        });
        searchInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') { clearTimeout(_searchTimer); doSearch(); }
        });
        searchBtn.addEventListener('click', () => { clearTimeout(_searchTimer); doSearch(); });
    }, 0);

    body.appendChild(modelTab);

    // ══════ 页签2: GitHub ══════
    const githubTab = el("div", { class: "nca-tab-content", id: "nca-settings-github" });
    githubTab.style.display = "none";

    const githubTokenInput = el("input", { type: "password", value: s.github_token || "", placeholder: "ghp_xxxx... 或 github_pat_xxxx..." });
    const githubUserInput = el("input", { type: "text", value: s.github_username || "", placeholder: "your-username" });

    const publicRadio = el("input", { type: "radio", name: "github_visibility", value: "public" });
    if ((s.github_visibility || "public") === "public") publicRadio.checked = true;
    const privateRadio = el("input", { type: "radio", name: "github_visibility", value: "private" });
    if (s.github_visibility === "private") privateRadio.checked = true;

    const testConnBtn = el("button", { class: "nca-btn nca-btn-sm", text: "🔍 测试连接" });
    testConnBtn.addEventListener("click", async () => {
        testConnBtn.disabled = true;
        testConnBtn.textContent = "测试中...";
        try {
            const resp = await fetch("/ai-coder/github-test-connection", { method: "POST" });
            const data = await resp.json();
            if (data.valid) {
                testConnBtn.textContent = "✓ 连接成功";
                testConnBtn.style.color = "#00ffc8";
            } else {
                testConnBtn.textContent = "✗ " + (data.message || "连接失败");
                testConnBtn.style.color = "#ff4444";
            }
        } catch (e) {
            testConnBtn.textContent = "✗ 网络错误";
            testConnBtn.style.color = "#ff4444";
        }
        setTimeout(() => {
            testConnBtn.disabled = false;
            testConnBtn.textContent = "🔍 测试连接";
            testConnBtn.style.color = "";
        }, 3000);
    });

    const helpText = el("div", { class: "nca-help-text" });
    helpText.innerHTML = '💡 <a href="https://github.com/settings/tokens" target="_blank" style="color: #00d4ff;">创建 Token</a> → 勾选 "repo" 权限 → 设置过期时间';
    helpText.style.cssText = "font-size: 11px; color: #888; margin-top: 8px; line-height: 1.6;";

    githubTab.appendChild(el("div", { class: "nca-form-section" }, [
        el("div", { class: "nca-form-label", text: "🔗 GitHub 设置" }),
        el("div", { class: "nca-field" }, [el("label", { text: "Personal Access Token" }), githubTokenInput]),
        el("div", { class: "nca-field" }, [el("label", { text: "GitHub 用户名" }), githubUserInput]),
        el("div", { class: "nca-field" }, [
            el("label", { text: "默认仓库可见性" }),
            el("div", { class: "nca-radio-group" }, [
                publicRadio,
                el("label", { text: " Public（公开）", style: { marginRight: "16px", cursor: "pointer" } }),
                privateRadio,
                el("label", { text: " Private（私有）", style: { cursor: "pointer" } }),
            ]),
        ]),
        testConnBtn,
        helpText,
    ]));

    body.appendChild(githubTab);

    // ══════ 页签3: 记忆管理 ══════
    const memoryTab = el("div", { class: "nca-tab-content", id: "nca-settings-memory" });
    memoryTab.style.display = "none";
    memoryTab.innerHTML = `
        <div class="nca-form-section">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
                <h4 style="color:#00d4ff; margin:0; font-size:12px; font-family:var(--nca-font-mono);">🧠 跨会话记忆</h4>
                <div style="display:flex; gap:6px;">
                    <button id="nca-memory-refresh" class="nca-btn nca-btn-xs" style="font-size:11px; padding:4px 8px;">刷新</button>
                    <button id="nca-memory-clear" class="nca-btn nca-btn-xs nca-btn-danger" style="font-size:11px; padding:4px 8px;">清除全部</button>
                </div>
            </div>
            <div id="nca-memory-content" style="max-height:400px; overflow-y:auto; font-size:12px;">
                <div style="color:#6b7280; padding:12px; text-align:center;">点击"刷新"加载记忆数据</div>
            </div>
        </div>
    `;
    body.appendChild(memoryTab);

    // 记忆数据加载与渲染
    async function 加载记忆数据(panelRef) {
        const contentDiv = (panelRef || panel).querySelector('#nca-memory-content');
        if (!contentDiv) return;
        contentDiv.innerHTML = '<div style="color:#6b7280; padding:12px; text-align:center;">加载中...</div>';
        try {
            const resp = await fetch('/ai-coder/memories');
            const json = await resp.json();
            if (!json.success || !json.data) {
                contentDiv.innerHTML = '<div style="color:#ef4444; padding:8px;">加载失败</div>';
                return;
            }
            const data = json.data;
            const 全局 = data["全局记忆"] || {};
            const 偏好列表 = 全局["用户偏好"] || [];
            const 问题列表 = 全局["常见问题"] || [];
            const 插件记忆 = data["插件记忆"] || {};
            let html = '';

            // 用户偏好
            html += '<div style="margin-bottom:12px;">';
            html += '<div style="color:var(--nca-accent); font-size:11px; margin-bottom:4px; font-family:var(--nca-font-mono);">[用户偏好]</div>';
            if (偏好列表.length === 0) {
                html += '<div style="color:#6b7280; padding:4px 0 4px 12px; font-size:11px;">暂无记录</div>';
            } else {
                偏好列表.forEach((item, i) => {
                    html += `<div style="display:flex; justify-content:space-between; align-items:center; padding:4px 0 4px 12px; border-bottom:1px solid rgba(255,255,255,0.05);">
                        <span style="flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; color:var(--nca-fg-dim);">- ${_转义HTML(item.value || item.key || '')}</span>
                        <button class="nca-memory-del-btn" data-type="用户偏好" data-index="${i}" style="background:none; border:none; color:#ef4444; cursor:pointer; font-size:11px; padding:2px 6px; flex-shrink:0;">✕</button>
                    </div>`;
                });
            }
            html += '</div>';

            // 常见问题
            html += '<div style="margin-bottom:12px;">';
            html += '<div style="color:var(--nca-accent); font-size:11px; margin-bottom:4px; font-family:var(--nca-font-mono);">[历史问题与解决方案]</div>';
            if (问题列表.length === 0) {
                html += '<div style="color:#6b7280; padding:4px 0 4px 12px; font-size:11px;">暂无记录</div>';
            } else {
                问题列表.forEach((item, i) => {
                    html += `<div style="display:flex; justify-content:space-between; align-items:flex-start; padding:4px 0 4px 12px; border-bottom:1px solid rgba(255,255,255,0.05);">
                        <div style="flex:1; min-width:0;">
                            <div style="color:var(--nca-fg-dim); font-size:11px;">Q: ${_转义HTML(item.question || '')}</div>
                            <div style="color:#6b7280; font-size:10px; margin-top:2px;">A: ${_转义HTML((item.solution || '').substring(0, 80))}${(item.solution || '').length > 80 ? '...' : ''}</div>
                        </div>
                        <button class="nca-memory-del-btn" data-type="常见问题" data-index="${i}" style="background:none; border:none; color:#ef4444; cursor:pointer; font-size:11px; padding:2px 6px; flex-shrink:0;">✕</button>
                    </div>`;
                });
            }
            html += '</div>';

            // 插件记忆
            const 插件Keys = Object.keys(插件记忆);
            if (插件Keys.length > 0) {
                html += '<div>';
                html += '<div style="color:var(--nca-accent); font-size:11px; margin-bottom:4px; font-family:var(--nca-font-mono);">[插件记忆]</div>';
                插件Keys.forEach(插件名 => {
                    const pm = 插件记忆[插件名];
                    const 信息 = pm["项目信息"] || {};
                    const 上下文列表 = pm["上下文"] || [];
                    html += `<div style="padding:4px 0 4px 12px; border-bottom:1px solid rgba(255,255,255,0.05);">
                        <div style="color:var(--nca-fg); font-size:11px; font-weight:600;">📦 ${_转义HTML(插件名)}</div>`;
                    if (信息["技术栈"] && 信息["技术栈"].length > 0) {
                        html += `<div style="color:var(--nca-fg-dim); font-size:10px; margin-top:2px;">技术栈: ${_转义HTML(信息["技术栈"].join(', '))}</div>`;
                    }
                    if (信息["最后活跃"]) {
                        html += `<div style="color:#6b7280; font-size:10px;">最后活跃: ${_转义HTML(信息["最后活跃"])}</div>`;
                    }
                    上下文列表.forEach((ctx, i) => {
                        html += `<div style="display:flex; justify-content:space-between; align-items:center; margin-top:2px;">
                            <span style="flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; color:var(--nca-fg-dim); font-size:10px;">- ${_转义HTML(ctx.content || '')}</span>
                            <button class="nca-memory-del-btn" data-type="上下文" data-index="${i}" data-plugin="${插件名}" style="background:none; border:none; color:#ef4444; cursor:pointer; font-size:11px; padding:2px 6px; flex-shrink:0;">✕</button>
                        </div>`;
                    });
                    html += `</div>`;
                });
                html += '</div>';
            }

            if (!偏好列表.length && !问题列表.length && !插件Keys.length) {
                html = '<div style="color:#6b7280; padding:12px; text-align:center;">暂无记忆数据</div>';
            }
            contentDiv.innerHTML = html;

            // 绑定删除按钮
            contentDiv.querySelectorAll('.nca-memory-del-btn').forEach(btn => {
                btn.addEventListener('click', async () => {
                    const type = btn.dataset.type;
                    const index = parseInt(btn.dataset.index);
                    const plugin = btn.dataset.plugin || null;
                    try {
                        const headers = { 'Content-Type': 'application/json' };
                        try {
                            const token = localStorage.getItem('ComfyCommunity_Token') || sessionStorage.getItem('ComfyCommunity_Token');
                            if (token) headers['Authorization'] = `Bearer ${token}`;
                        } catch (_) {}
                        const resp = await fetch('/ai-coder/memories', {
                            method: 'DELETE',
                            headers,
                            body: JSON.stringify({ type: 'item', 记忆类型: type, index, plugin_path: plugin }),
                        });
                        const rj = await resp.json();
                        if (rj.success) {
                            加载记忆数据(panelRef);
                            Toast && Toast.show && Toast.show('已删除', 'success');
                        } else {
                            Toast && Toast.show && Toast.show(rj.message || '删除失败', 'error');
                        }
                    } catch (e) {
                        Toast && Toast.show && Toast.show(`删除失败: ${e.message || e}`, 'error');
                    }
                });
            });
        } catch (e) {
            contentDiv.innerHTML = '<div style="color:#ef4444; padding:8px;">加载失败</div>';
        }
    }

    // 记忆 Tab 按钮事件绑定（延迟绑定确保 DOM 已创建）
    setTimeout(() => {
        const refreshBtn = panel.querySelector('#nca-memory-refresh');
        const clearBtn = panel.querySelector('#nca-memory-clear');
        if (refreshBtn) {
            refreshBtn.addEventListener('click', () => 加载记忆数据(panel));
        }
        if (clearBtn) {
            clearBtn.addEventListener('click', async () => {
                if (!confirm('确定要清除所有全局记忆吗？此操作不可撤销。')) return;
                clearBtn.disabled = true;
                clearBtn.textContent = '清除中...';
                try {
                    const headers = { 'Content-Type': 'application/json' };
                    try {
                        const token = localStorage.getItem('ComfyCommunity_Token') || sessionStorage.getItem('ComfyCommunity_Token');
                        if (token) headers['Authorization'] = `Bearer ${token}`;
                    } catch (_) {}
                    const resp = await fetch('/ai-coder/memories', {
                        method: 'DELETE',
                        headers,
                        body: JSON.stringify({}),
                    });
                    const rj = await resp.json();
                    if (rj.success) {
                        Toast && Toast.show && Toast.show('已清除全部记忆', 'success');
                        加载记忆数据(panel);
                    } else {
                        Toast && Toast.show && Toast.show(rj.message || '清除失败', 'error');
                    }
                } catch (e) {
                    Toast && Toast.show && Toast.show(`清除失败: ${e.message || e}`, 'error');
                } finally {
                    clearBtn.disabled = false;
                    clearBtn.textContent = '清除全部';
                }
            });
        }
    }, 0);

    // ══════ 页签4: 监控 ══════
    const monitorTab = el("div", { class: "nca-tab-content", id: "nca-settings-monitor" });
    monitorTab.style.display = "none";
    monitorTab.innerHTML = `
        <div class="nca-metrics-grid">
            <div class="nca-metric-card">
                <div class="nca-metric-label">QPS</div>
                <div class="nca-metric-value" id="nca-m-qps">--</div>
            </div>
            <div class="nca-metric-card">
                <div class="nca-metric-label">P90 延迟</div>
                <div class="nca-metric-value" id="nca-m-p90">--</div>
            </div>
            <div class="nca-metric-card">
                <div class="nca-metric-label">错误率</div>
                <div class="nca-metric-value" id="nca-m-error">--</div>
            </div>
            <div class="nca-metric-card">
                <div class="nca-metric-label">连接数</div>
                <div class="nca-metric-value" id="nca-m-conn">--</div>
            </div>
            <div class="nca-metric-card">
                <div class="nca-metric-label">推理/分钟</div>
                <div class="nca-metric-value" id="nca-m-infer">--</div>
            </div>
            <div class="nca-metric-card">
                <div class="nca-metric-label">运行时间</div>
                <div class="nca-metric-value" id="nca-m-uptime">--</div>
            </div>
        </div>
    `;
    body.appendChild(monitorTab);

    async function 刷新监控数据() {
        try {
            const resp = await fetch('/ai-coder/metrics');
            const { data } = await resp.json();
            if (!data) return;
            const qpsEl = panel.querySelector('#nca-m-qps');
            if (qpsEl) qpsEl.textContent = data.qps;
            const p90El = panel.querySelector('#nca-m-p90');
            if (p90El) p90El.textContent = data.latency.p90_ms + 'ms';
            const errorEl = panel.querySelector('#nca-m-error');
            if (errorEl) errorEl.textContent = data.error_rate_percent + '%';
            const connEl = panel.querySelector('#nca-m-conn');
            if (connEl) connEl.textContent = data.active_connections;
            const inferEl = panel.querySelector('#nca-m-infer');
            if (inferEl) inferEl.textContent = data.model.inferences_1m;
            const uptimeEl = panel.querySelector('#nca-m-uptime');
            if (uptimeEl) {
                const hours = Math.floor(data.uptime_seconds / 3600);
                const mins = Math.floor((data.uptime_seconds % 3600) / 60);
                uptimeEl.textContent = `${hours}h ${mins}m`;
            }
        } catch (e) {
            // 静默失败
        }
    }

    // 保存按钮
    const saveBtn = el("button", { class: "nca-btn nca-btn-primary", text: t("settings.save") });
    saveBtn.addEventListener("click", async () => {
        saveBtn.textContent = t("common.saving");
        saveBtn.disabled = true;
        const success = await 保存设置({
            model_source: 当前来源,
            local_path: localPathInput.value,
            base_url: baseUrlInput.value,
            model_name: modelNameInput.value,
            api_key: apiKeyInput.value,
            github_token: githubTokenInput.value.trim(),
            github_username: githubUserInput.value.trim(),
            github_visibility: panel.querySelector('input[name="github_visibility"]:checked')?.value || "public",
        });
        if (success) {
            Toast.success(t("settings.saved"));
            overlay.remove();
            if (更新状态栏Fn) 更新状态栏Fn(t("common.ready"));
        } else {
            saveBtn.textContent = t("common.retry");
            saveBtn.disabled = false;
        }
    });
    body.appendChild(saveBtn);

    panel.appendChild(body);
    overlay.appendChild(panel);
    overlay.addEventListener("click", (e) => {
        if (e.target === overlay) {
            if (_monitorTimer) { clearInterval(_monitorTimer); _monitorTimer = null; }
            if (_panelAbortController) { try { _panelAbortController.abort(); } catch (_) {} _panelAbortController = null; }
            overlay.remove();
        }
    });
    // 关闭按钮也需清理定时器与未完成的 fetch
    closeBtn.addEventListener("click", () => {
        if (_monitorTimer) { clearInterval(_monitorTimer); _monitorTimer = null; }
        if (_panelAbortController) { try { _panelAbortController.abort(); } catch (_) {} _panelAbortController = null; }
    }, { once: true });
    rootContainer.appendChild(overlay);
}

// ═══════════════════════════════════════════════════════════════
// 创建项目对话框
// ═══════════════════════════════════════════════════════════════

export function 显示创建项目对话框(rootContainer, refs) {
    const overlay = el("div", { class: "nca-overlay center-modal" });
    const modal = el("div", { class: "nca-modal" });

    const closeBtn = el("button", { class: "nca-icon-btn", text: "✕" });
    closeBtn.addEventListener("click", () => overlay.remove());

    modal.appendChild(el("div", { class: "nca-modal-header" }, [
        el("h3", { text: t("project.new") }),
        closeBtn,
    ]));

    const body = el("div", { class: "nca-modal-body" });
    const nameInput = el("input", { type: "text", placeholder: t("project.name_placeholder") });
    const preview = el("div", { class: "nca-create-preview", text: "ComfyUI/custom_nodes/..." });
    const errorMsg = el("div", { class: "nca-error-text", style: { display: "none" } });

    const validPattern = /^[a-zA-Z0-9_-]+$/;

    nameInput.addEventListener("input", () => {
        const val = nameInput.value;
        if (!val) {
            preview.textContent = "ComfyUI/custom_nodes/...";
            preview.classList.remove("valid");
            errorMsg.style.display = "none";
            nameInput.classList.remove("nca-input-error", "nca-input-valid");
        } else if (!validPattern.test(val)) {
            nameInput.classList.add("nca-input-error");
            nameInput.classList.remove("nca-input-valid");
            errorMsg.textContent = t("project.name_invalid");
            errorMsg.style.display = "block";
            preview.textContent = "";
            preview.classList.remove("valid");
        } else {
            nameInput.classList.remove("nca-input-error");
            nameInput.classList.add("nca-input-valid");
            errorMsg.style.display = "none";
            preview.textContent = `ComfyUI/custom_nodes/${val}/`;
            preview.classList.add("valid");
        }
    });

    body.appendChild(el("div", { class: "nca-field" }, [
        el("label", { text: t("project.name_label") }),
        nameInput,
        errorMsg,
    ]));
    body.appendChild(preview);

    const createBtn = el("button", { class: "nca-btn nca-btn-primary", text: t("common.create") });
    createBtn.addEventListener("click", async () => {
        const name = nameInput.value.trim();
        if (!name || !validPattern.test(name)) {
            nameInput.classList.add("nca-input-error");
            errorMsg.textContent = t("project.name_required");
            errorMsg.style.display = "block";
            return;
        }
        createBtn.textContent = t("common.creating");
        createBtn.disabled = true;

        const result = await 创建插件文件夹(name);
        if (result.success) {
            Toast.success(t("project.created").replace(/^✓\s*/, ''));
            overlay.remove();
            // 异步写入 IndexedDB（存储引擎内部同步刷新 localStorage 影子缓存）
            存储.写入(NCA_STORAGE_KEYS.plugin, name).catch(() => {});
            await 创建会话(name, name);
        } else {
            createBtn.textContent = t("common.create");
            createBtn.disabled = false;
            errorMsg.textContent = result.message || t("project.create_failed");
            errorMsg.style.display = "block";
        }
    });
    body.appendChild(createBtn);

    modal.appendChild(body);
    overlay.appendChild(modal);
    overlay.addEventListener("click", (e) => { if (e.target === overlay) overlay.remove(); });
    rootContainer.appendChild(overlay);

    nameInput.focus();
}

// ═══════════════════════════════════════════════════════════════
// 删除确认对话框
// ═══════════════════════════════════════════════════════════════

export function 显示删除确认(rootContainer, session, 删除会话Fn) {
    const overlay = el("div", { class: "nca-overlay center-modal" });
    const modal = el("div", { class: "nca-modal" });

    const closeBtn = el("button", { class: "nca-icon-btn", text: "✕" });
    closeBtn.addEventListener("click", () => overlay.remove());

    modal.appendChild(el("div", { class: "nca-modal-header" }, [
        el("h3", { text: `⚠ ${t("common.confirm")}` }),
        closeBtn,
    ]));

    const body = el("div", { class: "nca-modal-body" });
    modal.appendChild(body);
    overlay.appendChild(modal);
    overlay.addEventListener("click", (e) => { if (e.target === overlay) overlay.remove(); });
    rootContainer.appendChild(overlay);

    const hasFolder = !!(session && session.plugin_folder);

    // 主视图：根据是否有 plugin_folder 提供两或三个选项
    const 渲染主视图 = () => {
        body.innerHTML = "";
        body.appendChild(el("p", {
            text: t("session.confirm_delete", { title: session.title || t("session.untitled") }),
            style: { margin: "0", fontSize: "12px", color: "var(--nca-fg-dim)", lineHeight: "1.6" },
        }));

        const actions = el("div", { class: "nca-confirm-actions" });

        const cancelBtn = el("button", { class: "nca-btn nca-btn-sm", text: t("common.cancel") });
        cancelBtn.addEventListener("click", () => overlay.remove());

        const deleteOnlyBtn = el("button", { class: "nca-btn nca-btn-sm", text: t("session.delete_only") });
        deleteOnlyBtn.addEventListener("click", async () => {
            deleteOnlyBtn.textContent = "...";
            deleteOnlyBtn.disabled = true;
            await 删除会话Fn(session.id, false);
            overlay.remove();
            Toast.success(t("session.deleted"));
        });

        actions.appendChild(cancelBtn);
        actions.appendChild(deleteOnlyBtn);

        if (hasFolder) {
            const deleteWithFolderBtn = el("button", { class: "nca-btn nca-btn-sm nca-btn-danger", text: t("session.delete_with_folder") });
            deleteWithFolderBtn.addEventListener("click", () => 渲染二次确认());
            actions.appendChild(deleteWithFolderBtn);
        }

        body.appendChild(actions);
    };

    // 二次确认视图：同时删除文件夹的危险提示
    const 渲染二次确认 = () => {
        body.innerHTML = "";
        body.appendChild(el("p", {
            text: t("session.confirm_delete_folder", { folder: session.plugin_folder }),
            style: { margin: "0", fontSize: "12px", color: "var(--nca-danger, #ff5b5b)", lineHeight: "1.6", fontWeight: "600" },
        }));

        const actions = el("div", { class: "nca-confirm-actions" });

        const backBtn = el("button", { class: "nca-btn nca-btn-sm", text: t("session.delete_back") });
        backBtn.addEventListener("click", () => 渲染主视图());

        const confirmBtn = el("button", { class: "nca-btn nca-btn-sm nca-btn-danger", text: t("session.delete_confirm_btn") });
        confirmBtn.addEventListener("click", async () => {
            confirmBtn.textContent = "...";
            confirmBtn.disabled = true;
            backBtn.disabled = true;
            await 删除会话Fn(session.id, true);
            overlay.remove();
            Toast.success(t("session.deleted_with_folder"));
        });

        actions.appendChild(backBtn);
        actions.appendChild(confirmBtn);
        body.appendChild(actions);
    };

    渲染主视图();
}
