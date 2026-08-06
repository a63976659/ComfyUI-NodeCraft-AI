// ═══════════════════════════════════════════════════════════════
// 登录面板.js — RanKing 一键登录（Neo-Noir Luxury Terminal）
// NodeCraft AI - 节点梦工厂
//
// 【本地 API Key 直连改造后】：
//   · RanKing 登录只做身份展示（头像/昵称/邮箱），不再关联业务权限
//   · 移除充值 / 会员 / 用量 / 余额 相关 UI 与端点调用
//   · 不再远程校验 token，直接信任 RanKing 存储的凭证
//
// 导出：
//   - 创建登录面板(container, { onEnter })
//   - 获取登录状态()  → { loggedIn, username, displayName, avatar, email, ... }
//   - 获取Token()     → token | null
//   - 登出()          → 清除内存缓存
//   - 自动登录()      → Promise<boolean>
// ═══════════════════════════════════════════════════════════════

import { el, Toast } from "./工具函数.js";
import { t } from "./i18n.js";

// ─── RanKing 凭证存储键 ────────────────────────────────
const RK_TOKEN_KEY = "ComfyCommunity_Token";
const RK_USER_KEY  = "ComfyCommunity_User";

// ─── NCA 内存缓存 ────────────────────────────────────────
const _ncaState = {
    token:      null,
    user:       null,
    account:    null,
    verifiedAt: 0,
};

// 当前登录面板挂载容器与入口回调
let _currentHost = null;
let _currentOnEnter = null;


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
            user = parsed.user || parsed;
        } catch (_) {
            user = { name: userRaw };
        }
    }
    return { token, user };
}

// ─── 本地验证凭证（无网络调用）──────────────────────
function _本地验证(token, user) {
    if (!token) return { ok: false, reason: "NO_TOKEN" };
    if (!user) return { ok: false, reason: "NO_USER" };
    return {
        ok: true,
        data: {
            valid: true,
            user,
            account: user.account || user.id || user.name || "",
        },
    };
}

// ─── 公共导出：状态读写 ────────────────────────────
export function 获取Token() {
    if (_ncaState.token) return _ncaState.token;
    const { token } = _读取RanKing凭证();
    if (token) _ncaState.token = token;
    return _ncaState.token || null;
}

export function 获取登录状态() {
    const token = _ncaState.token;
    const user  = _ncaState.user;
    if (!token || !user) {
        return { loggedIn: false, username: "" };
    }
    const username = user.name || user.username || user.nickname || user.email || "用户";
    const nickname = user.nickname || user.name || "";
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
        source:      "ranking",
    };
}

export function 登出() {
    _ncaState.token      = null;
    _ncaState.user       = null;
    _ncaState.account    = null;
    _ncaState.verifiedAt = 0;
}

// ─── 登录响应处理 ──────────────────────────────────
function _处理登录响应(data, token, user) {
    _ncaState.token      = token;
    _ncaState.user       = data.user || user || { name: "RanKing 用户" };
    _ncaState.account    = data.account || (user && user.account) || "";
    _ncaState.verifiedAt = Date.now();
}

// ─── 自动登录 ─────────────────────────────────
export async function 自动登录() {
    if (_ncaState.token && _ncaState.user) return true;

    const { token, user } = _读取RanKing凭证();
    if (!token) return false;

    const r = _本地验证(token, user);
    if (r.ok) {
        _处理登录响应(r.data, token, user);
        return true;
    }
    return false;
}

// ─── UI 主入口 ────────────────────────────────────
export function 创建登录面板(container, opts) {
    const onEnter = opts && typeof opts.onEnter === 'function' ? opts.onEnter : null;
    container.classList.add("nc-login-root");
    container.innerHTML = "";

    _currentHost = container;
    _currentOnEnter = onEnter;

    if (_ncaState.token && _ncaState.user) {
        container.appendChild(_渲染Profile(container, onEnter));
    } else {
        container.appendChild(_渲染登录提示(container, onEnter));
    }
    return container;
}

