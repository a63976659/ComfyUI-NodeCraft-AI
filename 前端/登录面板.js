// ═══════════════════════════════════════════════════════════════
// 登录面板.js — RanKing 一键登录（Neo-Noir Luxury Terminal）
// NodeCraft AI - 节点梦工厂
//
// 设计原则：
//   · NCA 不再持有自己的用户/密码体系，用户身份由 RanKing 统一签发；
//   · 凭证读取自 RanKing 写入的 localStorage / sessionStorage：
//       - ComfyCommunity_Token  → JWT / 会话 token
//       - ComfyCommunity_User   → 用户信息（JSON 字符串，结构 { user: {...}, expireAt: ... }）
//   · 通过本地后端代理 (/nca/billing/verify-login) 校验 token 合法性
//     与有效期，避免前端直接信任浏览器存储；
//   · 余额来自 (/nca/billing/balance)，按需刷新并缓存到内存。
//
// 导出（保持与历史版本调用方兼容）：
//   - 创建登录面板(container)
//   - 获取登录状态() → { loggedIn, username, displayName, isMember, plan, balance, ... }
//   - 获取Token()    → token | null
//   - 登出()         → 清除 NCA 内存缓存（不动 RanKing 存储）
// ═══════════════════════════════════════════════════════════════

import { el, Toast } from "./工具函数.js";

// ─── RanKing 凭证存储键 ────────────────────────────────
const RK_TOKEN_KEY = "ComfyCommunity_Token";
const RK_USER_KEY  = "ComfyCommunity_User";

// ─── 本地后端端点 ────────────────────────────────────
const API_VERIFY_LOGIN = "/nca/billing/verify-login";
const API_BALANCE      = "/nca/billing/balance";
const API_DEDUCT       = "/nca/billing/deduct";

// ─── NCA 内存缓存（避免重复读取 localStorage / 频繁请求余额）──
const _ncaState = {
    token:      null,
    user:       null,    // { id, name, nickname, avatar, email, phone, plan, ... }
    balance:    null,    // { amount, currency, ... } | { error }
    verifiedAt: 0,
    balanceAt:  0,
};

// 余额缓存有效期（毫秒）
const BALANCE_TTL = 30_000;

// ─── 工具：读取 RanKing 凭证（localStorage 优先，sessionStorage 降级）──
function _读取RanKing凭证() {
    const readKey = (k) => {
        try {
            return localStorage.getItem(k) || sessionStorage.getItem(k) || null;
        } catch (_) {
            return null;
        }
    };
    const token = readKey(RK_TOKEN_KEY);
    const userRaw = readKey(RK_USER_KEY);
    let user = null;
    if (userRaw) {
        try {
            const parsed = JSON.parse(userRaw);
            // 兼容 RanKing 的 { user: {...}, expireAt: ... } 结构
            user = parsed.user || parsed;
        } catch (_) {
            // 兼容字段被存为纯字符串用户名的情况
            user = { name: userRaw };
        }
    }
    return { token, user };
}

// ─── 工具：本地后端调用（同源相对路径，无需配置）──
async function _本地调用(path, opts = {}) {
    try {
        const headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
        if (opts.token) headers["Authorization"] = "Bearer " + opts.token;
        const resp = await fetch(path, {
            method: opts.method || "GET",
            headers,
            body: opts.body ? JSON.stringify(opts.body) : undefined,
        });
        let data = {};
        try { data = await resp.json(); } catch (_) { /* 容忍非 JSON 响应 */ }
        return { ok: resp.ok, status: resp.status, data };
    } catch (e) {
        return { ok: false, status: 0, data: { error: "网络错误：" + (e && e.message || e) } };
    }
}

// ─── 公共导出：状态读写 ────────────────────────────
export function 获取Token() {
    if (_ncaState.token) return _ncaState.token;
    // 内存未命中时，主动尝试从 RanKing 存储引导一次
    const { token } = _读取RanKing凭证();
    if (token) _ncaState.token = token;
    return _ncaState.token || null;
}

