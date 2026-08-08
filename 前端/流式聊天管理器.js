// ═══════════════════════════════════════════════════════════════
// 流式聊天管理器.js — SSE 流式聊天公共逻辑
// NodeCraft AI — Luxury Terminal / Neo-Noir Hacker
// ═══════════════════════════════════════════════════════════════

import { 简易Markdown渲染, NCA_API_BASE } from "./工具函数.js";
import { 是可重连错误 } from "./交互与状态.js";
import { 绑定代码块复制按钮 } from "./消息渲染器.js";
import { Toast } from "./工具函数.js";
import { t } from "./i18n.js";

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
 * @param {Function}    [options.获取消息容器] - 动态获取当前消息容器 DOM（用于界面重建后恢复）
 * @param {Function}    [options.获取消息体]   - 动态获取当前消息体 DOM（用于界面重建后恢复）
 * @param {Function}    [options.获取滚动容器] - 动态获取当前滚动容器 DOM（用于界面重建后恢复）
 * @param {Object}      options.请求体   - POST 请求 body（直接 JSON 序列化）
 * @param {Function}    [options.数据处理] - 自定义原始事件处理 (data, 状态) => 'skip'|undefined
 *   返回 'skip' 表示已处理该事件、跳过默认内容累加；状态.el 可用于跨 chunk 保持 DOM 引用
 * @param {Function}    [options.完成回调] - 流式结束后调用 ({ fullContent, hasError, isAbort, 连接中断 }) => void
 *   若提供则由调用方自行渲染；若不提供则使用默认 Markdown 渲染
 * @param {Function}    [options.内容更新回调] - 每次内容更新后调用 (fullContent) => void，供外部追踪累积内容
 * @param {AbortSignal} [options.中止信号] - AbortController signal
 * @returns {Promise<{fullContent: string, hasError: boolean, isAbort: boolean, 连接中断: boolean}>}
 */
