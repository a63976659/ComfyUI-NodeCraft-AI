// ═══════════════════════════════════════════════════════════════
// 流式聊天管理器.js — SSE 流式聊天公共逻辑
// NodeCraft AI — Luxury Terminal / Neo-Noir Hacker
// ═══════════════════════════════════════════════════════════════

import { 简易Markdown渲染, NCA_API_BASE } from "./工具函数.js";
import { 是可重连错误, 设置余额缓存 } from "./交互与状态.js";
import { 绑定代码块复制按钮 } from "./消息渲染器.js";
import { Toast } from "./工具函数.js";

/**
 * SSE 流式聊天管理器
 *
 * 封装 SSE 连接、重试、流式渲染等公共逻辑，
 * 供 消息渲染器 / 优化面板 / 可视化面板 等共用。
 *
 * @param {Object} options
 * @param {HTMLElement} options.消息容器 - AI 回复气泡元素（用于追加工具指示器等）
 * @param {HTMLElement} options.消息体   - 消息内容渲染容器（innerHTML 会被实时更新）
 * @param {HTMLElement} options.滚动容器 - 滚动目标元素（每次更新后自动滚动到底部）
 * @param {Object}      options.请求体   - POST 请求 body（直接 JSON 序列化）
 * @param {Function}    [options.数据处理] - 自定义原始事件处理 (data, 状态) => 'skip'|undefined
 *   返回 'skip' 表示已处理该事件、跳过默认内容累加；状态.el 可用于跨 chunk 保持 DOM 引用
 * @param {Function}    [options.完成回调] - 流式结束后调用 ({ fullContent, hasError, isAbort, 连接中断 }) => void
 *   若提供则由调用方自行渲染；若不提供则使用默认 Markdown 渲染
 * @param {AbortSignal} [options.中止信号] - AbortController signal
 * @returns {Promise<{fullContent: string, hasError: boolean, isAbort: boolean, 连接中断: boolean}>}
 */