export function 获取登录状态() {
    const token = _ncaState.token;
    const user  = _ncaState.user;
    if (!token || !user) {
        return { loggedIn: false, username: "", isMember: false, plan: "guest" };
    }
    const username  = user.name || user.username || user.nickname || user.email || "用户";
    const nickname  = user.nickname || user.name || "";
    const plan      = (user.plan || user.tier || "free").toString().toLowerCase();
    return {
        loggedIn:    true,
        token,
        username,
        nickname,
        displayName: nickname || username,
        avatar:      user.avatar || user.avatar_url || "",
        email:       user.email || "",
        phone:       user.phone || "",
        status:      user.status || "active",
        plan,
        isMember:    plan === "pro" || plan === "vip" || plan === "enterprise",
        balance:     _ncaState.balance,
        source:      "ranking",
    };
}

export function 登出() {
    // 仅清除 NCA 内存中的引用，绝不动 RanKing 自身的 localStorage/sessionStorage
    _ncaState.token      = null;
    _ncaState.user       = null;
    _ncaState.balance    = null;
    _ncaState.verifiedAt = 0;
    _ncaState.balanceAt  = 0;
}

// ─── 自动登录（从 RanKing 存储引导凭证并验证）────────────────
// 返回 true 表示已登录或自动登录成功；false 表示需要手动登录
export async function 自动登录() {
    // 内存中已有有效凭证，直接返回
    if (_ncaState.token && _ncaState.user) return true;

    // 从 RanKing 存储读取凭证
    const { token, user } = _读取RanKing凭证();
    if (!token) return false;

    // 调用后端验证 token 合法性与有效期
    const r = await _本地调用(API_VERIFY_LOGIN, {
        method: "POST",
        body: { token },
    });

    if (r.ok && r.data && (r.data.valid === true || r.data.success === true || r.data.ok === true)) {
        _ncaState.token      = token;
        _ncaState.user       = r.data.user || user || { name: "RanKing 用户" };
        _ncaState.verifiedAt = Date.now();
        return true;
    }

    return false;
}

// ─── UI 主入口 ────────────────────────────────────
export function 创建登录面板(container, opts) {
    const onEnter = opts && typeof opts.onEnter === 'function' ? opts.onEnter : null;
    container.classList.add("nc-login-root");
    container.innerHTML = "";

    if (_ncaState.token && _ncaState.user) {
        const profile = _渲染Profile(container, onEnter);
        container.appendChild(profile);
        // 异步刷新余额（不阻塞渲染）
        _刷新余额(profile, false).catch(() => {});
    } else {
        container.appendChild(_渲染登录提示(container, onEnter));
    }
    return container;
}

// ─── 未登录视图：RanKing 一键登录提示卡 ─────────────
function _渲染登录提示(host, onEnter) {
    const root = el("div", { class: "nc-login-card nc-ranking-card" });

    // 顶部品牌头
    const header = el("div", { class: "nc-login-header" });
    header.appendChild(el("div", { class: "nc-login-brand", text: "RanKing × NodeCraft AI" }));
    header.appendChild(el("div", {
        class: "nc-login-subtitle",
        text: "// single-sign-on · ranking-account · zero-friction",
    }));
    root.appendChild(header);

    // 大图标
    const iconBox = el("div", { class: "nc-ranking-icon" });
    iconBox.textContent = "🔑";
    root.appendChild(iconBox);

    // 文案区
    const title = el("div", { class: "nc-ranking-title", text: "使用 RanKing 账号登录" });
    root.appendChild(title);

    const desc = el("div", {
        class: "nc-ranking-desc",
        text: "NodeCraft AI 直接复用 RanKing 插件中的登录态，无需重复注册。",
    });
    root.appendChild(desc);

    // 凭证检测提示条
    const { token: rkToken, user: rkUser } = _读取RanKing凭证();
    const status = el("div", { class: "nc-ranking-status" });
    const dot = el("span", { class: "nc-ranking-status-dot" });
    status.appendChild(dot);
    const statusText = el("span", { class: "nc-ranking-status-text" });
    if (rkToken) {
        status.classList.add("ok");
        const userHint = (rkUser && (rkUser.name || rkUser.nickname || rkUser.username)) || "已就绪";
        statusText.textContent = `检测到 RanKing 凭证 · ${userHint}`;
    } else {
        status.classList.add("warn");
        statusText.textContent = "未检测到 RanKing 凭证";
    }
    status.appendChild(statusText);
    root.appendChild(status);

    // 错误提示
    const errBox = el("div", { class: "nc-login-error" });
    root.appendChild(errBox);

    // 主按钮
    const btn = el("button", {
        class: "nc-login-btn nc-login-btn-primary nc-ranking-btn",
        text: "🔑   使用 RanKing 账号登录",
    });
    const _恢复按钮 = () => {
        btn.disabled = false;
        btn.textContent = "🔑   使用 RanKing 账号登录";
    };
    btn.addEventListener("click", async () => {
        _设错误(root, "");
        const { token, user } = _读取RanKing凭证();
        if (!token) {
            _设错误(root, "请先在 RanKing 插件中登录账号");
            return;
        }
        btn.disabled = true;
        btn.textContent = "⏳   验证登录态…";

        const r = await _本地调用(API_VERIFY_LOGIN, {
            method: "POST",
            body: { token },
        });

        if (r.ok && r.data && (r.data.valid === true || r.data.success === true || r.data.ok === true)) {
            _ncaState.token      = token;
            _ncaState.user       = r.data.user || user || { name: "RanKing 用户" };
            _ncaState.verifiedAt = Date.now();
            Toast.success("登录成功，欢迎回来");
            创建登录面板(host, { onEnter: onEnter });
            return;
        }

        // 失败分支
        _恢复按钮();
        const code = r.data && (r.data.error_code || r.data.code);
        if (r.status === 401 || code === "TOKEN_EXPIRED" || code === "INVALID_TOKEN") {
            _设错误(root, "Token 已过期，请在 RanKing 中重新登录");
        } else if (r.status === 0) {
            _设错误(root, r.data.error || "无法连接本地后端，请确认服务已启动");
        } else {
            _设错误(root, r.data.error || r.data.detail || `验证失败 (${r.status})`);
        }
    });
    root.appendChild(btn);

    // 底部提示
    const hint = el("div", { class: "nc-ranking-hint" });
    if (rkToken) {
        hint.textContent = "NodeCraft AI 不存储您的密码，凭证由 RanKing 统一管理。";
    } else {
        hint.textContent = "前往 ComfyUI 顶部 RanKing 插件登录后再返回此处。";
    }
    root.appendChild(hint);

    return root;
}

