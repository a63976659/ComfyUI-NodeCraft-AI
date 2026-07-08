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
//   · Token算力余额来自 (/nca/billing/token-balance)，按需刷新并缓存到内存。
//
// 导出（保持与历史版本调用方兼容）：
//   - 创建登录面板(container)
//   - 获取登录状态() → { loggedIn, username, displayName, isMember, plan, balance, ... }
//   - 获取Token()    → token | null
//   - 登出()         → 清除 NCA 内存缓存（不动 RanKing 存储）
// ═══════════════════════════════════════════════════════════════

import { el, Toast } from "./工具函数.js";
import { 事件总线 } from "./交互与状态.js";

// ─── RanKing 凭证存储键 ────────────────────────────────
const RK_TOKEN_KEY = "ComfyCommunity_Token";
const RK_USER_KEY  = "ComfyCommunity_User";

// ─── 本地后端端点 ────────────────────────────────────
const API_VERIFY_LOGIN = "/nca/billing/verify-login";
const API_BALANCE      = "/nca/billing/token-balance";
const API_DEDUCT       = "/nca/billing/deduct";

// ─── NCA 内存缓存（避免重复读取 localStorage / 频繁请求余额）──
const _ncaState = {
    token:      null,
    user:       null,    // { id, name, nickname, avatar, email, phone, plan, ... }
    account:    null,    // RanKing 用户唯一标识（verify-token 返回）
    balance:    null,    // { amount, currency, ... } | { error }
    tier_info:  null,    // 会员信息 { tier, expire, api_available } | null（免费用户为 null）
    verifiedAt: 0,
    balanceAt:  0,
};

// 余额缓存有效期（毫秒）
const BALANCE_TTL = 30_000;

// 当前已渲染的 Profile DOM 根节点（供持久监听器刷新显示）
let _profileRoot = null;

// 当前登录面板挂载容器与入口回调（供购买会员后整体重渲染）
let _currentHost = null;
let _currentOnEnter = null;

// 触发登录面板整体重渲染（会员状态/卡片会随之刷新）
function _刷新登录面板() {
    if (_currentHost) {
        创建登录面板(_currentHost, { onEnter: _currentOnEnter });
    }
}

// 模块级别注册持久余额更新监听器（确保 _ncaState.balance 始终被更新，即使未打开个人资料面板）
事件总线.on("nca:balance-updated", (balance) => {
    if (typeof balance === "number") {
        _ncaState.balance = { amount: balance, currency: "token", tokens_per_credit: 200000 };
        _ncaState.balanceAt = Date.now();
        // 如果 Profile 面板已创建，刷新显示
        if (_profileRoot) {
            _渲染余额(_profileRoot);
        }
    }
});

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
        tier_info:   _ncaState.tier_info,
        source:      "ranking",
    };
}

export function 登出() {
    // 仅清除 NCA 内存中的引用，绝不动 RanKing 自身的 localStorage/sessionStorage
    _ncaState.token      = null;
    _ncaState.user       = null;
    _ncaState.account    = null;
    _ncaState.balance    = null;
    _ncaState.tier_info  = null;
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
        // 保存 RanKing 返回的 account（用户唯一标识），供充值等接口使用
        _ncaState.account    = r.data.account || (user && user.account) || "";
        _ncaState.verifiedAt = Date.now();
        // 登录成功后直接使用后端返回的 nca_balance（云端数据源）
        if (r.data.nca_balance !== undefined) {
            _ncaState.balance = { amount: r.data.nca_balance, currency: "token", tokens_per_credit: 200000 };
            _ncaState.balanceAt = Date.now();
        }
        // 保存会员信息（tier_info），无则视为免费用户
        _ncaState.tier_info = r.data.tier_info || null;
        return true;
    }

    return false;
}

