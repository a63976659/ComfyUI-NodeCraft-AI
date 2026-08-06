// ═══════════════════════════════════════════════════════════════
// 消息渲染/虚拟滚动管理器.js — 消息列表虚拟滚动（性能优化）
// NodeCraft AI — Luxury Terminal / Neo-Noir Hacker
// 从 消息渲染器.js 拆分：仅在消息数量 >= 启用阈值 时启用，向后兼容原渲染逻辑
// ═══════════════════════════════════════════════════════════════

export const 虚拟滚动配置 = {
    启用阈值: 25,       // 超过该数量时启用虚拟滚动
    缓冲区大小: 15,     // 可视区上下各多渲染的消息条数
    预估消息高度: 120,  // 未测量消息的默认高度（px）
};

export class 虚拟滚动管理器 {
    constructor(容器元素, 消息列表, 渲染单条, 绑定后处理) {
        this.容器 = 容器元素;
        this.消息列表 = 消息列表;
        this.渲染单条 = 渲染单条;
        this.绑定后处理 = 绑定后处理;
        this.已测量高度 = new Map(); // 消息索引 → 实际高度
        // 消息索引(字符串) → 消息内各 <details> 的展开状态数组。
        // 重建 DOM 会把 <details> 重置为收起（表现为"长消息点击无法展开"），
        // 回收时记录、重建时恢复，跨重建保留展开状态
        this.展开状态 = new Map();
        this.startIndex = 0;
        this.endIndex = 0;
        this._rafId = null;
        this._rafResizeId = null;
        this.滚动监听 = this._节流滚动处理.bind(this);
        // ResizeObserver：监听消息气泡尺寸变化，解决流式内容动态增长导致的高度抖动
        this._resizeObserver = (typeof ResizeObserver !== "undefined") ? new ResizeObserver(entries => {
            if (this._rafResizeId) return; // RAF 节流，避免频繁重计算
            this._rafResizeId = requestAnimationFrame(() => {
                this._rafResizeId = null;
                let 需要更新 = false;
                for (const entry of entries) {
                    const index = entry.target._虚拟索引;
                    if (index !== undefined) {
                        const 新高度 = entry.target.offsetHeight;
                        if (新高度 > 0 && this.已测量高度.get(index) !== 新高度) {
                            this.已测量高度.set(index, 新高度);
                            需要更新 = true;
                        }
                    }
                }
                if (需要更新) this._渲染可视消息();
            });
        }) : null;
        this.顶部占位 = document.createElement("div");
        this.顶部占位.className = "nca-vscroll-spacer-top";
        this.顶部占位.style.cssText = "width:100%;flex-shrink:0;pointer-events:none;";
        this.底部占位 = document.createElement("div");
        this.底部占位.className = "nca-vscroll-spacer-bottom";
        this.底部占位.style.cssText = "width:100%;flex-shrink:0;pointer-events:none;";
        this._挂载();
    }

    _挂载() {
        this.容器.innerHTML = "";
        this.容器.appendChild(this.顶部占位);
        this.容器.appendChild(this.底部占位);
        this.容器.addEventListener("scroll", this.滚动监听, { passive: true });
        this._渲染可视消息();
    }

    销毁() {
        this.容器.removeEventListener("scroll", this.滚动监听);
        if (this._rafId) cancelAnimationFrame(this._rafId);
        this._rafId = null;
        if (this._resizeObserver) {
            this._resizeObserver.disconnect();
            this._resizeObserver = null;
        }
        if (this._rafResizeId) {
            cancelAnimationFrame(this._rafResizeId);
            this._rafResizeId = null;
        }
    }

    _获取高度(i) {
        const cached = this.已测量高度.get(i);
        if (cached) return cached;
        // 根据消息内容长度动态预估高度
        const msg = this.消息列表[i];
        if (msg && msg.content && msg.content.length > 500) {
            return Math.min(虚拟滚动配置.预估消息高度 * Math.ceil(msg.content.length / 300), 600);
        }
        return 虚拟滚动配置.预估消息高度;
    }