// ─── 未登录视图 ─────────────────────────────────
function _渲染登录提示(host, onEnter) {
    const root = el("div", { class: "nc-login-card nc-ranking-card" });

    const header = el("div", { class: "nc-login-header" });
    header.appendChild(el("div", { class: "nc-login-brand", text: "RanKing × NodeCraft AI" }));
    header.appendChild(el("div", {
        class: "nc-login-subtitle",
        text: "// single-sign-on · ranking-account · zero-friction",
    }));
    root.appendChild(header);

    const iconBox = el("div", { class: "nc-ranking-icon" });
    iconBox.textContent = "🔑";
    root.appendChild(iconBox);

    root.appendChild(el("div", { class: "nc-ranking-title", text: t("login.title") }));
    root.appendChild(el("div", {
        class: "nc-ranking-desc",
        text: t("login.desc"),
    }));

    // 凭证检测提示
    const { token: rkToken, user: rkUser } = _读取RanKing凭证();
    const status = el("div", { class: "nc-ranking-status" });
    const dot = el("span", { class: "nc-ranking-status-dot" });
    status.appendChild(dot);
    const statusText = el("span", { class: "nc-ranking-status-text" });
    if (rkToken) {
        status.classList.add("ok");
        const userHint = (rkUser && (rkUser.name || rkUser.nickname || rkUser.username)) || t("login.ready");
        statusText.textContent = t("login.detected", { user: userHint });
    } else {
        status.classList.add("warn");
        statusText.textContent = t("login.not_detected");
    }
    status.appendChild(statusText);
    root.appendChild(status);

    const errBox = el("div", { class: "nc-login-error" });
    root.appendChild(errBox);

    const btn = el("button", {
        class: "nc-login-btn nc-login-btn-primary nc-ranking-btn",
        text: t("login.button"),
    });
    const _恢复按钮 = () => {
        btn.disabled = false;
        btn.textContent = t("login.button");
    };
    btn.addEventListener("click", async () => {
        _设错误(root, "");
        const { token, user } = _读取RanKing凭证();
        if (!token) {
            _设错误(root, t("login.need_ranking"));
            return;
        }
        btn.disabled = true;
        btn.textContent = t("login.verifying");

        const r = _本地验证(token, user);
        if (r.ok) {
            _处理登录响应(r.data, token, user);
            Toast.success(t("login.success"));
            创建登录面板(host, { onEnter: onEnter });
            return;
        }

        _恢复按钮();
        _设错误(root, r.reason === "NO_TOKEN"
            ? t("login.need_ranking")
            : t("login.read_fail"));
    });
    root.appendChild(btn);

    const hint = el("div", { class: "nc-ranking-hint" });
    hint.textContent = rkToken
        ? t("login.hint_secure")
        : t("login.hint_go_ranking");
    root.appendChild(hint);

    return root;
}

// ─── 已登录视图（欢迎回来身份卡：头像/认证角标/昵称/邮箱/进入） ────────────────
function _渲染Profile(host, onEnter) {
    const state = 获取登录状态();

    const root = el("div", { class: "nc-login-profile nc-ranking-profile" });

    // 顶部小标：WELCOME BACK
    root.appendChild(el("div", { class: "nc-profile-eyebrow", text: "WELCOME BACK" }));

    // 头像（外发光环 + 右下角认证对勾）
    const avatarWrap = el("div", { class: "nc-profile-avatar-wrap" });
    const avatar = el("div", { class: "nc-login-avatar" });
    if (state.avatar) {
        const img = document.createElement("img");
        img.src = state.avatar;
        img.alt = state.displayName || "avatar";
        img.className = "nc-avatar-img";
        avatar.appendChild(img);
    } else {
        avatar.textContent = (state.displayName || "U").charAt(0).toUpperCase();
    }
    avatarWrap.appendChild(avatar);
    avatarWrap.appendChild(el("span", { class: "nc-profile-verified", text: "✓" }));
    root.appendChild(avatarWrap);

    // 昵称
    root.appendChild(el("div", {
        class: "nc-profile-name",
        text: state.displayName || "RanKing 用户",
    }));

    // 邮箱（若有）
    if (state.email) {
        root.appendChild(el("div", {
            class: "nc-profile-email",
            text: state.email,
        }));
    }

    // 状态胶囊：RanKing 账号已连接
    const pill = el("div", { class: "nc-profile-pill" });
    pill.appendChild(el("span", { class: "nc-profile-pill-dot" }));
    pill.appendChild(el("span", { class: "nc-profile-pill-text", text: t("login.connected") }));
    root.appendChild(pill);

    // 进入按钮（醒目主按钮）
    if (typeof onEnter === "function") {
        const enterBtn = el("button", {
            class: "nc-login-btn nc-login-btn-primary nc-profile-enter",
            text: t("login.enter"),
        });
        enterBtn.addEventListener("click", () => {
            try { onEnter(); } catch (_) {}
        });
        root.appendChild(enterBtn);
    }

    // 退出登录（低调文本链接）
    const logoutBtn = el("button", {
        class: "nc-logout-small",
        text: t("login.logout"),
    });
    logoutBtn.addEventListener("click", () => {
        登出();
        Toast.success(t("login.logged_out"));
        创建登录面板(host, { onEnter });
    });
    root.appendChild(logoutBtn);

    return root;
}

// ─── 错误提示工具 ─────────────────────────────────
function _设错误(root, msg) {
    const errBox = root.querySelector(".nc-login-error");
    if (!errBox) return;
    errBox.textContent = msg || "";
    errBox.style.display = msg ? "block" : "none";
}