// ─── 已登录视图：用户档案 + 余额钱包 ──────────────────
function _渲染Profile(host, onEnter) {
    const state = 获取登录状态();

    const root = el("div", { class: "nc-login-profile nc-ranking-profile" });

    // 顶部光晕装饰条（纯视觉）
    root.appendChild(el("div", { class: "nc-ranking-glow" }));

    // 头像
    const avatar = el("div", { class: "nc-login-avatar" });
    if (state.avatar) {
        const img = el("img", {
            src: state.avatar,
            alt: state.displayName || "avatar",
            class: "nc-avatar-img",
        });
        img.addEventListener("error", () => {
            try { avatar.removeChild(img); } catch (_) {}
            avatar.textContent = (state.displayName || "U").trim().charAt(0).toUpperCase() || "U";
        });
        avatar.appendChild(img);
    } else {
        avatar.textContent = (state.displayName || "U").trim().charAt(0).toUpperCase() || "U";
    }
    root.appendChild(avatar);

    // 信息块
    const info = el("div", { class: "nc-login-info" });

    const nameRow = el("div", { class: "nc-login-name-row" });
    nameRow.appendChild(el("span", { class: "nc-login-username", text: state.displayName }));
    nameRow.appendChild(el("span", {
        class: "nc-login-badge " + (state.isMember ? "nc-login-badge-gold" : "nc-login-badge-free"),
        text: state.isMember ? state.plan.toUpperCase() : "FREE",
    }));
    info.appendChild(nameRow);

    const sourceLine = el("div", { class: "nc-login-meta nc-source-line" });
    sourceLine.appendChild(el("span", { class: "nc-ranking-tag", text: "RanKing" }));
    sourceLine.appendChild(el("span", { class: "nc-source-text", text: "通过 RanKing 单点登录" }));
    info.appendChild(sourceLine);

    if (state.email) {
        info.appendChild(el("div", { class: "nc-login-meta", text: "✉  " + state.email }));
    } else if (state.phone) {
        info.appendChild(el("div", { class: "nc-login-meta", text: "📱  " + state.phone }));
    }

    root.appendChild(info);

    // 余额钱包
    const wallet = el("div", { class: "nc-wallet" });
    const walletHead = el("div", { class: "nc-wallet-head" });
    walletHead.appendChild(el("span", { class: "nc-wallet-label", text: "AI 余额 / BALANCE" }));
    const refresh = el("button", {
        class: "nc-wallet-refresh",
        text: "↻",
        title: "刷新余额",
    });
    refresh.addEventListener("click", () => {
        refresh.classList.add("spinning");
        _刷新余额(root, true).finally(() => {
            setTimeout(() => refresh.classList.remove("spinning"), 240);
        });
    });
    walletHead.appendChild(refresh);
    wallet.appendChild(walletHead);

    const valueEl = el("div", { class: "nc-wallet-value loading", text: "加载中…" });
    wallet.appendChild(valueEl);

    const rechargeHint = el("a", {
        class: "nc-wallet-recharge",
        href: "#",
        text: "余额不足？去 RanKing 充值  →",
    });
    rechargeHint.addEventListener("click", (e) => {
        e.preventDefault();
        Toast.info("请前往 RanKing 插件页面进行充值");
    });
    wallet.appendChild(rechargeHint);
    root.appendChild(wallet);

    // 会员购买区域
    root.appendChild(_渲染会员区域(root));

    // 操作区
    const actions = el("div", { class: "nc-login-actions" });

    if (onEnter) {
        // 入口模式：主按钮为"进入 →"，点击后关闭 overlay 进入工作区
        const enterBtn = el("button", {
            class: "nc-login-btn nc-login-btn-primary",
            text: "进入 →",
        });
        enterBtn.addEventListener("click", onEnter);
        actions.appendChild(enterBtn);
    } else {
        // 非入口模式（星星按钮）：显示退出登录按钮
        const logoutBtn = el("button", {
            class: "nc-login-btn nc-login-btn-ghost",
            text: "退出登录",
        });
        logoutBtn.addEventListener("click", () => {
            登出();
            // 清理缓存的用户状态
            _ncaState.balance = null;
            _ncaState.balanceAt = 0;
            try {
                sessionStorage.removeItem("ComfyCommunity_User");
            } catch (_) {}
            创建登录面板(host, { onEnter: null });
            Toast.info("已退出 NodeCraft AI（RanKing 凭证保留）");
        });
        actions.appendChild(logoutBtn);
    }

    // 小型退出登录按钮（仅在入口模式下显示，低调放置）
    if (onEnter) {
        const smallLogout = el("button", {
            class: "nc-logout-small",
            text: "退出登录",
            title: "退出 NodeCraft AI 登录",
        });
        smallLogout.addEventListener("click", () => {
            登出();
            // 清理缓存的用户状态
            _ncaState.balance = null;
            _ncaState.balanceAt = 0;
            try {
                sessionStorage.removeItem("ComfyCommunity_User");
            } catch (_) {}
            创建登录面板(host, { onEnter: onEnter });
            Toast.info("已退出 NodeCraft AI（RanKing 凭证保留）");
        });
        root.appendChild(actions);
        root.appendChild(smallLogout);
    } else {
        root.appendChild(actions);
    }

    // 用当前缓存（如有）渲染一次余额
    _渲染余额(root);

    return root;
}

