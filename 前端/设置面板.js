// ═══════════════════════════════════════════════════════════════
// 设置面板.js — 设置界面（Tab 页签：模型设置 + GitHub 设置）
// NodeCraft AI — Luxury Terminal / Neo-Noir Hacker
// ═══════════════════════════════════════════════════════════════

import { el, Toast, _转义HTML } from "./工具函数.js";
import { NCA_STORAGE_KEYS } from "./工具函数.js";
import { 存储 } from "./存储引擎.js";
import {
    事件总线, 事件, 状态,
    加载设置, 保存设置, 创建插件文件夹, 创建会话, 请求,
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

// 从云端获取可用模型列表（多提供商分组）
// 模块级缓存：按 cloud_url 缓存已拉取的模型列表，避免各面板/底部栏频繁重复请求
let _云端模型缓存 = { url: "", models: null };

// 使缓存失效（云端地址或凭据变更后可调用，下次将强制重新拉取）
export function 清除云端模型缓存() {
    _云端模型缓存 = { url: "", models: null };
}

export async function 加载云端模型列表(selectEl, settings) {
    const cloudUrl = settings.cloud_url || "";
    if (!cloudUrl) {
        selectEl.innerHTML = "";
        const opt = document.createElement("option");
        opt.value = "";
        opt.textContent = "请先配置云端地址";
        opt.disabled = true;
        opt.selected = true;
        selectEl.appendChild(opt);
        return;
    }
    try {
        let models;
        if (_云端模型缓存.url === cloudUrl && _云端模型缓存.models) {
            // 命中缓存，直接复用
            models = _云端模型缓存.models;
        } else {
            const resp = await fetch(`${cloudUrl}/api/open/available-models`);
            if (!resp.ok) return;
            const data = await resp.json();
            models = data.models || [];
            _云端模型缓存 = { url: cloudUrl, models };
        }
        {
            selectEl.innerHTML = "";
            // 按提供商分组
            const groups = {};
            for (const m of models) {
                const prov = m.provider || "default";
                if (!groups[prov]) groups[prov] = [];
                groups[prov].push(m);
            }
            for (const provName of Object.keys(groups)) {
                const group = document.createElement("optgroup");
                group.label = provName;
                for (const m of groups[provName]) {
                    const opt = document.createElement("option");
                    opt.value = m.id;
                    opt.textContent = (m.is_default ? "★ " : "") + m.name;
                    if (m.is_free) opt.dataset.isFree = "true";
                    if (m.enabled === false) {
                        opt.disabled = true;
                        opt.textContent += " [未启用]";
                    }
                    group.appendChild(opt);
                }
                selectEl.appendChild(group);
            }
            // 选中当前设置的模型
            if (settings.model_name) {
                selectEl.value = settings.model_name;
            }
        }
    } catch(e) {
        console.warn("加载云端模型列表失败:", e);
    }
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

    // ══════ 页签1: 模型设置 ══════
    const modelTab = el("div", { class: "nca-tab-content", id: "nca-settings-model" });
    modelTab.style.display = "block";

    const localPathInput = el("input", { type: "text", value: s.local_path || "", placeholder: "ComfyUI/models/LLM" });

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

    const localSection = el("div", { class: "nca-form-section" }, [
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


    modelTab.appendChild(localSection);

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

    // ══════ 高级设置区域 ══════
    // 智能任务规划开关：默认开启（enable_planning 未定义时视为 true）
    const planningToggle = el("input", { type: "checkbox" });
    planningToggle.checked = s.enable_planning !== false;
    // 主动踩坑预警开关：默认开启（proactive_pitfall_check 未定义时视为 true）
    const pitfallToggle = el("input", { type: "checkbox" });
    pitfallToggle.checked = s.proactive_pitfall_check !== false;
    // 工具调用最大轮次选择：默认25，可选 25 / 100 / 无限(-1)
    const 轮次Select = el("select", {
        style: {
            padding: "6px 10px", border: "1px solid var(--nca-border)",
            background: "var(--nca-bg-primary)", color: "var(--nca-fg)",
            borderRadius: "var(--nca-radius-sm)", fontSize: "12px",
            fontFamily: "var(--nca-font-mono)", outline: "none", cursor: "pointer",
        }
    });
    [
        { v: "25", label: "25（推荐）" },
        { v: "100", label: "100" },
        { v: "-1", label: "无限（有死循环和成本风险）" },
    ].forEach(o => {
        const opt = document.createElement("option");
        opt.value = o.v;
        opt.textContent = o.label;
        轮次Select.appendChild(opt);
    });
    轮次Select.value = String(s.max_tool_rounds != null ? s.max_tool_rounds : 25);
    if (!["25", "100", "-1"].includes(轮次Select.value)) 轮次Select.value = "25";
    modelTab.appendChild(el("div", { class: "nca-form-section" }, [
        el("div", { class: "nca-form-label", text: "⚙ 高级设置" }),
        el("label", {
            style: {
                display: "flex", alignItems: "center", justifyContent: "space-between",
                gap: "12px", cursor: "pointer", padding: "4px 0",
            }
        }, [
            el("div", { style: { flex: "1", minWidth: "0" } }, [
                el("div", { text: "智能任务规划", style: { fontSize: "12px", color: "var(--nca-fg)" } }),
                el("div", {
                    text: "复杂任务执行前展示执行计划并征询确认",
                    style: { fontSize: "11px", color: "var(--nca-fg-dim)", marginTop: "2px", lineHeight: "1.5" }
                }),
            ]),
            planningToggle,
        ]),
        el("label", {
            style: {
                display: "flex", alignItems: "center", justifyContent: "space-between",
                gap: "12px", cursor: "pointer", padding: "4px 0",
            }
        }, [
            el("div", { style: { flex: "1", minWidth: "0" } }, [
                el("div", { text: "主动踩坑预警", style: { fontSize: "12px", color: "var(--nca-fg)" } }),
                el("div", {
                    text: "AI 编码前主动检索历史踩坑记录，提前规避已知问题",
                    style: { fontSize: "11px", color: "var(--nca-fg-dim)", marginTop: "2px", lineHeight: "1.5" }
                }),
            ]),
            pitfallToggle,
        ]),
        el("label", {
            style: {
                display: "flex", alignItems: "center", justifyContent: "space-between",
                gap: "12px", cursor: "pointer", padding: "4px 0",
            }
        }, [
            el("div", { style: { flex: "1", minWidth: "0" } }, [
                el("div", { text: "工具调用最大轮次", style: { fontSize: "12px", color: "var(--nca-fg)" } }),
                el("div", {
                    text: "单次任务允许的工具调用轮数上限，默认 25（推荐）",
                    style: { fontSize: "11px", color: "var(--nca-fg-dim)", marginTop: "2px", lineHeight: "1.5" }
                }),
            ]),
            轮次Select,
        ]),
    ]));

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

    // Token 输入框下方的详细引导
    const tokenHint = el("div", { class: "nca-help-text" });
    tokenHint.innerHTML = '💡 推荐使用 Classic Token，创建时勾选 "repo" 权限即可<br>ℹ️ Repository access 请选择 "All repositories"（同步时会自动创建新仓库）';
    tokenHint.style.cssText = "font-size: 11px; color: var(--nca-fg-dim, #888); margin-top: 6px; line-height: 1.6;";

    // GitHub 设置区域底部的功能说明
    const featureHint = el("div", { class: "nca-help-text" });
    featureHint.innerHTML = '💡 同步时会自动匹配已有仓库，不存在则自动创建<br>ℹ️ 每个插件可同步到不同仓库，仓库名默认为插件名';
    featureHint.style.cssText = "font-size: 11px; color: var(--nca-fg-dim, #888); margin-top: 10px; padding-top: 8px; border-top: 1px solid var(--nca-border, rgba(255,255,255,0.08)); line-height: 1.6;";

    githubTab.appendChild(el("div", { class: "nca-form-section" }, [
        el("div", { class: "nca-form-label", text: "🔗 GitHub 设置" }),
        el("div", { class: "nca-field" }, [el("label", { text: "Personal Access Token" }), githubTokenInput, tokenHint]),
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
        featureHint,
    ]));

    body.appendChild(githubTab);

    // ══════ 页签3: 记忆管理 ══════
    const memoryTab = el("div", { class: "nca-tab-content", id: "nca-settings-memory" });
    memoryTab.style.display = "none";
    memoryTab.innerHTML = `
        <div class="nca-form-section">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px;">
                <div style="display:flex; align-items:center; gap:8px;">
                    <h4 style="color:#00d4ff; margin:0; font-size:12px; font-family:var(--nca-font-mono);">🧠 跨会话记忆</h4>
                    <span id="nca-memory-stats" style="font-size:10px; color:#6b7280;"></span>
                </div>
                <div style="display:flex; gap:6px;">
                    <button id="nca-memory-refresh" class="nca-btn nca-btn-xs" style="font-size:11px; padding:4px 8px;">刷新</button>
                    <button id="nca-memory-pull" class="nca-btn nca-btn-xs" style="font-size:11px; padding:4px 8px; color:#3b82f6;">⤓ 拉取云端踩坑记录</button>
                    <button id="nca-memory-clear" class="nca-btn nca-btn-xs nca-btn-danger" style="font-size:11px; padding:4px 8px;">清除全部</button>
                </div>
            </div>
            <div id="nca-memory-content" style="max-height:480px; overflow-y:auto; font-size:12px;">
                <div style="color:#6b7280; padding:24px; text-align:center;">点击"刷新"加载记忆数据</div>
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
            const 插件Keys = Object.keys(插件记忆);
            let 上下文总数 = 0;
            插件Keys.forEach(k => { 上下文总数 += (插件记忆[k]["上下文"] || []).length; });

            // 更新统计概览
            const statsEl = (panelRef || panel).querySelector('#nca-memory-stats');
            if (statsEl) {
                statsEl.textContent = `偏好 ${偏好列表.length} · 问题 ${问题列表.length} · 插件 ${插件Keys.length} · 上下文 ${上下文总数}`;
            }

            let html = '';

            // ── 区块1: 用户偏好 ──
            html += `<div style="background:rgba(255,255,255,0.02); border:1px solid var(--nca-border); border-radius:6px; margin-bottom:10px; overflow:hidden;">
                <div class="nca-mem-section-header" data-section="pref" style="display:flex; justify-content:space-between; align-items:center; padding:8px 12px; cursor:pointer; user-select:none;">
                    <span style="color:var(--nca-accent); font-size:11px; font-family:var(--nca-font-mono);">⚙ 用户偏好 <span style="color:#6b7280;">(${偏好列表.length})</span></span>
                    <span class="nca-mem-toggle" style="color:#6b7280; font-size:10px;">▾</span>
                </div>
                <div class="nca-mem-section-body" data-body="pref" style="padding:0 12px 8px;">
                    <div id="nca-memory-add-form" style="display:flex; gap:6px; align-items:center; margin:8px 0;">
                        <input id="nca-memory-key" type="text" placeholder="偏好名称（如：代码风格_缩进）" style="flex:0 0 30%; min-width:0; padding:5px 8px; font-size:11px; background:var(--nca-bg-secondary); color:var(--nca-fg); border:1px solid var(--nca-border); border-radius:4px;">
                        <input id="nca-memory-value" type="text" placeholder="偏好内容（如：使用4空格缩进）" style="flex:1 1 50%; min-width:0; padding:5px 8px; font-size:11px; background:var(--nca-bg-secondary); color:var(--nca-fg); border:1px solid var(--nca-border); border-radius:4px;">
                        <button id="nca-memory-add-btn" class="nca-btn nca-btn-xs" style="flex:0 0 auto; font-size:11px; padding:5px 10px;">添加</button>
                    </div>`;
            if (偏好列表.length === 0) {
                html += '<div style="color:#6b7280; padding:8px 0; font-size:11px; text-align:center;">暂无记录</div>';
            } else {
                偏好列表.forEach((item, i) => {
                    html += `<div style="display:flex; justify-content:space-between; align-items:center; padding:5px 0; border-bottom:1px solid rgba(255,255,255,0.05);">
                        <div style="flex:1; min-width:0;">
                            <span style="color:var(--nca-fg-dim); font-size:10px;">${_转义HTML(item.key || '')}</span>
                            <span style="color:var(--nca-fg); font-size:11px; margin-left:4px;">${_转义HTML(item.value || '')}</span>
                        </div>
                        <button class="nca-memory-edit-btn" data-key="${_转义HTML(item.key || '')}" data-value="${_转义HTML(item.value || '')}" title="编辑" style="background:none; border:none; color:var(--nca-accent); cursor:pointer; font-size:11px; padding:2px 6px; flex-shrink:0;">✏️</button>
                        <button class="nca-memory-del-btn" data-type="用户偏好" data-index="${i}" style="background:none; border:none; color:#ef4444; cursor:pointer; font-size:11px; padding:2px 6px; flex-shrink:0;">✕</button>
                    </div>`;
                });
            }
            html += `</div></div>`;

            // ── 区块2: 历史问题与解决方案 ──
            html += `<div style="background:rgba(255,255,255,0.02); border:1px solid var(--nca-border); border-radius:6px; margin-bottom:10px; overflow:hidden;">
                <div class="nca-mem-section-header" data-section="faq" style="display:flex; justify-content:space-between; align-items:center; padding:8px 12px; cursor:pointer; user-select:none;">
                    <span style="color:var(--nca-accent); font-size:11px; font-family:var(--nca-font-mono);">📋 历史问题与解决方案 <span style="color:#6b7280;">(${问题列表.length})</span></span>
                    <span class="nca-mem-toggle" style="color:#6b7280; font-size:10px;">▾</span>
                </div>
                <div class="nca-mem-section-body" data-body="faq" style="padding:0 12px 8px;">
                    <div style="color:#6b7280; font-size:10px; padding:4px 0; font-style:italic;">AI对话中出现"解决了"等关键词时自动提取并上传云端，管理员审核通过后同步到本地</div>`;
            if (问题列表.length === 0) {
                html += '<div style="color:#6b7280; padding:8px 0; font-size:11px; text-align:center;">暂无记录</div>';
            } else {
                问题列表.forEach((item, i) => {
                    html += `<div style="display:flex; justify-content:space-between; align-items:flex-start; padding:5px 0; border-bottom:1px solid rgba(255,255,255,0.05);">
                        <div style="flex:1; min-width:0;">
                            <div style="color:var(--nca-fg-dim); font-size:11px;">Q: ${_转义HTML(item.question || '')}</div>
                            <div style="color:#6b7280; font-size:10px; margin-top:2px;">A: ${_转义HTML((item.solution || '').substring(0, 80))}${(item.solution || '').length > 80 ? '...' : ''}</div>
                            ${item.timestamp ? `<div style="color:#4b5563; font-size:9px; margin-top:1px;">${_转义HTML(item.timestamp.substring(0, 10))}</div>` : ''}
                        </div>
                        <button class="nca-memory-del-btn" data-type="常见问题" data-index="${i}" style="background:none; border:none; color:#ef4444; cursor:pointer; font-size:11px; padding:2px 6px; flex-shrink:0;">✕</button>
                    </div>`;
                });
            }
            html += `</div></div>`;

            // ── 区块3: 插件记忆 ──
            html += `<div style="background:rgba(255,255,255,0.02); border:1px solid var(--nca-border); border-radius:6px; margin-bottom:10px; overflow:hidden;">
                <div class="nca-mem-section-header" data-section="plugin" style="display:flex; justify-content:space-between; align-items:center; padding:8px 12px; cursor:pointer; user-select:none;">
                    <span style="color:var(--nca-accent); font-size:11px; font-family:var(--nca-font-mono);">📦 插件记忆 <span style="color:#6b7280;">(${插件Keys.length})</span></span>
                    <span class="nca-mem-toggle" style="color:#6b7280; font-size:10px;">▾</span>
                </div>
                <div class="nca-mem-section-body" data-body="plugin" style="padding:0 12px 8px;">
                    <div id="nca-pm-add-form" style="display:flex; gap:6px; align-items:center; margin:8px 0;">
                        <input id="nca-pm-name" type="text" placeholder="插件名称" style="flex:0 0 30%; min-width:0; padding:5px 8px; font-size:11px; background:var(--nca-bg-secondary); color:var(--nca-fg); border:1px solid var(--nca-border); border-radius:4px;">
                        <input id="nca-pm-content" type="text" placeholder="上下文内容（如：该插件使用 PyTorch 处理图像）" style="flex:1 1 50%; min-width:0; padding:5px 8px; font-size:11px; background:var(--nca-bg-secondary); color:var(--nca-fg); border:1px solid var(--nca-border); border-radius:4px;">
                        <button id="nca-pm-add-btn" class="nca-btn nca-btn-xs" style="flex:0 0 auto; font-size:11px; padding:5px 10px;">添加</button>
                    </div>`;
            if (插件Keys.length === 0) {
                html += '<div style="color:#6b7280; padding:8px 0; font-size:11px; text-align:center;">暂无记录</div>';
            } else {
                插件Keys.forEach(插件名 => {
                    const pm = 插件记忆[插件名];
                    const 信息 = pm["项目信息"] || {};
                    const 上下文列表 = pm["上下文"] || [];
                    html += `<div style="padding:6px 0; border-bottom:1px solid rgba(255,255,255,0.05);">
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
            }
            html += `</div></div>`;

            contentDiv.innerHTML = html;

            // 绑定分区折叠
            contentDiv.querySelectorAll('.nca-mem-section-header').forEach(header => {
                header.addEventListener('click', () => {
                    const body = contentDiv.querySelector(`[data-body="${header.dataset.section}"]`);
                    const toggle = header.querySelector('.nca-mem-toggle');
                    if (body) {
                        const isHidden = body.style.display === 'none';
                        body.style.display = isHidden ? '' : 'none';
                        if (toggle) toggle.textContent = isHidden ? '▾' : '▸';
                    }
                });
            });

            // 绑定删除按钮
            contentDiv.querySelectorAll('.nca-memory-del-btn').forEach(btn => {
                btn.addEventListener('click', async () => {
                    const type = btn.dataset.type;
                    const index = parseInt(btn.dataset.index);
                    const plugin = btn.dataset.plugin || null;
                    if (!confirm('确定要删除这条记忆吗？')) return;
                    try {
                        const rj = await 请求('DELETE', '/memories', { type: 'item', 记忆类型: type, index, plugin_path: plugin });
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

            // 绑定编辑按钮（仅用户偏好）
            contentDiv.querySelectorAll('.nca-memory-edit-btn').forEach(btn => {
                btn.addEventListener('click', () => {
                    const root = panelRef || panel;
                    const keyInput = root.querySelector('#nca-memory-key');
                    const valueInput = root.querySelector('#nca-memory-value');
                    const addBtn = root.querySelector('#nca-memory-add-btn');
                    if (!keyInput || !valueInput || !addBtn) return;
                    keyInput.value = btn.dataset.key || '';
                    valueInput.value = btn.dataset.value || '';
                    addBtn.textContent = '保存修改';
                    addBtn.dataset.editing = '1';
                    valueInput.focus();
                });
            });

            // 绑定添加/保存按钮（表单随内容区重新渲染，每次刷新都需重新绑定）
            const addBtn = contentDiv.querySelector('#nca-memory-add-btn');
            const keyInput = contentDiv.querySelector('#nca-memory-key');
            const valueInput = contentDiv.querySelector('#nca-memory-value');
            if (addBtn && keyInput && valueInput) {
                const 提交偏好 = async () => {
                    const key = keyInput.value.trim();
                    const value = valueInput.value.trim();
                    if (!key || !value) {
                        Toast && Toast.show && Toast.show('名称和内容不能为空', 'error');
                        return;
                    }
                    if (addBtn.dataset.editing === '1' && !confirm('确定要保存修改吗？')) return;
                    addBtn.disabled = true;
                    const 原文本 = addBtn.dataset.editing === '1' ? '保存修改' : '添加';
                    addBtn.textContent = '保存中...';
                    try {
                        const rj = await 请求('POST', '/memories', { key, value });
                        if (rj.success) {
                            Toast && Toast.show && Toast.show('保存成功', 'success');
                            keyInput.value = '';
                            valueInput.value = '';
                            addBtn.dataset.editing = '';
                            addBtn.textContent = '添加';
                            加载记忆数据(panelRef);
                        } else {
                            Toast && Toast.show && Toast.show(rj.message || '保存失败', 'error');
                            addBtn.textContent = 原文本;
                        }
                    } catch (e) {
                        Toast && Toast.show && Toast.show(`保存失败: ${e.message || e}`, 'error');
                        addBtn.textContent = 原文本;
                    } finally {
                        addBtn.disabled = false;
                    }
                };
                addBtn.addEventListener('click', 提交偏好);
                valueInput.addEventListener('keydown', (e) => {
                    if (e.key === 'Enter') { e.preventDefault(); 提交偏好(); }
                });
            }

            // 绑定插件记忆添加按钮
            const pmAddBtn = contentDiv.querySelector('#nca-pm-add-btn');
            const pmNameInput = contentDiv.querySelector('#nca-pm-name');
            const pmContentInput = contentDiv.querySelector('#nca-pm-content');
            if (pmAddBtn && pmNameInput && pmContentInput) {
                const 提交插件记忆 = async () => {
                    const plugin_name = pmNameInput.value.trim();
                    const content = pmContentInput.value.trim();
                    if (!plugin_name || !content) {
                        Toast && Toast.show && Toast.show('插件名称和内容不能为空', 'error');
                        return;
                    }
                    pmAddBtn.disabled = true;
                    pmAddBtn.textContent = '保存中...';
                    try {
                        const rj = await 请求('POST', '/memories/plugin', { plugin_name, content });
                        if (rj.success) {
                            Toast && Toast.show && Toast.show('保存成功', 'success');
                            pmNameInput.value = '';
                            pmContentInput.value = '';
                            加载记忆数据(panelRef);
                        } else {
                            Toast && Toast.show && Toast.show(rj.message || '保存失败', 'error');
                            pmAddBtn.textContent = '添加';
                        }
                    } catch (e) {
                        Toast && Toast.show && Toast.show(`保存失败: ${e.message || e}`, 'error');
                        pmAddBtn.textContent = '添加';
                    } finally {
                        pmAddBtn.disabled = false;
                    }
                };
                pmAddBtn.addEventListener('click', 提交插件记忆);
                pmContentInput.addEventListener('keydown', (e) => {
                    if (e.key === 'Enter') { e.preventDefault(); 提交插件记忆(); }
                });
            }
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
        const pullBtn = panel.querySelector('#nca-memory-pull');
        if (pullBtn) {
            pullBtn.addEventListener('click', async () => {
                if (!confirm('确定要从云端拉取踩坑记录吗？')) return;
                pullBtn.disabled = true;
                pullBtn.textContent = '拉取中...';
                try {
                    const rj = await 请求('POST', '/cloud/pull', {});
                    if (rj.success) {
                        const msg = `拉取完成: 新增${rj.pulled||0}, 更新${rj.merged||0}, 删除${rj.deleted||0}, 跳过${rj.skipped||0}`;
                        Toast && Toast.show && Toast.show(msg, 'success');
                        加载记忆数据(panel);
                    } else {
                        Toast && Toast.show && Toast.show(rj.error || rj.message || '拉取失败', 'error');
                    }
                } catch (e) {
                    Toast && Toast.show && Toast.show(`拉取失败: ${e.message || e}`, 'error');
                } finally {
                    pullBtn.disabled = false;
                    pullBtn.textContent = '⤓ 拉取云端踩坑记录';
                }
            });
        }
        if (clearBtn) {
            clearBtn.addEventListener('click', async () => {
                if (!confirm('确定要清除所有全局记忆吗？此操作不可撤销。')) return;
                clearBtn.disabled = true;
                clearBtn.textContent = '清除中...';
                try {
                    const rj = await 请求('DELETE', '/memories', {});
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
            local_path: localPathInput.value,
            github_token: githubTokenInput.value.trim(),
            github_username: githubUserInput.value.trim(),
            github_visibility: panel.querySelector('input[name="github_visibility"]:checked')?.value || "public",
            enable_planning: planningToggle.checked,
            proactive_pitfall_check: pitfallToggle.checked,
            max_tool_rounds: parseInt(轮次Select.value, 10),
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