export async function 创建流式聊天(options) {
    const {
        消息容器, 消息体, 滚动容器,
        获取消息容器, 获取消息体, 获取滚动容器,
        请求体,
        数据处理 = null,
        完成回调 = null,
        内容更新回调 = null,
        中止信号 = null,
    } = options;

    // 辅助：获取当前有效的 DOM 引用（优先使用 getter，回退到初始引用）
    const _当前消息体 = () => (获取消息体 ? 获取消息体() : 消息体);
    const _当前消息容器 = () => (获取消息容器 ? 获取消息容器() : 消息容器);
    const _当前滚动容器 = () => (获取滚动容器 ? 获取滚动容器() : 滚动容器);

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

    // ── 批量渲染缓冲 ──
    let _batchTimer = null;
    let _首块已渲染 = false; // 首个 chunk 零缓冲立即渲染，降低首字可见延迟
    const _BATCH_INTERVAL = 50; // 50ms 批渲染窗口（首块之后的后续 chunk）

    // ── 推理进度状态行（后端 inference_start / thinking 心跳事件）──
    // 老后端不发送这些事件时完全不生效，行为与既往一致
    let _推理状态行 = null;   // 状态行 DOM（插入消息体，首个内容 chunk 渲染时被整体替换）
    let _推理定时器 = null;   // 本地 1s 平滑计时器（后端心跳事件用于校准）
    let _推理等待秒 = 0;
    let _推理模型名 = '';

    const _渲染推理状态 = () => {
        if (!_推理状态行) return;
        const 文本 = _推理等待秒 > 0
            ? `模型推理中 · 已等待 ${_推理等待秒}s`
            : (_推理模型名 ? `模型推理中 · ${_推理模型名}` : '模型推理中');
        // 模型名来自服务端，用 textContent 插入避免 HTML 注入
        _推理状态行.innerHTML = '<span class="nca-inference-spin">⟳</span> ';
        _推理状态行.appendChild(document.createTextNode(文本));
    };

    const _清理推理状态 = () => {
        if (_推理定时器) { clearInterval(_推理定时器); _推理定时器 = null; }
        if (_推理状态行) { _推理状态行.remove(); _推理状态行 = null; }
    };

    const _显示推理状态 = (模型名) => {
        const body = _当前消息体();
        if (!body) return;
        _清理推理状态(); // 防重复（如重连后再次收到 inference_start）
        _推理模型名 = 模型名 || '';
        _推理等待秒 = 0;
        _推理状态行 = document.createElement('div');
        _推理状态行.className = 'nca-inference-status';
        body.appendChild(_推理状态行);
        _渲染推理状态();
        // 本地 1s 定时器平滑累加显示，后端 3s 心跳仅用于校准
        _推理定时器 = setInterval(() => {
            _推理等待秒++;
            _渲染推理状态();
        }, 1000);
    };

    // ── 增量流式渲染：稳定前缀缓存（方案A）──
    // 长回复流式期间避免每个批渲染窗口都对全文重新解析 Markdown：将已完结的安全前缀
    // （空行分隔、代码围栏闭合、think 标签配平）的渲染结果缓存，每帧只重渲染尾部未完结
    // 片段。done 后的完成渲染仍走一次全量渲染，纠正任何增量拼接误差。
    const _增量启用阈值 = 2048; // 内容超过该长度才启用前缀缓存（短内容全量渲染开销可忽略）
    const _尾部保留 = 600;      // 边界与内容末尾保持的最小距离，降低跨块结构的视觉误差
    const _渲染签名 = () => `${!!window.marked}|${!!window.DOMPurify}`;
    const _新增量状态 = () => ({
        边界: 0,            // 已提交前缀的末尾位置（缓存HTML 对应 [0, 边界)）
        缓存HTML: "",       // 已提交前缀的渲染结果
        签名: _渲染签名(),  // marked/DOMPurify 异步加载会改变渲染输出，签名变化时缓存作废
        扫描位置: 0,        // 逐行扫描进度（只扫描新增内容，整体 O(n)）
        围栏开: false,      // 扫描位置处代码围栏是否未闭合
        think深度: 0,       // 扫描位置处未闭合 <think> 标签深度
        待定边界: 0,        // 已遇空行、待下一非空行确认的候选边界
        候选边界: 0,        // 最近确认的安全边界（可能因 _尾部保留 尚未提交）
        需整体重建: false,  // 扫描中遇到孤立 </think>（渲染器会向前折叠全部内容），提交时须整体重渲染
    });
    let _增量 = _新增量状态();

    // 逐行推进扫描：寻找「空行 + 围栏闭合 + think 配平」的安全分块边界
    const _推进扫描 = () => {
        const inc = _增量;
        while (true) {
            const 行尾 = fullContent.indexOf("\n", inc.扫描位置);
            if (行尾 === -1) break; // 末行不完整，等待后续 chunk
            const line = fullContent.slice(inc.扫描位置, 行尾);
            if (/^ {0,3}(```|~~~)/.test(line)) {
                // 代码围栏开/闭切换；未闭合围栏使尾部块按代码块渲染（与全量渲染语义一致）
                inc.待定边界 = 0;
                inc.围栏开 = !inc.围栏开;
            } else if (!inc.围栏开) {
                if (line.trim() === "") {
                    if (inc.think深度 === 0) inc.待定边界 = 行尾 + 1;
                } else {
                    // 空行后的首个非空行若是列表项/缩进延续，不作分块点
                    // （避免松散列表被拆成多个列表、有序列表重新编号）
                    if (inc.待定边界 && !/^\s*(?:[-*+]|\d+[.)])\s/.test(line) && !/^\s{4,}/.test(line)) {
                        inc.候选边界 = inc.待定边界;
                    }
                    inc.待定边界 = 0;
                    const 开数 = (line.match(/<(think|thinking)>/gi) || []).length;
                    const 闭数 = (line.match(/<\/(think|thinking)>/gi) || []).length;
                    inc.think深度 += 开数 - 闭数;
                    if (inc.think深度 < 0) {
                        // 孤立 </think>：简易Markdown渲染 会将其前的全部内容折叠为思考块，
                        // 分段缓存的前缀失效，须在下次提交时整体重渲染
                        inc.think深度 = 0;
                        inc.候选边界 = 0;
                        inc.需整体重建 = true;
                    }
                }
            }
            inc.扫描位置 = 行尾 + 1;
        }
    };

    const _更新增量缓存 = () => {
        if (_增量.签名 !== _渲染签名()) _增量 = _新增量状态(); // 渲染库加载完成 → 输出格式变化，重建
        _推进扫描();
        const inc = _增量;
        const 上限 = fullContent.length - _尾部保留;
        if (inc.候选边界 > inc.边界 && inc.候选边界 <= 上限) {
            if (inc.需整体重建) {
                // 单次全量调用渲染整个前缀，保证孤立 </think> 的向前折叠语义正确
                inc.缓存HTML = 简易Markdown渲染(fullContent.slice(0, inc.候选边界));
                inc.需整体重建 = false;
            } else {
                inc.缓存HTML += 简易Markdown渲染(fullContent.slice(inc.边界, inc.候选边界));
            }
            inc.边界 = inc.候选边界;
        }
    };

    // 尾部片段是否含孤立 </think>（此时渲染器会向前折叠已缓存前缀，只能整段全量渲染）
    const _尾部有孤立闭合 = (tail) => {
        let 深度 = 0;
        const re = /<(\/?)(?:think|thinking)>/gi;
        let m;
        while ((m = re.exec(tail)) !== null) {
            if (m[1]) { if (--深度 < 0) return true; }
            else 深度++;
        }
        return false;
    };

    // ── 辅助：渲染当前累积内容（保留流式光标，不触发 Prism 高亮）──
    const _渲染累积内容 = () => {
        const body = _当前消息体();
        if (body) {
            let html;
            if (fullContent.length >= _增量启用阈值) {
                _更新增量缓存();
                const tail = fullContent.slice(_增量.边界);
                if (_增量.边界 > 0 && _尾部有孤立闭合(tail)) {
                    html = 简易Markdown渲染(fullContent);
                } else {
                    html = _增量.缓存HTML + 简易Markdown渲染(tail);
                }
            } else {
                html = 简易Markdown渲染(fullContent);
            }
            body.innerHTML = html + '<span class="nca-streaming-cursor"></span>';
        }
        if (内容更新回调) 内容更新回调(fullContent);
    };

    // ── 辅助：滚动到底部 ──
    const 滚动 = (force = false) => {
        const container = _当前滚动容器();
        if (!container) return;
        // 智能滚动：用户向上阅读时不打断（距底部>150px视为阅读中）
        if (!force) {
            const distToBottom = container.scrollHeight - container.scrollTop - container.clientHeight;
            if (distToBottom > 150) return;
        }
        requestAnimationFrame(() => {
            container.scrollTop = container.scrollHeight;
        });
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

                        // 收到任何实际输出（内容/工具执行/错误/结束）即推理等待结束，
                        // 在 数据处理 回调前清理（tool_executing 会被回调 skip，不会到达下方分支）
                        if ((_推理状态行 || _推理定时器)
                            && (data.content || data.type === 'tool_executing' || data.error || data.done)) {
                            _清理推理状态();
                        }

                        // 自定义数据处理（如 context_health / tool_executing）
                        if (数据处理) {
                            const result = 数据处理(data, 状态);
                            if (result === 'skip') continue;
                        }

                        // ── 推理进度状态事件（老后端不发送，其余 status 子类型保持忽略）──
                        if (data.type === 'status') {
                            if (data.status === 'inference_start') {
                                _显示推理状态(data.model);
                                滚动();
                            } else if (data.status === 'thinking') {
                                // 心跳校准本地计时（取较大值，避免显示回跳）
                                const elapsed = parseInt(data.elapsed, 10);
                                if (Number.isFinite(elapsed) && elapsed > _推理等待秒) {
                                    _推理等待秒 = elapsed;
                                    _渲染推理状态();
                                }
                            }
                            continue; // status 事件不含正文内容
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
                            _清理推理状态();
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

                        // ── ask_user 事件：模型向用户提问，结束流等待用户真实回复 ──
                        if (data.type === 'ask_user') {
                            _清理推理状态();
                            const 结果 = {
                                fullContent: fullContent || "",
                                hasError: false,
                                isAbort: false,
                                连接中断: false,
                                type: "ask_user",
                                question: data.question || "",
                                options: Array.isArray(data.options) ? data.options : []
                            };
                            if (完成回调) {
                                完成回调(结果);
                            }
                            return 结果;
                        }

                        if (data.type === 'billing') {
                            // 本地 API Key 直连后不再处理后端 billing 事件（后端已不再发送）
                            continue;
                        }
                        if (data.error) {
                            hasError = true;
                            fullContent += `\n⚠ 错误: ${data.error}`;
                            break;
                        }
                        if (data.done) {
                            // 强制刷新剩余缓冲
                            if (_batchTimer) {
                                clearTimeout(_batchTimer);
                                _batchTimer = null;
                                _渲染累积内容();
                                滚动(true);
                            }
                            break;
                        }

                        if (data.content) {
                            fullContent += data.content;
                            if (!_首块已渲染) {
                                // 首个 chunk 零缓冲：立即渲染，尽快让用户看到首字
                                _首块已渲染 = true;
                                _渲染累积内容();
                                滚动();
                            } else if (!_batchTimer) {
                                // 后续批量缓冲：累积chunk，50ms后统一渲染
                                _batchTimer = setTimeout(() => {
                                    _batchTimer = null;
                                    _渲染累积内容();
                                    滚动();
                                }, _BATCH_INTERVAL);
                            }
                        }
                    } catch (parseErr) {
                        console.warn("[节点梦工厂] SSE 解析警告:", parseErr.message);
                    }
                }
                if (hasError) break;
            }
        } catch (e) {
            if (_batchTimer) { clearTimeout(_batchTimer); _batchTimer = null; }
            _清理推理状态(); // 中止/网络错误/重试路径统一清理推理状态行与定时器
            // 本地 API Key 直连后不再区分 402/403 会员/余额，统一按通用错误处理
            if (e.name === 'AbortError') {
                isAbort = true;
                fullContent += `\n\n_${t("chat.stopped")}_`;
                break SSE重试;
            }

            // 可重连错误且未收到任何内容：自动重试（指数退避，最大 3 次，10 秒时间窗口）
            if (是可重连错误(e) && !fullContent && 重试次数 < 最大重试 && (Date.now() - 重试开始时间) < 10000) {
                try { Toast.warning(`连接中断，正在重试(${重试次数 + 1}/${最大重试})...`); } catch (_) {}
                const _jitter = 0.8 + Math.random() * 0.4; // ±20% 随机抖动
                await new Promise(r => setTimeout(r, 1000 * Math.pow(2, 重试次数) * _jitter));
                重试次数++;
                需要重试 = true;
                // 重置状态以便重试
                buffer = '';
                hasError = false;
                _首块已渲染 = false;
                _增量 = _新增量状态(); // 重试从零累积内容，增量缓存同步重置
                状态.el = null;
                const retryBody = _当前消息体();
                if (retryBody) retryBody.innerHTML = '<span class="nca-streaming-cursor"></span>';
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

    // 兜底：流结束后无论何种路径，推理状态行与定时器都必须已清理
    _清理推理状态();

    // ── 完成后渲染 ──
    const 结果 = { fullContent: fullContent || "（无回复）", hasError, isAbort, 连接中断, planResult: 状态.planResult || null };

    if (完成回调) {
        完成回调(结果);
    } else {
        // 默认渲染
        const doneBody = _当前消息体();
        if (doneBody) doneBody.innerHTML = 简易Markdown渲染(结果.fullContent);
        const doneContainer = _当前消息容器();
        // DOMPurify 未就绪时标记并缓存原始内容，待加载完成后重新渲染
        if (!window.DOMPurify && doneBody && doneContainer) {
            doneBody.dataset.pendingSanitize = "true";
            doneContainer._pendingContent = 结果.fullContent;
        }
        if (doneContainer) 绑定代码块复制按钮(doneContainer);
    }

    // 连接中断且有部分内容：添加"重试"按钮（全量重试，保留已有内容）
    const retryContainer = _当前消息容器();
    if (连接中断 && fullContent && !isAbort && retryContainer) {
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
            const body = _当前消息体();
            const container = _当前消息容器();
            const 备份HTML = body ? body.innerHTML : '';
            重试按钮.remove();
            try {
                const 重试结果 = await 创建流式聊天(options);
                const 成功 = !重试结果.hasError && !重试结果.isAbort && !重试结果.连接中断
                    && 重试结果.fullContent && 重试结果.fullContent !== '（无回复）';
                if (!成功) {
                    // 重试失败：恢复原内容并重新添加重试按钮
                    if (body) body.innerHTML = 备份HTML;
                    if (container) {
                        绑定代码块复制按钮(container);
                        container.querySelectorAll('.nca-retry-btn').forEach(b => b.remove());
                    }
                    重试按钮.disabled = false;
                    重试按钮.textContent = '重试';
                    if (container) container.appendChild(重试按钮);
                }
                // 重试成功：内容已由创建流式聊天渲染，无需额外处理
            } catch (e) {
                if (body) body.innerHTML = 备份HTML;
                if (container) {
                    绑定代码块复制按钮(container);
                    container.querySelectorAll('.nca-retry-btn').forEach(b => b.remove());
                }
                重试按钮.disabled = false;
                重试按钮.textContent = '重试';
                if (container) container.appendChild(重试按钮);
            }
        });
        retryContainer.appendChild(重试按钮);
    }

    滚动(true);
    return 结果;
}

// ─── WebSocket 预连接 ──────────────────────────────────
let _wsPreconnected = null;
let _ws保活定时器 = null;

// 服务端仅在收到应用层消息时刷新活跃时间（协议级 heartbeat 不算数），
// 空闲超过 300 秒会被定期清理关闭，故每 240 秒发一次 ping 保活
const _WS保活间隔毫秒 = 240 * 1000;

function _清理保活定时器() {
    if (_ws保活定时器) {
        clearInterval(_ws保活定时器);
        _ws保活定时器 = null;
    }
}

function _发送保活() {
    try {
        if (_wsPreconnected && _wsPreconnected.readyState === WebSocket.OPEN) {
            _wsPreconnected.send(JSON.stringify({ type: 'ping' }));
        }
    } catch (e) { /* 发送失败忽略，由 onclose/onerror 回收 */ }
}

export function preconnectWebSocket() {
    if (_wsPreconnected) return;
    try {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/ai-coder/ws`;
        _wsPreconnected = new WebSocket(wsUrl);
        _wsPreconnected.onopen = () => {
            console.debug('[NCA] WebSocket 预连接就绪');
            _发送保活();
            _清理保活定时器();
            _ws保活定时器 = setInterval(_发送保活, _WS保活间隔毫秒);
        };
        _wsPreconnected.onerror = () => { _wsPreconnected = null; _清理保活定时器(); };
        _wsPreconnected.onclose = () => { _wsPreconnected = null; _清理保活定时器(); };
    } catch (e) {
        _wsPreconnected = null;
        _清理保活定时器();
    }
}

export function getPreconnectedWs() {
    const ws = _wsPreconnected;
    _wsPreconnected = null;
    _清理保活定时器();  // 连接已移交调用方接管，停止预连接保活
    return ws;
}