export async function 创建流式聊天(options) {
    const {
        消息容器, 消息体, 滚动容器,
        请求体,
        数据处理 = null,
        完成回调 = null,
        中止信号 = null,
    } = options;

    let fullContent = '';
    let buffer = '';
    let hasError = false;
    let 重试次数 = 0;
    const 最大重试 = 3;
    const 重试开始时间 = Date.now();
    let 需要重试 = false;
    let isAbort = false;
    let 连接中断 = false;

    // 跨 chunk 保持的状态引用（供 数据处理 回调通过 状态.el 读写）
    const 状态 = { el: null };

    // ── 辅助：滚动到底部 ──
    const 滚动 = () => {
        if (滚动容器) {
            requestAnimationFrame(() => {
                滚动容器.scrollTop = 滚动容器.scrollHeight;
            });
        }
    };

    SSE重试: do {
        需要重试 = false;
        try {
            const response = await fetch(`${NCA_API_BASE}/chat-stream`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(请求体),
                signal: 中止信号 || undefined,
            });

            if (!response.ok) {
                let errorMsg = `HTTP ${response.status}: ${response.statusText}`;
                let errData = {};
                try {
                    errData = await response.json();
                    // errData.error 可能是布尔值 true（非字符串），须优先取 message
                    if (typeof errData.error === "string") errorMsg = errData.error;
                    else if (errData.message) errorMsg = errData.message;
                } catch (_) {}
                const err = new Error(errorMsg);
                err.status = response.status;
                err.code = errData.code || null;
                err.data = errData;
                throw err;
            }

            const reader = response.body.getReader();
            const decoder = new TextDecoder();

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;

                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split('\n');
                buffer = lines.pop(); // 保留不完整的行

                for (const line of lines) {
                    if (!line.startsWith('data: ')) continue;
                    try {
                        const data = JSON.parse(line.slice(6));

                        // 自定义数据处理（如 context_health / tool_executing）
                        if (数据处理) {
                            const result = 数据处理(data, 状态);
                            if (result === 'skip') continue;
                        }

                        // ── 规划事件处理 ──
                        if (data.type === 'planning_start') {
                            状态.planning = true;
                            continue;
                        }
                        if (data.type === 'planning_result') {
                            状态.planResult = data;
                            continue;
                        }
                        if (data.type === 'planning_done' && data.needs_confirmation) {
                            // 规划需要确认 → 触发完成回调返回规划数据，提前结束流
                            const 结果 = {
                                fullContent: fullContent || "",
                                hasError: false,
                                isAbort: false,
                                连接中断: false,
                                type: "planning",
                                planResult: 状态.planResult
                            };
                            if (完成回调) {
                                完成回调(结果);
                            }
                            return 结果;
                        }

                        if (data.type === 'billing') {
                            状态.billing = { cost: data.cost, balance: data.balance, tokens: data.tokens };
                            设置余额缓存(data.balance);
                            continue; // 记录计费信息后继续处理后续事件（含 done），切勿 return 退出整个函数
                        }
                        if (data.error) {
                            hasError = true;
                            if (data.code === 'BALANCE_EXHAUSTED') {
                                fullContent += "\n\n⚠ 额度已耗尽，请充值后继续使用";
                                try { Toast.warning("额度已耗尽，请充值后继续使用"); } catch (_) {}
                            } else {
                                fullContent += `\n⚠ 错误: ${data.error}`;
                            }
                            break;
                        }
                        if (data.done) break;

                        if (data.content) {
                            fullContent += data.content;
                            消息体.innerHTML = 简易Markdown渲染(fullContent) + '<span class="nca-streaming-cursor"></span>';
                            滚动();
                        }
                    } catch (parseErr) {
                        console.warn("[节点梦工厂] SSE 解析警告:", parseErr.message);
                    }
                }
                if (hasError) break;
            }
        } catch (e) {
            if (e?.code === 'BALANCE_EXHAUSTED' || e?.data?.code === 'BALANCE_EXHAUSTED') {
                hasError = true;
                fullContent += "\n\n⚠ 额度已耗尽，请充值后继续使用";
                try { Toast.warning("额度已耗尽，请充值后继续使用"); } catch (_) {}
                break SSE重试;
            }
            // 会员权限不足（403）：弹出登录面板并提示升级会员
            if (e?.status === 403) {
                hasError = true;
                const 提示 = (e?.data && e.data.message) || e?.message || "当前会员无法使用 API 模型，请升级会员后使用";
                fullContent += `\n\n⚠ ${提示}`;
                try { Toast.error(提示); } catch (_) {}
                try {
                    import("./登录面板.js").then(m => {
                        if (m && typeof m.显示会员升级提示 === "function") m.显示会员升级提示(提示);
                    }).catch(() => {});
                } catch (_) {}
                break SSE重试;
            }
            if (e.name === 'AbortError') {
                isAbort = true;
                fullContent += "\n\n_（生成已停止）_";
                break SSE重试;
            }

            // 可重连错误且未收到任何内容：自动重试（指数退避，最大 3 次，10 秒时间窗口）
            if (是可重连错误(e) && !fullContent && 重试次数 < 最大重试 && (Date.now() - 重试开始时间) < 10000) {
                try { Toast.warning(`连接中断，正在重试(${重试次数 + 1}/${最大重试})...`); } catch (_) {}
                await new Promise(r => setTimeout(r, 1000 * Math.pow(2, 重试次数)));
                重试次数++;
                需要重试 = true;
                // 重置状态以便重试
                buffer = '';
                hasError = false;
                状态.el = null;
                消息体.innerHTML = '<span class="nca-streaming-cursor"></span>';
                continue SSE重试;
            }

            // 已收到部分内容：标记连接中断，后续添加重试按钮
            if (fullContent) {
                连接中断 = true;
                try { Toast.warning("连接中断，已生成内容已保留"); } catch (_) {}
            } else {
                hasError = true;
                // 重试耗尽或不可重连的网络错误
                if (重试次数 > 0) {
                    try { Toast.error("连接多次失败，请检查网络后重试"); } catch (_) {}
                } else if (e instanceof TypeError || (e.message && (e.message.includes("Failed to fetch") || e.message.includes("NetworkError")))) {
                    try { Toast.error("网络连接失败，请检查网络"); } catch (_) {}
                }
            }
        }
    } while (需要重试);

    // ── 完成后渲染 ──
    const 结果 = { fullContent: fullContent || "（无回复）", hasError, isAbort, 连接中断, billing: 状态.billing };

    if (完成回调) {
        完成回调(结果);
    } else {
        // 默认渲染
        消息体.innerHTML = 简易Markdown渲染(结果.fullContent);
        // DOMPurify 未就绪时标记并缓存原始内容，待加载完成后重新渲染
        if (!window.DOMPurify && 消息容器) {
            消息体.dataset.pendingSanitize = "true";
            消息容器._pendingContent = 结果.fullContent;
        }
        if (消息容器) 绑定代码块复制按钮(消息容器);
    }

    // 连接中断且有部分内容：添加“重试”按钮（全量重试，保留已有内容）
    if (连接中断 && fullContent && !isAbort && 消息容器) {
        const 重试按钮 = document.createElement('button');
        重试按钮.className = 'nca-retry-btn';
        重试按钮.textContent = '重试';
        重试按钮.style.cssText = 'display:inline-block;margin-top:8px;padding:4px 16px;border:1px solid var(--nca-accent,#5b9fff);border-radius:6px;background:transparent;color:var(--nca-accent,#5b9fff);cursor:pointer;font-size:13px;transition:all .2s;';
        重试按钮.addEventListener('mouseenter', () => {
            重试按钮.style.background = 'var(--nca-accent,#5b9fff)';
            重试按钮.style.color = '#fff';
        });
        重试按钮.addEventListener('mouseleave', () => {
            重试按钮.style.background = 'transparent';
            重试按钮.style.color = 'var(--nca-accent,#5b9fff)';
        });
        重试按钮.addEventListener('click', async () => {
            const 备份HTML = 消息体.innerHTML;
            重试按钮.remove();
            try {
                const 重试结果 = await 创建流式聊天(options);
                const 成功 = !重试结果.hasError && !重试结果.isAbort && !重试结果.连接中断
                    && 重试结果.fullContent && 重试结果.fullContent !== '（无回复）';
                if (!成功) {
                    // 重试失败：恢复原内容并重新添加重试按钮
                    消息体.innerHTML = 备份HTML;
                    if (消息容器) 绑定代码块复制按钮(消息容器);
                    消息容器.querySelectorAll('.nca-retry-btn').forEach(b => b.remove());
                    重试按钮.disabled = false;
                    重试按钮.textContent = '重试';
                    消息容器.appendChild(重试按钮);
                }
                // 重试成功：内容已由创建流式聊天渲染，无需额外处理
            } catch (e) {
                消息体.innerHTML = 备份HTML;
                if (消息容器) 绑定代码块复制按钮(消息容器);
                消息容器.querySelectorAll('.nca-retry-btn').forEach(b => b.remove());
                重试按钮.disabled = false;
                重试按钮.textContent = '重试';
                消息容器.appendChild(重试按钮);
            }
        });
        消息容器.appendChild(重试按钮);
    }

    滚动();
    return 结果;
}