    _计算可视范围() {
        const scrollTop = this.容器.scrollTop;
        const viewportHeight = this.容器.clientHeight;
        const total = this.消息列表.length;
        let acc = 0;
        let startIndex = 0;
        for (let i = 0; i < total; i++) {
            const h = this._获取高度(i);
            if (acc + h > scrollTop) { startIndex = i; break; }
            acc += h;
            startIndex = i + 1;
        }
        let endIndex = startIndex;
        let cumul = acc;
        for (let i = startIndex; i < total; i++) {
            cumul += this._获取高度(i);
            endIndex = i;
            if (cumul >= scrollTop + viewportHeight) break;
        }
        const buf = 虚拟滚动配置.缓冲区大小;
        startIndex = Math.max(0, startIndex - buf);
        endIndex = Math.min(total - 1, endIndex + buf);
        let topPad = 0;
        for (let i = 0; i < startIndex; i++) topPad += this._获取高度(i);
        let bottomPad = 0;
        for (let i = endIndex + 1; i < total; i++) bottomPad += this._获取高度(i);
        return { startIndex, endIndex, topPad, bottomPad };
    }

    _渲染可视消息() {
        if (this.消息列表.length === 0) {
            this.顶部占位.style.height = "0px";
            this.底部占位.style.height = "0px";
            return;
        }
        const { startIndex, endIndex, topPad, bottomPad } = this._计算可视范围();
        this.startIndex = startIndex;
        this.endIndex = endIndex;
        // 回收占位之间的旧 DOM 节点
        let node = this.顶部占位.nextSibling;
        while (node && node !== this.底部占位) {
            const next = node.nextSibling;
            // 回收前记录该消息内 <details> 展开状态（长消息折叠/思考块），重建时恢复
            const idx = node.dataset ? node.dataset.vscrollIndex : undefined;
            if (idx !== undefined && node.querySelectorAll) {
                const opens = Array.from(node.querySelectorAll("details"), d => d.open);
                if (opens.some(Boolean)) this.展开状态.set(idx, opens);
                else this.展开状态.delete(idx);
            }
            if (this._resizeObserver) this._resizeObserver.unobserve(node);
            this.容器.removeChild(node);
            node = next;
        }
        this.顶部占位.style.height = topPad + "px";
        this.底部占位.style.height = bottomPad + "px";
        // 渲染可视范围 + 缓冲区
        const 新增节点 = [];
        for (let i = startIndex; i <= endIndex; i++) {
            const msg = this.消息列表[i];
            if (!msg) continue;
            // 传递索引：消息编辑功能需要知道当前消息在列表中的位置
            const dom = this.渲染单条(msg, i);
            dom.dataset.vscrollIndex = String(i);
            dom._虚拟索引 = i;
            this.容器.insertBefore(dom, this.底部占位);
            if (this.绑定后处理) this.绑定后处理(dom);
            // 恢复重建前的 <details> 展开状态（按出现顺序对位）
            const opens = this.展开状态.get(String(i));
            if (opens) {
                dom.querySelectorAll("details").forEach((d, k) => {
                    if (opens[k]) d.open = true;
                });
            }
            // 观察该消息气泡尺寸，流式内容增长时自动更新已测量高度
            if (this._resizeObserver) this._resizeObserver.observe(dom);
            新增节点.push({ index: i, dom });
        }
        // 下一帧测量实际高度并缓存
        requestAnimationFrame(() => {
            for (const { index, dom } of 新增节点) {
                const h = dom.offsetHeight;
                if (h > 0) this.已测量高度.set(index, h);
            }
        });
    }

    _节流滚动处理() {
        if (this._rafId) return;
        this._rafId = requestAnimationFrame(() => {
            this._rafId = null;
            this._渲染可视消息();
        });
    }

    重新渲染() {
        this._渲染可视消息();
    }

    追加新消息() {
        // 调用方已将新消息 push 到共享 消息列表
        this._渲染可视消息();
    }

    跳转到消息(index) {
        let offset = 0;
        for (let i = 0; i < index; i++) offset += this._获取高度(i);
        this.容器.scrollTop = offset;
    }

    // 编辑重生成后调用：消息列表已被外部截断到 newLength，
    // 清理 >= newLength 的高度缓存并重新渲染可视区。
    截断消息(newLength) {
        for (const key of Array.from(this.已测量高度.keys())) {
            if (key >= newLength) this.已测量高度.delete(key);
        }
        for (const key of Array.from(this.展开状态.keys())) {
            if (Number(key) >= newLength) this.展开状态.delete(key);
        }
        this._渲染可视消息();
    }
}
