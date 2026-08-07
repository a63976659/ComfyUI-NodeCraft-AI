// ═══════════════════════════════════════════════════════════════
// 设置面板.js — 设置界面（Tab 页签：模型设置 + GitHub 设置）
// NodeCraft AI — Luxury Terminal / Neo-Noir Hacker
// ═══════════════════════════════════════════════════════════════

import { el, Toast, _转义HTML } from "./工具函数.js";
import { NCA_STORAGE_KEYS } from "./工具函数.js";
import { 存储 } from "./存储引擎.js";
import {
    事件总线, 事件, 状态,
    加载设置, 保存设置, 创建插件文件夹, 创建会话, 请求, 模型默认MaxTokens, 模型MaxTokens上限,
} from "./交互与状态.js";
import { t } from "./i18n.js";
import { 显示文件夹浏览器 } from "./文件夹浏览器.js";

// 格式化下载数
function _formatCount(n) {
    if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M';
    if (n >= 1000) return (n / 1000).toFixed(1) + 'K';
    return String(n);
}

// 文件夹浏览器已提取至 ./文件夹浏览器.js

// 【已弃用】云端模型列表已移除（本地 API Key 直连后无云端可用模型端点）
// 保留空实现以兼容旧调用点（会话列表面板等），下次刷新后可删除
// eslint-disable-next-line no-unused-vars
export function 清除云端模型缓存() { /* no-op */ }
// eslint-disable-next-line no-unused-vars
export async function 加载云端模型列表(selectEl, settings) {
    // 兼容旧调用点：把 settings.model_name 作为唯一选项填入，不再发网络请求
    if (!selectEl) return;
    selectEl.innerHTML = "";
    const opt = document.createElement("option");
    opt.value = settings && settings.model_name ? settings.model_name : "";
    opt.textContent = settings && settings.model_name ? settings.model_name : t("settings.configure_model_hint");
    opt.selected = true;
    if (!opt.value) opt.disabled = true;
    selectEl.appendChild(opt);
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
        { id: "nca-settings-memory", label: t("settings.tab.memory") },
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
        title: t("settings.browse_folder"),
    });
    browseBtn.addEventListener("click", () => {
        if (browseBtn.disabled) return;
        显示文件夹浏览器(
            rootContainer,
            localPathInput.value || s.default_local_path || "",
            (选中路径) => {
                localPathInput.value = 选中路径;
                localPathInput.dispatchEvent(new Event("change", { bubbles: true }));
                Toast && Toast.show && Toast.show(t("settings.path_selected"), "success");
            }
        );
    });

    const 默认路径Btn = el("button", { class: "nca-btn nca-btn-sm nca-btn-ghost", text: t("settings.default_path") });
    默认路径Btn.addEventListener("click", () => {
        localPathInput.value = s.default_local_path || "models/LLM";
        localPathInput.dispatchEvent(new Event("change", { bubbles: true }));
    });

    // 本地模型卡片内容延后到「模型来源」双卡区域统一构建（见下方 modelGrid）

    // ── 语言切换已迁移至顶部品牌栏语言按钮，此处不再提供设置项 ──

    // ══════ API 服务区域（本地 API Key 直连）══════
    const 供应商预设 = {
        openai:      { label: "OpenAI",      base_url: "https://api.openai.com/v1",             help: "https://platform.openai.com/api-keys" },
        deepseek:    { label: "DeepSeek",    base_url: "https://api.deepseek.com/v1",           help: "https://platform.deepseek.com/api_keys" },
        kimi:        { label: "Kimi 月之暗面", base_url: "https://api.moonshot.cn/v1",          help: "https://platform.kimi.com/console/api-keys" },
        modelscope:  { label: "魔搭 ModelScope", base_url: "https://api-inference.modelscope.cn/v1", help: "https://modelscope.cn/my/myaccesstoken" },
        siliconflow: { label: "硅基流动 SiliconFlow", base_url: "https://api.siliconflow.cn/v1", help: "https://cloud.siliconflow.cn/account/ak" },
        openrouter:  { label: "OpenRouter",  base_url: "https://openrouter.ai/api/v1",          help: "https://openrouter.ai/keys" },
        dashscope:   { label: "阿里云百炼 千问", base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1", help: "https://bailian.console.aliyun.com/?apiKey=1#/api-key", model: "Qwen3.8-Max" },
        custom:      { label: t("settings.provider_custom"), base_url: "",                                     help: "" },
    };

    const apiProviderSelect = el("select", {
        id: "nca-api-provider",
        style: {
            padding: "6px 10px", border: "1px solid var(--nca-border)",
            background: "var(--nca-bg-primary)", color: "var(--nca-fg)",
            borderRadius: "var(--nca-radius-sm)", fontSize: "12px",
            fontFamily: "var(--nca-font-mono)", outline: "none", cursor: "pointer",
            width: "100%", boxSizing: "border-box",
        }
    });
    Object.entries(供应商预设).forEach(([key, cfg]) => {
        const opt = document.createElement("option");
        opt.value = key;
        opt.textContent = cfg.label;
        apiProviderSelect.appendChild(opt);
    });
    apiProviderSelect.value = s.api_provider || "openai";

    const apiBaseUrlInput = el("input", {
        id: "nca-api-base-url",
        type: "text",
        value: s.base_url || 供应商预设[apiProviderSelect.value]?.base_url || "",
        placeholder: "https://api.openai.com/v1",
    });

    const apiKeyInput = el("input", {
        id: "nca-api-key",
        type: "password",
        value: s.api_key || "",
        placeholder: "sk-...",
    });

    const apiModelInput = el("input", {
        id: "nca-api-model",
        type: "text",
        value: s.model_name || "",
        placeholder: t("settings.api_model_placeholder"),
    });

    // 最大输出 tokens（按配置方案分开存储；留空 = 自动采用模型推荐默认值，placeholder 实时显示）
    // 仅 API 路径生效：本地模型按上下文窗口动态计算生成额度，不读此值
    const apiMaxTokensInput = el("input", {
        id: "nca-api-max-tokens",
        type: "number",
        min: "1024", max: "1048576", step: "1024",
        placeholder: "4096",
    });
    const apiMaxTokensHint = el("div", { class: "nca-help-text", text: t("settings.max_tokens_hint") });
    apiMaxTokensHint.style.cssText = "font-size: 11px; color: var(--nca-fg-dim, #888); margin-top: 4px; line-height: 1.5;";
    // placeholder 与输入上限跟随模型名变化：留空时的默认值 + 各家 API 实际允许的最大输出
    function 刷新MaxTokens默认提示() {
        const 模型名 = apiModelInput.value.trim();
        apiMaxTokensInput.placeholder = String(模型默认MaxTokens(模型名));
        // 上限同步：Kimi K3 最高 1M / DeepSeek V4 384K / GLM-5.2 128K / 其他 32768
        const 上限 = 模型MaxTokens上限(模型名);
        apiMaxTokensInput.max = String(上限);
        apiMaxTokensHint.textContent = `${t("settings.max_tokens_hint")} ${t("settings.max_tokens_cap", { n: 上限.toLocaleString() })}`;
    }
    apiModelInput.addEventListener("input", 刷新MaxTokens默认提示);

    // 供应商切换时联动填充 base_url（可手动修改）；预设带默认模型的同步填充模型名
    apiProviderSelect.addEventListener("change", () => {
        const cfg = 供应商预设[apiProviderSelect.value];
        if (cfg && apiProviderSelect.value !== "custom") {
            apiBaseUrlInput.value = cfg.base_url;
            if (cfg.model) {
                apiModelInput.value = cfg.model;
                刷新MaxTokens默认提示();
            }
        }
        更新帮助链接();
    });

    // 帮助提示区域
    const apiHelpText = el("div", { class: "nca-help-text" });
    apiHelpText.style.cssText = "font-size: 11px; color: var(--nca-fg-dim, #888); margin-top: 8px; line-height: 1.6;";
    function 更新帮助链接() {
        const cfg = 供应商预设[apiProviderSelect.value];
        if (cfg && cfg.help) {
            apiHelpText.innerHTML = t("settings.api_help", { url: cfg.help, label: cfg.label });
        } else {
            apiHelpText.innerHTML = t("settings.api_help_custom");
        }
    }
    更新帮助链接();

    // ══════ 多 API 配置方案管理（新增/删除/切换，保存时一并落盘）══════
    const 生成配置ID = () => Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
    // 工作副本：编辑期间不碰全局状态，点保存才提交
    const 配置列表 = Array.isArray(s.api_profiles) ? s.api_profiles.map((p) => ({ ...p })) : [];
    if (配置列表.length === 0) {
        配置列表.push({
            id: 生成配置ID(), name: s.model_name || t("settings.default_profile"),
            api_provider: s.api_provider || "openai", base_url: s.base_url || "",
            api_key: s.api_key || "", model_name: s.model_name || "",
            // 旧顶层值为历史默认 4096 时视为未设置，让模型推荐默认值生效
            max_tokens: (s.max_tokens && s.max_tokens !== 4096) ? s.max_tokens : null,
            reasoning_effort: s.reasoning_effort || null,
        });
    }
    let 当前配置ID = 配置列表.some((p) => p.id === s.active_api_profile_id)
        ? s.active_api_profile_id : 配置列表[0].id;

    const 配置Select = el("select", {
        id: "nca-api-profile",
        style: {
            flex: "1", minWidth: "0", padding: "6px 10px", border: "1px solid var(--nca-border)",
            background: "var(--nca-bg-primary)", color: "var(--nca-fg)",
            borderRadius: "var(--nca-radius-sm)", fontSize: "12px",
            fontFamily: "var(--nca-font-mono)", outline: "none", cursor: "pointer", boxSizing: "border-box",
        }
    });
    const 新增配置Btn = el("button", { class: "nca-btn nca-btn-sm", text: t("settings.add_profile"), title: t("settings.add_profile_tip") });
    const 删除配置Btn = el("button", { class: "nca-btn nca-btn-sm", text: "🗑", title: t("settings.del_profile_tip") });
    const 配置名称Input = el("input", { type: "text", placeholder: t("settings.profile_name_placeholder") });

    function 当前配置() { return 配置列表.find((p) => p.id === 当前配置ID) || 配置列表[0]; }

    // 把四个输入框当前值暂存回当前选中的配置（切换/新增/保存前调用）
    function 暂存输入到当前配置() {
        const p = 当前配置();
        if (!p) return;
        p.api_provider = apiProviderSelect.value;
        p.base_url = apiBaseUrlInput.value.trim();
        p.api_key = apiKeyInput.value.trim();
        p.model_name = apiModelInput.value.trim();
        // 空值/非法值存 null（保存/切换时自动采用模型推荐默认值），合法值限幅到 1024~模型输出上限
        const mt = parseInt(apiMaxTokensInput.value, 10);
        p.max_tokens = Number.isFinite(mt) && mt > 0 ? Math.min(Math.max(mt, 1024), 模型MaxTokens上限(apiModelInput.value.trim())) : null;
        // 思考深度（reasoning_effort）改由模型切换栏按钮调节，此处不再读写，保留配置已存值
        p.name = 配置名称Input.value.trim() || p.model_name || t("settings.unnamed_profile");
    }

    function 加载配置到输入(p) {
        配置名称Input.value = p.name || "";
        apiProviderSelect.value = 供应商预设[p.api_provider] ? p.api_provider : "custom";
        apiBaseUrlInput.value = p.base_url || "";
        apiKeyInput.value = p.api_key || "";
        apiModelInput.value = p.model_name || "";
        apiMaxTokensInput.value = p.max_tokens || "";
        刷新MaxTokens默认提示();
        更新帮助链接();
    }

    function 刷新配置下拉() {
        配置Select.innerHTML = "";
        配置列表.forEach((p) => {
            const opt = document.createElement("option");
            opt.value = p.id;
            opt.textContent = p.name || p.model_name || t("settings.unnamed_profile");
            if (p.id === 当前配置ID) opt.selected = true;
            配置Select.appendChild(opt);
        });
        删除配置Btn.disabled = 配置列表.length <= 1;
    }

    配置Select.addEventListener("change", () => {
        暂存输入到当前配置();
        当前配置ID = 配置Select.value;
        加载配置到输入(当前配置());
        刷新配置下拉();
    });
    新增配置Btn.addEventListener("click", () => {
        暂存输入到当前配置();
        const 新配置 = {
            id: 生成配置ID(), name: t("settings.profile_n", { n: 配置列表.length + 1 }), api_provider: "openai",
            base_url: 供应商预设.openai.base_url, api_key: "", model_name: "",
        };
        配置列表.push(新配置);
        当前配置ID = 新配置.id;
        加载配置到输入(新配置);
        刷新配置下拉();
        配置名称Input.focus();
    });
    删除配置Btn.addEventListener("click", () => {
        if (配置列表.length <= 1) return;
        const p = 当前配置();
        if (!confirm(t("settings.confirm_delete_profile", { name: p.name || t("settings.unnamed_profile") }))) return;
        const idx = 配置列表.findIndex((x) => x.id === 当前配置ID);
        配置列表.splice(idx, 1);
        当前配置ID = 配置列表[Math.max(0, idx - 1)].id;
        加载配置到输入(当前配置());
        刷新配置下拉();
    });
    加载配置到输入(当前配置());
    刷新配置下拉();

    // 测试连接按钮：向 {base_url}/models 发 GET
    const 测试APIBtn = el("button", { class: "nca-btn nca-btn-sm", text: t("settings.test_api") });
    测试APIBtn.addEventListener("click", async () => {
        const base = (apiBaseUrlInput.value || "").trim().replace(/\/+$/, "");
        const key = (apiKeyInput.value || "").trim();
        if (!base) {
            测试APIBtn.textContent = t("settings.need_base_url");
            测试APIBtn.style.color = "#ff4444";
            setTimeout(() => { 测试APIBtn.textContent = t("settings.test_api"); 测试APIBtn.style.color = ""; }, 3000);
            return;
        }
        if (!key) {
            测试APIBtn.textContent = t("settings.need_api_key");
            测试APIBtn.style.color = "#ff4444";
            setTimeout(() => { 测试APIBtn.textContent = t("settings.test_api"); 测试APIBtn.style.color = ""; }, 3000);
            return;
        }
        测试APIBtn.disabled = true;
        测试APIBtn.textContent = t("settings.testing");
        try {
            const resp = await fetch(`${base}/models`, {
                method: "GET",
                headers: { "Authorization": `Bearer ${key}` },
            });
            if (resp.ok) {
                测试APIBtn.textContent = t("settings.test_ok");
                测试APIBtn.style.color = "#00ffc8";
            } else {
                测试APIBtn.textContent = `✗ HTTP ${resp.status}`;
                测试APIBtn.style.color = "#ff4444";
            }
        } catch (e) {
            测试APIBtn.textContent = "✗ " + t("common.network_error");
            测试APIBtn.style.color = "#ff4444";
        }
        setTimeout(() => {
            测试APIBtn.disabled = false;
            测试APIBtn.textContent = t("settings.test_api");
            测试APIBtn.style.color = "";
        }, 3000);
    });

    // ══════ 模型来源：本地模型 / API 服务（横向双卡并排，窄屏自动堆叠）══════
    const 本地模型卡 = el("div", { class: "nca-model-card nca-model-card--local" }, [
        el("div", { class: "nca-model-card-head" }, [
            el("span", { class: "nca-model-card-icon", text: "🖥" }),
            el("div", { class: "nca-model-card-heading" }, [
                el("div", { class: "nca-model-card-title", text: t("settings.local_model") }),
                el("div", { class: "nca-model-card-sub", text: t("settings.local_sub") }),
            ]),
        ]),
        el("div", { class: "nca-model-card-body" }, [
            el("div", { class: "nca-field" }, [
                el("label", { text: t("settings.model_path") }),
                el("div", { class: "nca-path-row" }, [
                    localPathInput,
                    browseBtn,
                ]),
            ]),
            默认路径Btn,
        ]),
    ]);

    const API服务卡 = el("div", { class: "nca-model-card nca-model-card--api" }, [
        el("div", { class: "nca-model-card-head" }, [
            el("span", { class: "nca-model-card-icon", text: "☁" }),
            el("div", { class: "nca-model-card-heading" }, [
                el("div", { class: "nca-model-card-title", text: t("settings.api_service") }),
                el("div", { class: "nca-model-card-sub", text: t("settings.api_sub") }),
            ]),
        ]),
        el("div", { class: "nca-model-card-body" }, [
            el("div", { class: "nca-field" }, [
                el("label", { text: t("settings.profile") }),
                el("div", { style: { display: "flex", gap: "6px", alignItems: "center" } }, [配置Select, 新增配置Btn, 删除配置Btn]),
            ]),
            el("div", { class: "nca-field" }, [el("label", { text: t("settings.profile_name") }), 配置名称Input]),
            el("div", { class: "nca-field" }, [el("label", { text: t("settings.provider_preset") }), apiProviderSelect]),
            el("div", { class: "nca-field" }, [el("label", { text: "Base URL" }), apiBaseUrlInput]),
            el("div", { class: "nca-field" }, [el("label", { text: "API Key" }), apiKeyInput]),
            el("div", { class: "nca-field" }, [el("label", { text: t("settings.model_name") }), apiModelInput]),
            el("div", { class: "nca-field" }, [el("label", { text: t("settings.max_tokens") }), apiMaxTokensInput, apiMaxTokensHint]),
            测试APIBtn,
            apiHelpText,
        ]),
    ]);

    // ── 模型来源切换：点按钮显示对应详情，默认显示本地模型（与模型市场源切换一致）──
    const 本地Tab按钮 = el("button", { class: "nca-btn nca-btn-sm nca-model-src-btn active", text: "\u{1F5A5} " + t("settings.local_model") });
    本地Tab按钮.dataset.target = "local";
    const APITab按钮 = el("button", { class: "nca-btn nca-btn-sm nca-model-src-btn", text: "\u2601 " + t("settings.api_service") });
    APITab按钮.dataset.target = "api";

    // 默认显示本地模型，隐藏 API 服务详情
    API服务卡.style.display = "none";

    function 切换模型来源UI(target) {
        本地模型卡.style.display = target === "local" ? "" : "none";
        API服务卡.style.display = target === "api" ? "" : "none";
        [本地Tab按钮, APITab按钮].forEach((b) => {
            b.classList.toggle("active", b.dataset.target === target);
        });
    }
    本地Tab按钮.addEventListener("click", () => 切换模型来源UI("local"));
    APITab按钮.addEventListener("click", () => 切换模型来源UI("api"));

    const 模型说明 = document.createElement('p');
    模型说明.className = 'nca-model-hint';
    模型说明.textContent = t("settings.model_hint");

    modelTab.appendChild(el("div", { class: "nca-form-section" }, [
        el("div", { class: "nca-form-label", text: "⌘ " + t("settings.model_source") }),
        el("div", { class: "nca-model-src-switch" }, [本地Tab按钮, APITab按钮]),
        模型说明,
        el("div", { class: "nca-model-detail" }, [本地模型卡, API服务卡]),
    ]));

    // ══════ 模型市场区域 ══════
    const marketSection = el("div", { class: "nca-form-section" });
    marketSection.innerHTML = `
        <h4 style="color:#00d4ff; margin:0 0 8px 0; font-size:12px; font-family:var(--nca-font-mono);">${t("settings.market")}</h4>
        <div style="display:flex; gap:4px; margin-bottom:8px;">
            <button id="nca-market-src-hf" class="nca-btn nca-btn-xs nca-market-src-btn active" data-source="huggingface" style="font-size:11px; padding:4px 8px; background:var(--nca-accent); color:#000; border-color:var(--nca-accent);">🤗 HuggingFace</button>
            <button id="nca-market-src-ms" class="nca-btn nca-btn-xs nca-market-src-btn" data-source="modelscope" style="font-size:11px; padding:4px 8px;">🔮 魔搭</button>
        </div>
        <div style="display:flex; gap:6px; margin-bottom:8px;">
            <input type="text" id="nca-model-search" placeholder="${t('settings.market_search_placeholder')}"
                   style="flex:1; padding:8px 10px; border:1px solid var(--nca-border); background:var(--nca-bg-primary); color:var(--nca-fg); border-radius:var(--nca-radius-sm); font-size:12px; font-family:var(--nca-font-mono); outline:none; box-sizing:border-box;">
            <button id="nca-model-search-btn" class="nca-btn nca-btn-sm" style="white-space:nowrap;">${t("common.search")}</button>
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
                    ? t('settings.market_search_ms')
                    : t('settings.market_search_placeholder');
                resultsDiv.innerHTML = '';
            });
        });

        const doSearch = async () => {
            const q = searchInput.value.trim();
            if (!q) { resultsDiv.innerHTML = ''; return; }
            searchBtn.disabled = true;
            searchBtn.textContent = t('common.searching');
            resultsDiv.innerHTML = `<div style="color:#6b7280;font-size:11px;padding:8px;">${t('settings.market_searching')}</div>`;
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
                            <button class="nca-model-download-btn" data-model-id="${m.id}">${t('settings.market_download')}</button>
                        </div>
                    `).join('');
                    // 绑定下载按钮
                    resultsDiv.querySelectorAll('.nca-model-download-btn').forEach(btn => {
                        btn.addEventListener('click', async () => {
                            const modelId = btn.dataset.modelId;
                            btn.disabled = true;
                            btn.textContent = t('settings.market_starting');
                            try {
                                const r = await fetch('/ai-coder/model-market/download', {
                                    method: 'POST',
                                    headers: {'Content-Type': 'application/json'},
                                    body: JSON.stringify({model_id: modelId, source: currentSource})
                                });
                                const rj = await r.json();
                                if (rj.success) {
                                    btn.textContent = t('settings.market_started');
                                    btn.style.color = '#10b981';
                                    btn.style.borderColor = 'rgba(16,185,129,0.4)';
                                } else {
                                    btn.textContent = rj.message || t('settings.market_fail');
                                    btn.style.color = '#ef4444';
                                }
                            } catch (e) {
                                btn.textContent = t('common.network_error');
                                btn.style.color = '#ef4444';
                            }
                        });
                    });
                } else {
                    resultsDiv.innerHTML = `<div style="color:#6b7280;font-size:11px;padding:8px;text-align:center;">${t('settings.market_no_results')}</div>`;
                }
            } catch (e) {
                if (e && e.name === 'AbortError') return;
                if (!resultsDiv.parentNode) return;
                resultsDiv.innerHTML = `<div style="color:#ef4444;font-size:11px;padding:8px;">${t('settings.market_error')}</div>`;
            } finally {
                if (resultsDiv.parentNode) {
                    searchBtn.disabled = false;
                    searchBtn.textContent = t('common.search');
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
        { v: "25", label: t("settings.rounds_recommended") },
        { v: "100", label: "100" },
        { v: "-1", label: t("settings.rounds_unlimited") },
    ].forEach(o => {
        const opt = document.createElement("option");
        opt.value = o.v;
        opt.textContent = o.label;
        轮次Select.appendChild(opt);
    });
    轮次Select.value = String(s.max_tool_rounds != null ? s.max_tool_rounds : 25);
    if (!["25", "100", "-1"].includes(轮次Select.value)) 轮次Select.value = "25";
    modelTab.appendChild(el("div", { class: "nca-form-section" }, [
        el("div", { class: "nca-form-label", text: t("settings.advanced") }),
        el("label", {
            style: {
                display: "flex", alignItems: "center", justifyContent: "space-between",
                gap: "12px", cursor: "pointer", padding: "4px 0",
            }
        }, [
            el("div", { style: { flex: "1", minWidth: "0" } }, [
                el("div", { text: t("settings.planning"), style: { fontSize: "12px", color: "var(--nca-fg)" } }),
                el("div", {
                    text: t("settings.planning_desc"),
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
                el("div", { text: t("settings.pitfall"), style: { fontSize: "12px", color: "var(--nca-fg)" } }),
                el("div", {
                    text: t("settings.pitfall_desc"),
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
                el("div", { text: t("settings.max_rounds"), style: { fontSize: "12px", color: "var(--nca-fg)" } }),
                el("div", {
                    text: t("settings.max_rounds_desc"),
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

    const githubTokenInput = el("input", { type: "password", value: s.github_token || "", placeholder: t("settings.github_token_placeholder") });
    const githubUserInput = el("input", { type: "text", value: s.github_username || "", placeholder: "your-username" });

    const publicRadio = el("input", { type: "radio", name: "github_visibility", value: "public" });
    if ((s.github_visibility || "public") === "public") publicRadio.checked = true;
    const privateRadio = el("input", { type: "radio", name: "github_visibility", value: "private" });
    if (s.github_visibility === "private") privateRadio.checked = true;

    const testConnBtn = el("button", { class: "nca-btn nca-btn-sm", text: t("settings.test_api") });
    testConnBtn.addEventListener("click", async () => {
        testConnBtn.disabled = true;
        testConnBtn.textContent = t("settings.testing");
        try {
            const resp = await fetch("/ai-coder/github-test-connection", { method: "POST" });
            const data = await resp.json();
            if (data.valid) {
                testConnBtn.textContent = t("settings.test_ok");
                testConnBtn.style.color = "#00ffc8";
            } else {
                testConnBtn.textContent = "✗ " + (data.message || t("settings.test_fail"));
                testConnBtn.style.color = "#ff4444";
            }
        } catch (e) {
            testConnBtn.textContent = "✗ " + t("common.network_error");
            testConnBtn.style.color = "#ff4444";
        }
        setTimeout(() => {
            testConnBtn.disabled = false;
            testConnBtn.textContent = t("settings.test_api");
            testConnBtn.style.color = "";
        }, 3000);
    });

    const helpText = el("div", { class: "nca-help-text" });
    helpText.innerHTML = t("settings.github_token_help");
    helpText.style.cssText = "font-size: 11px; color: #888; margin-top: 8px; line-height: 1.6;";

    // Token 输入框下方的详细引导
    const tokenHint = el("div", { class: "nca-help-text" });
    tokenHint.innerHTML = t("settings.github_token_hint");
    tokenHint.style.cssText = "font-size: 11px; color: var(--nca-fg-dim, #888); margin-top: 6px; line-height: 1.6;";

    // GitHub 设置区域底部的功能说明
    const featureHint = el("div", { class: "nca-help-text" });
    featureHint.innerHTML = t("settings.github_feature_hint");
    featureHint.style.cssText = "font-size: 11px; color: var(--nca-fg-dim, #888); margin-top: 10px; padding-top: 8px; border-top: 1px solid var(--nca-border, rgba(255,255,255,0.08)); line-height: 1.6;";

    githubTab.appendChild(el("div", { class: "nca-form-section" }, [
        el("div", { class: "nca-form-label", text: t("settings.github_section") }),
        el("div", { class: "nca-field" }, [el("label", { text: "Personal Access Token" }), githubTokenInput, tokenHint]),
        el("div", { class: "nca-field" }, [el("label", { text: t("settings.github_username") }), githubUserInput]),
        el("div", { class: "nca-field" }, [
            el("label", { text: t("settings.github_visibility") }),
            el("div", { class: "nca-radio-group" }, [
                publicRadio,
                el("label", { text: t("settings.github_public"), style: { marginRight: "16px", cursor: "pointer" } }),
                privateRadio,
                el("label", { text: t("settings.github_private"), style: { cursor: "pointer" } }),
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
                    <h4 style="color:#00d4ff; margin:0; font-size:12px; font-family:var(--nca-font-mono);">${t("settings.memory_section")}</h4>
                    <span id="nca-memory-stats" style="font-size:10px; color:#6b7280;"></span>
                </div>
                <div style="display:flex; gap:6px;">
                    <button id="nca-memory-refresh" class="nca-btn nca-btn-xs" style="font-size:11px; padding:4px 8px;">${t("settings.memory_refresh")}</button>
                    <button id="nca-memory-clear" class="nca-btn nca-btn-xs nca-btn-danger" style="font-size:11px; padding:4px 8px;">${t("settings.memory_clear")}</button>
                </div>
            </div>
            <div id="nca-memory-content" style="font-size:12px;">
                <div style="color:#6b7280; padding:24px; text-align:center;">${t("settings.memory_hint")}</div>
            </div>
        </div>
    `;
    body.appendChild(memoryTab);

    // 记忆数据加载与渲染
    async function 加载记忆数据(panelRef) {
        const contentDiv = (panelRef || panel).querySelector('#nca-memory-content');
        if (!contentDiv) return;
        contentDiv.innerHTML = `<div style="color:#6b7280; padding:12px; text-align:center;">${t('common.loading')}</div>`;
        try {
            const resp = await fetch('/ai-coder/memories');
            const json = await resp.json();
            if (!json.success || !json.data) {
                contentDiv.innerHTML = `<div style="color:#ef4444; padding:8px;">${t('settings.memory_load_fail')}</div>`;
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
                statsEl.textContent = t('settings.memory_stats', { prefs: 偏好列表.length, faqs: 问题列表.length, plugins: 插件Keys.length, contexts: 上下文总数 });
            }

            let html = '';

            // ── 区块1: 用户偏好 ──
            html += `<div style="background:rgba(255,255,255,0.02); border:1px solid var(--nca-border); border-radius:6px; margin-bottom:10px; overflow:hidden;">
                <div class="nca-mem-section-header" data-section="pref" style="display:flex; justify-content:space-between; align-items:center; padding:8px 12px; cursor:pointer; user-select:none;">
                    <span style="color:var(--nca-accent); font-size:11px; font-family:var(--nca-font-mono);">${t('settings.memory_pref_title')} <span style="color:#6b7280;">(${偏好列表.length})</span></span>
                    <span class="nca-mem-toggle" style="color:#6b7280; font-size:10px;">▾</span>
                </div>
                <div class="nca-mem-section-body" data-body="pref" style="padding:0 12px 8px;">
                    <div id="nca-memory-add-form" style="display:flex; gap:6px; align-items:center; margin:8px 0;">
                        <input id="nca-memory-key" type="text" placeholder="${t('settings.memory_pref_key_ph')}" style="flex:0 0 30%; min-width:0; padding:5px 8px; font-size:11px; background:var(--nca-bg-secondary); color:var(--nca-fg); border:1px solid var(--nca-border); border-radius:4px;">
                        <input id="nca-memory-value" type="text" placeholder="${t('settings.memory_pref_value_ph')}" style="flex:1 1 50%; min-width:0; padding:5px 8px; font-size:11px; background:var(--nca-bg-secondary); color:var(--nca-fg); border:1px solid var(--nca-border); border-radius:4px;">
                        <button id="nca-memory-add-btn" class="nca-btn nca-btn-xs" style="flex:0 0 auto; font-size:11px; padding:5px 10px;">${t('settings.memory_add')}</button>
                    </div>`;
            if (偏好列表.length === 0) {
                html += `<div style="color:#6b7280; padding:8px 0; font-size:11px; text-align:center;">${t('settings.memory_empty')}</div>`;
            } else {
                偏好列表.forEach((item, i) => {
                    html += `<div style="display:flex; justify-content:space-between; align-items:center; padding:5px 0; border-bottom:1px solid rgba(255,255,255,0.05);">
                        <div style="flex:1; min-width:0;">
                            <span style="color:var(--nca-fg-dim); font-size:10px;">${_转义HTML(item.key || '')}</span>
                            <span style="color:var(--nca-fg); font-size:11px; margin-left:4px;">${_转义HTML(item.value || '')}</span>
                        </div>
                        <button class="nca-memory-edit-btn" data-key="${_转义HTML(item.key || '')}" data-value="${_转义HTML(item.value || '')}" title="${t('settings.memory_edit')}" style="background:none; border:none; color:var(--nca-accent); cursor:pointer; font-size:11px; padding:2px 6px; flex-shrink:0;">✏️</button>
                        <button class="nca-memory-del-btn" data-type="用户偏好" data-index="${i}" style="background:none; border:none; color:#ef4444; cursor:pointer; font-size:11px; padding:2px 6px; flex-shrink:0;">✕</button>
                    </div>`;
                });
            }
            html += `</div></div>`;

            // ── 区块2: 历史问题与解决方案 ──
            html += `<div style="background:rgba(255,255,255,0.02); border:1px solid var(--nca-border); border-radius:6px; margin-bottom:10px; overflow:hidden;">
                <div class="nca-mem-section-header" data-section="faq" style="display:flex; justify-content:space-between; align-items:center; padding:8px 12px; cursor:pointer; user-select:none;">
                    <span style="color:var(--nca-accent); font-size:11px; font-family:var(--nca-font-mono);">${t('settings.memory_faq_title')} <span style="color:#6b7280;">(${问题列表.length})</span></span>
                    <span class="nca-mem-toggle" style="color:#6b7280; font-size:10px;">▾</span>
                </div>
                <div class="nca-mem-section-body" data-body="faq" style="padding:0 12px 8px;">
                    <div style="color:#6b7280; font-size:10px; padding:4px 0; font-style:italic;">${t('settings.memory_faq_hint')}</div>`;
            if (问题列表.length === 0) {
                html += `<div style="color:#6b7280; padding:8px 0; font-size:11px; text-align:center;">${t('settings.memory_empty')}</div>`;
            } else {
                问题列表.forEach((item, i) => {
                    html += `<div style="display:flex; justify-content:space-between; align-items:flex-start; padding:5px 0; border-bottom:1px solid rgba(255,255,255,0.05);">
                        <div style="flex:1; min-width:0;">
                            <div style="color:var(--nca-fg-dim); font-size:11px;">${t('settings.memory_q_prefix')}${_转义HTML(item.question || '')}</div>
                            <div style="color:#6b7280; font-size:10px; margin-top:2px;">${t('settings.memory_a_prefix')}${_转义HTML((item.solution || '').substring(0, 80))}${(item.solution || '').length > 80 ? '...' : ''}</div>
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
                    <span style="color:var(--nca-accent); font-size:11px; font-family:var(--nca-font-mono);">${t('settings.memory_plugin_title')} <span style="color:#6b7280;">(${插件Keys.length})</span></span>
                    <span class="nca-mem-toggle" style="color:#6b7280; font-size:10px;">▾</span>
                </div>
                <div class="nca-mem-section-body" data-body="plugin" style="padding:0 12px 8px;">
                    <div id="nca-pm-add-form" style="display:flex; gap:6px; align-items:center; margin:8px 0;">
                        <input id="nca-pm-name" type="text" placeholder="${t('settings.memory_plugin_name_ph')}" style="flex:0 0 30%; min-width:0; padding:5px 8px; font-size:11px; background:var(--nca-bg-secondary); color:var(--nca-fg); border:1px solid var(--nca-border); border-radius:4px;">
                        <input id="nca-pm-content" type="text" placeholder="${t('settings.memory_plugin_content_ph')}" style="flex:1 1 50%; min-width:0; padding:5px 8px; font-size:11px; background:var(--nca-bg-secondary); color:var(--nca-fg); border:1px solid var(--nca-border); border-radius:4px;">
                        <button id="nca-pm-add-btn" class="nca-btn nca-btn-xs" style="flex:0 0 auto; font-size:11px; padding:5px 10px;">${t('settings.memory_add')}</button>
                    </div>`;
            if (插件Keys.length === 0) {
                html += `<div style="color:#6b7280; padding:8px 0; font-size:11px; text-align:center;">${t('settings.memory_empty')}</div>`;
            } else {
                插件Keys.forEach(插件名 => {
                    const pm = 插件记忆[插件名];
                    const 信息 = pm["项目信息"] || {};
                    const 上下文列表 = pm["上下文"] || [];
                    html += `<div style="padding:6px 0; border-bottom:1px solid rgba(255,255,255,0.05);">
                        <div style="display:flex; justify-content:space-between; align-items:center;">
                            <span style="color:var(--nca-fg); font-size:11px; font-weight:600;">📦 ${_转义HTML(插件名)}</span>
                            <button class="nca-memory-del-btn" data-type="plugin" data-plugin="${_转义HTML(插件名)}" title="${t('settings.memory_plugin_delete')}" style="background:none; border:none; color:#ef4444; cursor:pointer; font-size:11px; padding:2px 6px; flex-shrink:0;">✕</button>
                        </div>`;
                    if (信息["技术栈"] && 信息["技术栈"].length > 0) {
                        html += `<div style="color:var(--nca-fg-dim); font-size:10px; margin-top:2px;">${t('settings.memory_tech_stack', { stack: _转义HTML(信息["技术栈"].join(', ')) })}</div>`;
                    }
                    if (信息["最后活跃"]) {
                        html += `<div style="color:#6b7280; font-size:10px;">${t('settings.memory_last_active', { date: _转义HTML(信息["最后活跃"]) })}</div>`;
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
                    // 删除整个插件记忆需单独确认文案（会连带删除全部上下文）
                    const 确认文案 = type === 'plugin'
                        ? t('settings.memory_confirm_delete_plugin', { name: plugin })
                        : t('settings.memory_confirm_delete');
                    if (!confirm(确认文案)) return;
                    try {
                        const body = { type: 'item', 记忆类型: type, plugin_path: plugin };
                        if (type !== 'plugin') body.index = index;
                        const rj = await 请求('DELETE', '/memories', body);
                        if (rj.success) {
                            加载记忆数据(panelRef);
                            Toast && Toast.show && Toast.show(t('settings.memory_deleted'), 'success');
                        } else {
                            Toast && Toast.show && Toast.show(rj.message || t('settings.memory_delete_fail'), 'error');
                        }
                    } catch (e) {
                        Toast && Toast.show && Toast.show(t('settings.memory_delete_fail_msg', { error: e.message || e }), 'error');
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
                    addBtn.textContent = t('settings.memory_save_edit');
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
                        Toast && Toast.show && Toast.show(t('settings.memory_field_required'), 'error');
                        return;
                    }
                    if (addBtn.dataset.editing === '1' && !confirm(t('settings.memory_confirm_edit'))) return;
                    addBtn.disabled = true;
                    const 原文本 = addBtn.dataset.editing === '1' ? t('settings.memory_save_edit') : t('settings.memory_add');
                    addBtn.textContent = t('common.saving');
                    try {
                        const rj = await 请求('POST', '/memories', { key, value });
                        if (rj.success) {
                            Toast && Toast.show && Toast.show(t('settings.memory_save_ok'), 'success');
                            keyInput.value = '';
                            valueInput.value = '';
                            addBtn.dataset.editing = '';
                            addBtn.textContent = t('settings.memory_add');
                            加载记忆数据(panelRef);
                        } else {
                            Toast && Toast.show && Toast.show(rj.message || t('settings.save_fail'), 'error');
                            addBtn.textContent = 原文本;
                        }
                    } catch (e) {
                        Toast && Toast.show && Toast.show(t('settings.memory_save_fail_msg', { error: e.message || e }), 'error');
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
                        Toast && Toast.show && Toast.show(t('settings.memory_plugin_required'), 'error');
                        return;
                    }
                    pmAddBtn.disabled = true;
                    pmAddBtn.textContent = t('common.saving');
                    try {
                        const rj = await 请求('POST', '/memories/plugin', { plugin_name, content });
                        if (rj.success) {
                            Toast && Toast.show && Toast.show(t('settings.memory_save_ok'), 'success');
                            pmNameInput.value = '';
                            pmContentInput.value = '';
                            加载记忆数据(panelRef);
                        } else {
                            Toast && Toast.show && Toast.show(rj.message || t('settings.save_fail'), 'error');
                            pmAddBtn.textContent = t('settings.memory_add');
                        }
                    } catch (e) {
                        Toast && Toast.show && Toast.show(t('settings.memory_save_fail_msg', { error: e.message || e }), 'error');
                        pmAddBtn.textContent = t('settings.memory_add');
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
            contentDiv.innerHTML = `<div style="color:#ef4444; padding:8px;">${t('settings.memory_load_fail')}</div>`;
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
                if (!confirm(t('settings.memory_confirm_clear'))) return;
                clearBtn.disabled = true;
                clearBtn.textContent = t('settings.memory_clearing');
                try {
                    const rj = await 请求('DELETE', '/memories', {});
                    if (rj.success) {
                        Toast && Toast.show && Toast.show(t('settings.memory_cleared'), 'success');
                        加载记忆数据(panel);
                    } else {
                        Toast && Toast.show && Toast.show(rj.message || t('settings.memory_clear_fail'), 'error');
                    }
                } catch (e) {
                    Toast && Toast.show && Toast.show(t('settings.memory_clear_fail_msg', { error: e.message || e }), 'error');
                } finally {
                    clearBtn.disabled = false;
                    clearBtn.textContent = t('settings.memory_clear');
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
                <div class="nca-metric-label">${t("monitor.p90")}</div>
                <div class="nca-metric-value" id="nca-m-p90">--</div>
            </div>
            <div class="nca-metric-card">
                <div class="nca-metric-label">${t("monitor.error_rate")}</div>
                <div class="nca-metric-value" id="nca-m-error">--</div>
            </div>
            <div class="nca-metric-card">
                <div class="nca-metric-label">${t("monitor.connections")}</div>
                <div class="nca-metric-value" id="nca-m-conn">--</div>
            </div>
            <div class="nca-metric-card">
                <div class="nca-metric-label">${t("monitor.inferences")}</div>
                <div class="nca-metric-value" id="nca-m-infer">--</div>
            </div>
            <div class="nca-metric-card">
                <div class="nca-metric-label">${t("monitor.uptime")}</div>
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

    // 保存按钮 + 状态指示
    const saveBtn = el("button", { class: "nca-btn nca-btn-primary", text: t("settings.save") });
    const saveStatus = el("span", { style: { fontSize: "12px", marginLeft: "8px", transition: "opacity 0.3s" } });
    saveBtn.addEventListener("click", async () => {
        saveBtn.textContent = t("common.saving");
        saveBtn.disabled = true;
        saveStatus.textContent = t("common.saving");
        saveStatus.style.color = "var(--nca-fg-dim)";
        saveStatus.style.opacity = "1";
        // 把表单输入暂存回当前配置，设置面板中选中的配置即为激活配置
        暂存输入到当前配置();
        const 激活配置 = 当前配置();
        const success = await 保存设置({
            local_path: localPathInput.value,
            api_profiles: 配置列表,
            active_api_profile_id: 激活配置.id,
            api_provider: 激活配置.api_provider,
            base_url: 激活配置.base_url,
            api_key: 激活配置.api_key,
            model_name: 激活配置.model_name,
            max_tokens: 激活配置.max_tokens || 模型默认MaxTokens(激活配置.model_name),
            reasoning_effort: 激活配置.reasoning_effort || "",
            github_token: githubTokenInput.value.trim(),
            github_username: githubUserInput.value.trim(),
            github_visibility: panel.querySelector('input[name="github_visibility"]:checked')?.value || "public",
            enable_planning: planningToggle.checked,
            proactive_pitfall_check: pitfallToggle.checked,
            max_tool_rounds: parseInt(轮次Select.value, 10),
        });
        if (success) {
            saveStatus.textContent = t("settings.saved_short");
            saveStatus.style.color = "var(--nca-accent)";
            setTimeout(() => { saveStatus.style.opacity = "0"; }, 2000);
            Toast.success(t("settings.saved"));
            overlay.remove();
            if (更新状态栏Fn) 更新状态栏Fn(t("common.ready"));
        } else {
            saveStatus.textContent = t("settings.save_fail");
            saveStatus.style.color = "var(--nca-error)";
            saveBtn.textContent = t("common.retry");
            saveBtn.disabled = false;
        }
    });
    body.appendChild(el("div", { style: { display: "flex", alignItems: "center" } }, [saveBtn, saveStatus]));

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
// Re-export 已提取的组件，保持外部导入兼容
// ═══════════════════════════════════════════════════════════════
export { 显示文件夹浏览器 } from "./文件夹浏览器.js";
export { 显示创建项目对话框 } from "./项目创建对话框.js";
export { 显示删除确认 } from "./删除确认对话框.js";