// ─── UI 主入口 ────────────────────────────────────
export function 创建登录面板(container, opts) {
    const onEnter = opts && typeof opts.onEnter === 'function' ? opts.onEnter : null;
    container.classList.add("nc-login-root");
    container.innerHTML = "";

    // 记录当前容器与入口回调，供购买会员后整体重渲染
    _currentHost = container;
    _currentOnEnter = onEnter;

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
            // 保存 RanKing 返回的 account（用户唯一标识），供充值等接口使用
            _ncaState.account    = r.data.account || (user && user.account) || "";
            _ncaState.verifiedAt = Date.now();
            // 登录成功后直接使用后端返回的 nca_balance（云端数据源）
            if (r.data.nca_balance !== undefined) {
                _ncaState.balance = { amount: r.data.nca_balance, currency: "token", tokens_per_credit: 200000 };
                _ncaState.balanceAt = Date.now();
            }
            // 保存会员信息（tier_info），无则视为免费用户
            _ncaState.tier_info = r.data.tier_info || null;
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
    // 会员徒章：依据有效会员等级显示
    const 徽章等级 = _当前会员等级();
    const 是会员 = 徽章等级 === "pro" || 徽章等级 === "premium";
    nameRow.appendChild(el("span", {
        class: "nc-login-badge " + (是会员 ? "nc-login-badge-gold" : "nc-login-badge-free"),
        text: 是会员 ? 徽章等级.toUpperCase() : "FREE",
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

    // 会员状态卡片（基础/进阶/高级 + 到期时间）
    root.appendChild(_渲染会员状态卡());

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

    // 余额详情行：积分换算
    const detailEl = el("div", {
        class: "nc-wallet-detail",
        style: {
            fontSize: "11px",
            color: "var(--nca-fg-dim, #a0a0b8)",
            marginTop: "4px",
            lineHeight: "1.5",
            minHeight: "16px",
        },
    });
    wallet.appendChild(detailEl);

    const rechargeBtn = el("button", {
        class: "nc-wallet-recharge-btn",
        style: {
            background: "var(--nca-accent, #6366f1)",
            color: "#fff",
            border: "none",
            borderRadius: "8px",
            padding: "8px 20px",
            fontSize: "13px",
            fontWeight: "600",
            cursor: "pointer",
            marginTop: "8px",
            width: "100%",
        },
        text: "充值",
    });
    rechargeBtn.addEventListener("click", () => {
        _显示充值对话框(root);
    });
    wallet.appendChild(rechargeBtn);
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

    // 记录当前 Profile 根节点，供模块级持久监听器刷新显示
    _profileRoot = root;

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
        const data = r.data || {};
        _ncaState.balance = {
            amount: data.token_balance ?? 0,
            currency: "token",
            tokens_per_credit: data.tokens_per_credit ?? 200000,
        };
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
    const detailEl = scope.querySelector(".nc-wallet-detail");
    const b = _ncaState.balance;
    if (!b) {
        valueEl.textContent = "加载中…";
        valueEl.className = "nc-wallet-value loading";
        valueEl.removeAttribute("title");
        if (detailEl) detailEl.textContent = "";
        return;
    }
    if (b.error) {
        valueEl.textContent = "— —";
        valueEl.title = b.error;
        valueEl.className = "nc-wallet-value error";
        if (detailEl) detailEl.textContent = "";
        return;
    }
    const raw = (b.amount ?? b.balance ?? b.value ?? 0);
    const num = Number(raw);
    const display = Number.isFinite(num) ? Math.floor(num).toLocaleString() : String(raw);
    const currency = b.currency || b.unit || "token";
    const tpc = b.tokens_per_credit || 200000;
    const lowThreshold = tpc * 0.1; // 低于 0.1 积分时警告
    valueEl.textContent = `${display}`;
    valueEl.className = "nc-wallet-value";
    if (Number.isFinite(num) && num <= lowThreshold) valueEl.classList.add("low");
    valueEl.removeAttribute("title");

    // 货币单位：以独立小标签呈现（若已存在则更新）
    let unit = scope.querySelector(".nc-wallet-unit");
    if (!unit) {
        unit = el("span", { class: "nc-wallet-unit" });
        valueEl.parentElement && valueEl.parentElement.insertBefore(unit, valueEl.nextSibling);
    }
    unit.textContent = currency;

    // 积分换算
    if (detailEl) {
        if (Number.isFinite(num)) {
            const credits = (num / tpc).toFixed(2);
            detailEl.textContent = `≈ ${credits} 积分`;
        } else {
            detailEl.textContent = "";
        }
        detailEl.style.color = (Number.isFinite(num) && num <= lowThreshold)
            ? "var(--nca-warn, #f59e0b)"
            : "var(--nca-fg-dim, #a0a0b8)";
    }
}

// ─── 错误提示工具 ────────────────────────────────
function _设错误(scope, msg) {
    const err = scope.querySelector(".nc-login-error");
    if (!err) return;
    err.textContent = msg || "";
    err.style.display = msg ? "block" : "none";
}

// ─── 会员体系 ────────────────────────────────────
const API_MEMBERSHIP = "/nca/billing/membership-purchase";

// 会员购买本次会话提醒关闭标记（充值对话框内使用）
const _MEMBER_REMINDER_KEY = "nca_membership_reminder_dismissed";

// 会员档次配置（展示文案以任务需求为准，实际扣费由后端依 tier 计算）
const _会员档次 = [
    { key: "basic",    name: "基础版",  period: "永久",   price: "1 积分",  perk: "解锁基础功能",       color: "basic"   },
    { key: "pro",      name: "进阶版",  period: "1 个月", price: "20 积分", perk: "赠送 12 元 Token",   color: "pro"     },
    { key: "premium",  name: "高级版",  period: "1 个月", price: "50 积分", perk: "赠送 45 元 Token",   color: "premium" },
];

// 等级权重（用于"当前方案"判断：拥有等级 >= 卡片等级则视为已拥有）
const _等级权重 = { basic: 1, pro: 2, premium: 3 };

// 等级展示名称
const _等级名称 = { basic: "基础会员", pro: "进阶会员", premium: "高级会员" };

// ─── 会员状态工具 ───
function _解析到期时间(expire) {
    if (expire === null || expire === undefined || expire === "") return null;
    try {
        const d = (typeof expire === "number") ? new Date(expire) : new Date(String(expire));
        return isNaN(d.getTime()) ? null : d;
    } catch (_) {
        return null;
    }
}

function _格式化到期(expire) {
    const d = _解析到期时间(expire);
    if (!d) return "";
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, "0");
    const day = String(d.getDate()).padStart(2, "0");
    return `${y}-${m}-${day} 到期`;
}

// 会员是否仍有效（basic 永久有效；pro/premium 校验到期时间）
function _会员是否有效(info) {
    if (!info || !info.tier) return false;
    const tier = String(info.tier).toLowerCase();
    if (!_等级权重[tier]) return false;
    if (tier === "basic") return true; // 永久
    const d = _解析到期时间(info.expire);
    if (d === null) return true; // 无法解析到期时间时宽容视为有效
    return d.getTime() > Date.now();
}

// 当前有效会员等级（返回 "basic"|"pro"|"premium"，无有效会员返回 null）
function _当前会员等级() {
    const info = _ncaState.tier_info;
    if (!info || !_会员是否有效(info)) return null;
    const tier = String(info.tier).toLowerCase();
    return _等级权重[tier] ? tier : null;
}

// ─── 会员状态卡片 ───
function _渲染会员状态卡() {
    const info = _ncaState.tier_info;
    const 有效 = _会员是否有效(info);
    let tier = (info && 有效) ? String(info.tier).toLowerCase() : "basic";
    if (!_等级名称[tier]) tier = "basic";

    const card = el("div", { class: `nc-member-status nc-member-status--${tier}` });

    const badge = el("span", {
        class: `nc-member-badge nc-member-badge--${tier}`,
        text: _等级名称[tier],
    });
    card.appendChild(badge);

    // 到期/说明文案
    let 说明 = "";
    if (tier === "pro" || tier === "premium") {
        说明 = _格式化到期(info && info.expire) || "有效期内";
    } else {
        说明 = "免费用户";
    }
    card.appendChild(el("span", { class: "nc-member-expire", text: 说明 }));

    return card;
}

// ─── 会员购买区域 ────────────────────────────────
function _渲染会员区域(scope) {
    const section = el("div", { class: "nc-membership-section" });

    const header = el("div", { class: "nc-membership-header" });
    header.appendChild(el("span", { class: "nc-membership-title", text: "开通会员" }));
    header.appendChild(el("span", { class: "nc-membership-subtitle", text: "// MEMBERSHIP" }));
    section.appendChild(header);

    const 当前等级 = _当前会员等级();
    const 当前权重 = 当前等级 ? _等级权重[当前等级] : 0;

    const cards = el("div", { class: "nc-membership-cards" });
    for (const tier of _会员档次) {
        const card = el("div", { class: `nc-membership-card nc-membership-card--${tier.color}` });

        const nameRow = el("div", { class: "nc-membership-name-row" });
        nameRow.appendChild(el("span", { class: "nc-membership-name", text: tier.name }));
        nameRow.appendChild(el("span", { class: "nc-membership-label", text: tier.period }));
        card.appendChild(nameRow);

        card.appendChild(el("div", { class: "nc-membership-price", text: tier.price }));
        card.appendChild(el("div", { class: "nc-membership-perk", text: tier.perk }));

        // 已拥有该等级或更高等级
        const 已拥有 = 当前权重 > 0 && 当前权重 >= _等级权重[tier.key];
        // 基础版已拥有：显示"已解锁"，可点击但仅提示，不发起购买
        const 基础版已解锁 = 已拥有 && tier.key === "basic";
        const btn = el("button", {
            class: "nc-membership-buy-btn",
            text: 已拥有 ? (基础版已解锁 ? "已解锁" : "当前方案") : "购买",
        });
        if (基础版已解锁) {
            // 基础会员已解锁：不禁用，点击仅提示，不发起购买请求
            btn.addEventListener("click", () => {
                Toast.info("您已拥有基础会员");
            });
        } else if (已拥有) {
            btn.disabled = true;
        } else {
            btn.addEventListener("click", async () => {
                await _购买会员(tier, btn, scope);
            });
        }
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

    // 自定义确认弹窗（挂载到侧边栏 scope 容器内）
    const overlay = document.createElement("div");
    overlay.className = "nc-sync-overlay";

    const modal = document.createElement("div");
    modal.className = "nc-sync-modal";
    modal.style.width = "320px";

    // 标题
    const header = document.createElement("div");
    header.className = "nc-sync-header";
    header.innerHTML = `<span>确认购买</span>`;
    const closeBtn = document.createElement("span");
    closeBtn.className = "nc-sync-close";
    closeBtn.textContent = "✕";
    closeBtn.addEventListener("click", () => overlay.remove());
    header.appendChild(closeBtn);

    // 内容
    const body = document.createElement("div");
    body.className = "nc-sync-body";
    body.innerHTML = `<p style="margin:0 0 16px;font-size:13px;color:var(--nca-fg-dim);">确认开通「${tier.name}」会员？<br>价格：${tier.price}（${tier.period}）<br>权益：${tier.perk}</p>`;

    // 按钮区
    const actions = document.createElement("div");
    actions.className = "nc-sync-actions";

    const cancelBtn = document.createElement("button");
    cancelBtn.className = "nc-sync-btn-cancel";
    cancelBtn.textContent = "取消";
    cancelBtn.addEventListener("click", () => overlay.remove());

    const confirmBtn = document.createElement("button");
    confirmBtn.className = "nc-sync-btn-confirm";
    confirmBtn.textContent = "确认购买";
    confirmBtn.addEventListener("click", async () => {
        overlay.remove();
        await _执行购买会员(tier, btn, token);
    });

    actions.appendChild(cancelBtn);
    actions.appendChild(confirmBtn);
    body.appendChild(actions);

    modal.appendChild(header);
    modal.appendChild(body);
    overlay.appendChild(modal);
    overlay.addEventListener("click", (e) => {
        if (e.target === overlay) overlay.remove();
    });
    // 找到侧边栏根容器，确保遮罩覆盖整个面板
    const overlayTarget = scope.closest(".nca-sidebar-root") || scope;
    overlayTarget.appendChild(overlay);
}

async function _执行购买会员(tier, btn, token) {
    btn.disabled = true;
    const originalText = btn.textContent;
    btn.textContent = "处理中…";

    const r = await _本地调用(API_MEMBERSHIP, {
        method: "POST",
        token,
        body: {
            token,
            account: _ncaState.account || "",
            tier: tier.key,
        },
    });

    if (r.ok && r.data && r.data.success === true) {
        // 更新本地会员状态
        _ncaState.tier_info = r.data.tier_info || { tier: tier.key, expire: "", api_available: true };
        // 更新余额
        if (r.data.nca_balance !== undefined && r.data.nca_balance !== null) {
            const tpc = (_ncaState.balance && _ncaState.balance.tokens_per_credit) || 200000;
            _ncaState.balance = { amount: r.data.nca_balance, currency: "token", tokens_per_credit: tpc };
            _ncaState.balanceAt = Date.now();
        }
        Toast.success(r.data.message || `成功开通${tier.name}会员`);
        // 整体重渲染，刷新会员状态卡与购买按钮
        _刷新登录面板();
        return;
    }

    // 失败分支
    const code = r.data && (r.data.code || r.data.error_code);
    const msg  = r.data && (r.data.error || r.data.detail || r.data.message);
    if (code === "INSUFFICIENT_BALANCE" || (msg && msg.indexOf("不足") >= 0)) {
        Toast.warning("积分不足，请先充值 RanKing 积分");
    } else if (code === "INVALID_TOKEN" || code === "TOKEN_EXPIRED") {
        Toast.error("登录态已失效，请重新登录");
    } else {
        Toast.error(msg || `开通失败 (${r.status || "网络"})`);
    }

    btn.disabled = false;
    btn.textContent = originalText;
}

// ══════ 充值功能 ══════
const API_RECHARGE = "/nca/billing/recharge";
const MIN_RECHARGE = 10;

// 向充值对话框插入会员权益提醒条（非进阶/高级会员且本会话未关闭）
function _插入会员提醒条(dialog) {
    const 当前等级 = _当前会员等级();
    const 已是高级会员 = 当前等级 === "pro" || 当前等级 === "premium";
    let dismissed = false;
    try { dismissed = sessionStorage.getItem(_MEMBER_REMINDER_KEY) === "1"; } catch (_) {}
    if (已是高级会员 || dismissed) return;

    const reminder = el("div", { class: "nc-member-reminder" });
    reminder.appendChild(el("span", { class: "nc-member-reminder-icon", text: "🎁" }));
    reminder.appendChild(el("span", {
        class: "nc-member-reminder-text",
        text: "购买进阶会员或高级会员可获赠大量 Token！",
    }));
    const close = el("button", {
        class: "nc-member-reminder-close",
        text: "×",
        title: "本次会话不再提示",
    });
    close.addEventListener("click", () => {
        try { sessionStorage.setItem(_MEMBER_REMINDER_KEY, "1"); } catch (_) {}
        reminder.remove();
    });
    reminder.appendChild(close);
    dialog.appendChild(reminder);
}

async function _显示充值对话框(scope) {
    // 移除已有对话框
    const existing = document.getElementById("nc-recharge-dialog");
    if (existing) existing.remove();

    const overlay = el("div", {
        id: "nc-recharge-dialog",
        class: "nc-sync-overlay",
    });

    const dialog = el("div", {
        class: "nc-sync-modal",
        style: {
            padding: "28px",
            width: "360px",
        },
    });

    // 标题
    dialog.appendChild(el("div", {
        style: { fontSize: "18px", fontWeight: "600", marginBottom: "6px" },
        text: "充值",
    }));
    dialog.appendChild(el("div", {
        style: { fontSize: "12px", color: "var(--nca-fg-dim, #a0a0b8)", marginBottom: "12px" },
        text: `从 RanKing 积分充值（1积分 = 1元 = ${((_ncaState.balance && _ncaState.balance.tokens_per_credit) || 200000).toLocaleString()} token）`,
    }));

    // 会员提醒条：非进阶/高级会员、且本会话未关闭时显示
    _插入会员提醒条(dialog);

    // 模型定价参考表
    const pricingBox = el("div", {
        style: {
            background: "var(--nca-bg-input, #2d2d3d)",
            borderRadius: "8px",
            padding: "10px 12px",
            marginBottom: "16px",
            fontSize: "11px",
            color: "var(--nca-fg-dim, #a0a0b8)",
        },
    });
    pricingBox.appendChild(el("div", {
        style: { fontWeight: "600", marginBottom: "6px", color: "var(--nca-fg, #e0e0f0)" },
        text: "模型定价参考（每百万 token）",
    }));
    const _定价行 = [
        { name: "DeepSeek-V4-Pro",   inP: "¥3.0",  outP: "¥6.0" },
        { name: "GLM-5.2 (智谱)",   inP: "¥5.0",  outP: "¥10.0" },
        { name: "DeepSeek-Chat (经济)", inP: "¥0.5",  outP: "¥1.0" },
    ];
    for (const 行 of _定价行) {
        const r = el("div", {
            style: { display: "flex", justifyContent: "space-between", padding: "2px 0" },
        });
        r.appendChild(el("span", { text: 行.name }));
        r.appendChild(el("span", {
            style: { color: "var(--nca-fg-dim, #a0a0b8)" },
            text: `输入 ${行.inP} / 输出 ${行.outP}`,
        }));
        pricingBox.appendChild(r);
    }
    pricingBox.appendChild(el("div", {
        style: { marginTop: "6px", fontSize: "10px", color: "var(--nca-accent, #6c5ce7)" },
        text: "※ 带蓝色「免费」徽章的模型不扣费，基础会员可用",
    }));
    dialog.appendChild(pricingBox);

    // 充值建议（基于当前余额）
    const curBalance = (_ncaState.balance && _ncaState.balance.amount) || 0;
    const curTpc = (_ncaState.balance && _ncaState.balance.tokens_per_credit) || 200000;
    const curCredits = curBalance / curTpc;
    let suggestedAmount = 10;
    if (curCredits < 0.5) suggestedAmount = 50;
    else if (curCredits < 1) suggestedAmount = 20;
    const suggestEl = el("div", {
        style: {
            fontSize: "11px",
            color: "var(--nca-fg-dim, #a0a0b8)",
            marginBottom: "6px",
        },
        text: curCredits < 1 ? `当前余额较低（≈ ${curCredits.toFixed(2)} 积分），建议充值 ${suggestedAmount} 元` : `当前余额 ≈ ${curCredits.toFixed(2)} 积分`,
    });
    dialog.appendChild(suggestEl);

    // 金额输入
    const amountInput = el("input", {
        type: "number",
        min: String(MIN_RECHARGE),
        step: "1",
        placeholder: `最少 ${MIN_RECHARGE} 元`,
        style: {
            width: "100%", padding: "12px 16px",
            background: "var(--nca-bg-input, #2d2d3d)",
            border: "1px solid var(--nca-border, #3f3f5a)",
            borderRadius: "8px",
            color: "#fff", fontSize: "16px",
            outline: "none", boxSizing: "border-box",
        },
    });
    dialog.appendChild(el("div", {
        style: { fontSize: "12px", color: "var(--nca-fg-dim, #a0a0b8)", marginBottom: "6px" },
        text: "充值金额",
    }));
    dialog.appendChild(amountInput);

    // 快捷金额
    const quickRow = el("div", {
        style: { display: "flex", gap: "8px", marginTop: "10px" },
    });
    for (const amt of [10, 50, 100, 200]) {
        const btn = el("button", {
            style: {
                flex: "1", padding: "8px 0",
                background: "var(--nca-bg-input, #2d2d3d)",
                border: "1px solid var(--nca-border, #3f3f5a)",
                borderRadius: "6px", color: "#fff",
                fontSize: "13px", cursor: "pointer",
            },
            text: `${amt}`,
        });
        btn.addEventListener("click", () => {
            amountInput.value = amt;
            confirmBtn.textContent = `确认充值 ${amt} 元`;
        });
        quickRow.appendChild(btn);
    }
    dialog.appendChild(quickRow);

    // 确认按钮
    let confirmBtn = el("button", {
        style: {
            width: "100%", marginTop: "20px",
            padding: "12px", background: "var(--nca-accent, #6366f1)",
            color: "#fff", border: "none",
            borderRadius: "8px", fontSize: "14px",
            fontWeight: "600", cursor: "pointer",
        },
        text: "确认充值",
    });

    // 输入时更新按钮文字
    amountInput.addEventListener("input", () => {
        const val = parseFloat(amountInput.value);
        if (val > 0) {
            confirmBtn.textContent = `确认充值 ${val} 元`;
        } else {
            confirmBtn.textContent = "确认充值";
        }
    });

    // 确认充值
    confirmBtn.addEventListener("click", async () => {
        const amount = parseFloat(amountInput.value);
        if (isNaN(amount) || amount < MIN_RECHARGE) {
            Toast.error(`最小充值额度为 ${MIN_RECHARGE} 元`);
            return;
        }

        const token = _ncaState.token;
        if (!token) {
            Toast.error("登录态已失效，请重新登录");
            overlay.remove();
            return;
        }

        const confirmed = confirm(`确认充值 ${amount} 元？\n将从 RanKing 扣除 ${amount} 积分。`);
        if (!confirmed) return;

        confirmBtn.disabled = true;
        confirmBtn.textContent = "处理中…";

        try {
            const r = await _本地调用(API_RECHARGE, {
                method: "POST",
                token,
                body: { token, amount, account: _ncaState.account || "" },
            });

            if (r.ok && r.data && r.data.success === true) {
                Toast.success(`充值成功！${amount} 元已到账`);
                if (r.data.warning) {
                    Toast.warning(r.data.warning);
                }
                // 直接用充值响应中的余额更新显示（后端已返回 NCA token 余额）
                if (r.data.balance !== undefined && r.data.balance !== null) {
                    const tpc = (_ncaState.balance && _ncaState.balance.tokens_per_credit) || 200000;
                    _ncaState.balance = {
                        amount: r.data.balance,
                        currency: "token",
                        tokens_per_credit: tpc,
                    };
                    _ncaState.balanceAt = Date.now();
                }
                overlay.remove();
                // 先渲染内联余额，再强制从后端刷新确保显示准确
                _渲染余额(scope);
                _刷新余额(scope, true).catch(() => {});
            } else {
                const code = r.data && (r.data.code || r.data.error_code);
                const msg = r.data && (r.data.error || r.data.detail || r.data.message);
                const refId = r.data && r.data.reference_id;
                if (code === "INSUFFICIENT_BALANCE") {
                    Toast.error("RanKing 积分不足，请先在 RanKing 充值");
                } else if (refId) {
                    Toast.error(`${msg}\n交易单号: ${refId}`);
                } else {
                    Toast.error(msg || `充值失败 (${r.status || "网络错误"})`);
                }
            }
        } catch (e) {
            Toast.error(`充值失败: ${e.message}`);
        }

        confirmBtn.disabled = false;
        confirmBtn.textContent = "确认充值";
    });

    dialog.appendChild(confirmBtn);

    // 取消按钮
    const cancelBtn = el("button", {
        style: {
            width: "100%", marginTop: "10px",
            padding: "10px", background: "transparent",
            color: "var(--nca-fg-dim, #a0a0b8)",
            border: "1px solid var(--nca-border, #3f3f5a)",
            borderRadius: "8px", fontSize: "13px",
            cursor: "pointer",
        },
        text: "取消",
    });
    cancelBtn.addEventListener("click", () => overlay.remove());
    dialog.appendChild(cancelBtn);

    // 点击遮罩关闭
    overlay.addEventListener("click", (e) => {
        if (e.target === overlay) overlay.remove();
    });

    overlay.appendChild(dialog);
    // 找到侧边栏根容器，确保遮罩覆盖整个面板
    const overlayTarget = scope.closest(".nca-sidebar-root") || scope;
    overlayTarget.appendChild(overlay);
    amountInput.focus();
}

// ══════ 会员权限不足升级提示 ══════
// 在登录面板顶部插入/更新一条醒目的橙红色错误提示条
function _显示升级提示条(scope, msg) {
    if (!scope) return;
    let bar = scope.querySelector(".nc-upgrade-alert");
    if (!bar) {
        bar = el("div", { class: "nc-upgrade-alert" });
        bar.style.cssText = "margin:0 0 12px;padding:12px 14px;border-radius:10px;"
            + "background:linear-gradient(90deg,#f59e0b,#ef4444);color:#fff;"
            + "font-size:13px;font-weight:600;line-height:1.5;"
            + "box-shadow:0 6px 18px rgba(239,68,68,.35);";
        scope.insertBefore(bar, scope.firstChild);
    }
    bar.textContent = "\u26a0 " + msg;
}

/**
 * 会员权限不足（收到 403）时调用：
 *   1. 若登录面板 overlay 未打开，则触发侧边栏账号按钮打开；
 *   2. 在面板顶部显示一条橙红色错误提示（error_msg）；
 *   3. 自动滚动到会员购买区域。
 * @param {string} errorMsg 服务器返回的错误信息
 */
export function 显示会员升级提示(errorMsg) {
    const msg = errorMsg || "当前会员无法使用 API 模型，请升级会员后使用";

    // 1) 登录面板未打开时，触发侧边栏账号按钮（星星 ✦）打开 overlay
    if (!document.querySelector(".nca-login-entry-overlay")) {
        const 账号按钮 = document.querySelector(".nca-icon-btn[title='账号']")
            || document.querySelector("[title='账号']");
        if (账号按钮) {
            try { 账号按钮.click(); } catch (_) {}
        }
    }

    // 2) 等待面板渲染完成后展示提示并滚动到会员区域（最多重试约 1.5s）
    let 尝试 = 0;
    const 定时 = setInterval(() => {
        尝试++;
        const scope = _profileRoot
            || document.querySelector(".nca-login-entry-overlay .nc-ranking-profile");
        const 会员区 = scope && scope.querySelector(".nc-membership-section");
        if (scope) {
            _显示升级提示条(scope, msg);
        }
        if (会员区) {
            clearInterval(定时);
            try { 会员区.scrollIntoView({ behavior: "smooth", block: "center" }); } catch (_) {}
        } else if (尝试 >= 15) {
            clearInterval(定时);
        }
    }, 100);
}