// ─── 余额获取（带 TTL 缓存）─────────────────────────
async function _刷新余额(scope, force = false) {
    const token = _ncaState.token;
    if (!token) return;
    if (!force && _ncaState.balance && !_ncaState.balance.error
        && (Date.now() - _ncaState.balanceAt < BALANCE_TTL)) {
        _渲染余额(scope);
        return;
    }
    const r = await _本地调用(API_BALANCE, { method: "GET", token });
    if (r.ok) {
        const payload = r.data && (r.data.balance ?? r.data.data ?? r.data);
        _ncaState.balance = payload && typeof payload === "object" ? payload : { amount: payload };
        _ncaState.balanceAt = Date.now();
    } else if (r.status === 401) {
        _ncaState.balance = { error: "登录态已失效，请重新登录" };
    } else {
        _ncaState.balance = { error: r.data.error || r.data.detail || `加载失败 (${r.status || "网络"})` };
    }
    _渲染余额(scope);
}

function _渲染余额(scope) {
    if (!scope) return;
    const valueEl = scope.querySelector(".nc-wallet-value");
    if (!valueEl) return;
    const b = _ncaState.balance;
    if (!b) {
        valueEl.textContent = "加载中…";
        valueEl.className = "nc-wallet-value loading";
        valueEl.removeAttribute("title");
        return;
    }
    if (b.error) {
        valueEl.textContent = "— —";
        valueEl.title = b.error;
        valueEl.className = "nc-wallet-value error";
        return;
    }
    const raw = (b.amount ?? b.balance ?? b.value ?? 0);
    const num = Number(raw);
    const display = Number.isFinite(num) ? num.toFixed(2) : String(raw);
    const currency = b.currency || b.unit || "积分";
    valueEl.textContent = `${display}`;
    valueEl.className = "nc-wallet-value";
    if (Number.isFinite(num) && num <= 0) valueEl.classList.add("low");
    valueEl.removeAttribute("title");

    // 货币单位：以独立小标签呈现（若已存在则更新）
    let unit = scope.querySelector(".nc-wallet-unit");
    if (!unit) {
        unit = el("span", { class: "nc-wallet-unit" });
        valueEl.parentElement && valueEl.parentElement.insertBefore(unit, valueEl.nextSibling);
    }
    unit.textContent = currency;
}

// ─── 错误提示工具 ────────────────────────────────
function _设错误(scope, msg) {
    const err = scope.querySelector(".nc-login-error");
    if (!err) return;
    err.textContent = msg || "";
    err.style.display = msg ? "block" : "none";
}

// ─── 会员购买区域 ────────────────────────────────
const _会员档次 = [
    { key: "basic",    name: "Basic",    label: "基础版",  amount: 1, color: "green",   reason: "membership_purchase_basic" },
    { key: "pro",      name: "Pro",      label: "进阶版",  amount: 2, color: "accent",  reason: "membership_purchase_pro" },
    { key: "premium",  name: "Premium",  label: "高级版",  amount: 3, color: "purple",  reason: "membership_purchase_premium" },
];

function _渲染会员区域(scope) {
    const section = el("div", { class: "nc-membership-section" });

    const header = el("div", { class: "nc-membership-header" });
    header.appendChild(el("span", { class: "nc-membership-title", text: "开通会员" }));
    header.appendChild(el("span", { class: "nc-membership-subtitle", text: "// MEMBERSHIP" }));
    section.appendChild(header);

    const cards = el("div", { class: "nc-membership-cards" });
    for (const tier of _会员档次) {
        const card = el("div", { class: `nc-membership-card nc-membership-card--${tier.color}` });

        const nameRow = el("div", { class: "nc-membership-name-row" });
        nameRow.appendChild(el("span", { class: "nc-membership-name", text: tier.name }));
        nameRow.appendChild(el("span", { class: "nc-membership-label", text: tier.label }));
        card.appendChild(nameRow);

        const desc = el("div", { class: "nc-membership-desc", text: tier.amount + " 积分" });
        card.appendChild(desc);

        const btn = el("button", {
            class: "nc-membership-buy-btn",
            text: "购买",
        });
        btn.addEventListener("click", async () => {
            await _购买会员(tier, btn, scope);
        });
        card.appendChild(btn);

        cards.appendChild(card);
    }
    section.appendChild(cards);

    return section;
}

async function _购买会员(tier, btn, scope) {
    const token = _ncaState.token;
    if (!token) {
        Toast.error("登录态已失效，请重新登录");
        return;
    }

    const confirmed = confirm(`确认购买【${tier.label}（${tier.name}）】会员？\n将扣除 ${tier.amount} 积分。`);
    if (!confirmed) return;

    btn.disabled = true;
    const originalText = btn.textContent;
    btn.textContent = "处理中…";

    const r = await _本地调用(API_DEDUCT, {
        method: "POST",
        token,
        body: {
            token,
            amount: tier.amount,
            reason: tier.reason,
            reference_id: `membership_${Date.now()}`,
        },
    });

    if (r.ok && r.data && r.data.success === true) {
        Toast.success(`购买成功！已扣除 ${tier.amount} 积分`);
        await _刷新余额(scope, true);
    } else {
        const code = r.data && (r.data.code || r.data.error_code);
        const msg  = r.data && (r.data.error || r.data.detail || r.data.message);

        if (code === "INSUFFICIENT_BALANCE") {
            Toast.warning("余额不足，请前往 RanKing 充值");
        } else if (code === "INVALID_TOKEN" || code === "TOKEN_EXPIRED") {
            Toast.error("Token 已失效，请重新登录");
        } else {
            Toast.error(msg || `购买失败 (${r.status || "网络"})`);
        }
    }

    btn.disabled = false;
    btn.textContent = originalText;
}
