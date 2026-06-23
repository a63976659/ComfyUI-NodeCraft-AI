// ═══════════════════════════════════════════════════════════════
// 可视化引擎.js — 3D 力导向图渲染（基于 3d-force-graph）
// 节点梦工厂 — 插件依赖可视化
// ═══════════════════════════════════════════════════════════════

// CDN 加载 3d-force-graph（异步按需，仅在用户打开可视化面板并触发渲染时加载）
let ForceGraph3D = null;
let graphLibLoaded = false;
let loadingPromise = null;
const CDN_URL = 'https://unpkg.com/3d-force-graph@1.73.4/dist/3d-force-graph.min.js';

/**
 * 是否已加载 3D 可视化库（供 UI 决定是否显示加载提示）
 */
export function 是否已加载可视化库() {
    return graphLibLoaded;
}

/**
 * 异步加载 3d-force-graph 库（已缓存：重复调用不会重复请求）
 * @param {Function} [onStart] - 首次开始加载时回调（用于显示加载提示）
 */
export async function 加载可视化库(onStart) {
    // 已加载：直接返回缓存结果
    if (graphLibLoaded) return true;
    // 正在加载：复用同一个 Promise，避免并发重复加载
    if (loadingPromise) return loadingPromise;

    // 首次加载：触发提示回调
    if (typeof onStart === 'function') {
        try { onStart(); } catch (_) { /* 忽略回调错误 */ }
    }

    loadingPromise = new Promise((resolve, reject) => {
        // 通过动态 script 标签按需引入（3d-force-graph UMD 版本已内置 three.js）
        const script = document.createElement('script');
        script.src = CDN_URL;
        script.async = true;
        script.onload = () => {
            ForceGraph3D = window.ForceGraph3D;
            if (ForceGraph3D) {
                graphLibLoaded = true;
                resolve(true);
            } else {
                loadingPromise = null;
                reject(new Error('ForceGraph3D 未加载'));
            }
        };
        script.onerror = () => {
            loadingPromise = null;
            reject(new Error('3d-force-graph CDN 加载失败，请检查网络连接'));
        };
        document.head.appendChild(script);
    });

    return loadingPromise;
}

// 节点颜色映射
const NODE_COLORS = {
    python: '#38bdf8',      // 蓝
    javascript: '#fbbf24',  // 黄
    config: '#a78bfa',      // 紫
    directory: '#6b7280',   // 灰
    entry: '#00ffc8',       // 青（发光）
    other: '#94a3b8'
};

// 连线颜色映射
const LINK_COLORS = {
    normal: '#10b981',      // 绿
    error: '#ef4444',       // 红
    weak: '#6b7280'         // 灰
};

/**
 * 创建可视化图实例
 * @param {HTMLElement} container - 渲染容器
 * @param {Object} graphData - { nodes: [...], links: [...] }
 * @param {Object} options - 配置选项
 * @returns {Object} 图实例
 */
export function 创建可视化图(container, graphData, options = {}) {
    if (!ForceGraph3D) {
        console.error('[节点梦工厂] ForceGraph3D 未加载');
        return null;
    }
    
    const { onNodeClick, onNodeHover } = options;
    
    const graph = ForceGraph3D()(container)
        .graphData(graphData)
        .backgroundColor('#0a0e1a')
        .nodeColor(node => NODE_COLORS[node.type] || NODE_COLORS.other)
        .nodeVal(node => node.type === 'directory' ? 8 : (node.type === 'entry' ? 5 : 3))
        .nodeLabel(node => `${node.name}\n${formatSize(node.size)}`)
        .nodeOpacity(0.9)
        .linkColor(link => LINK_COLORS[link.status] || LINK_COLORS.normal)
        .linkWidth(link => link.status === 'error' ? 2.5 : 1)
        .linkOpacity(0.6)
        .linkDirectionalParticles(link => link.status === 'error' ? 4 : 0)
        .linkDirectionalParticleColor(() => '#ef4444')
        .linkDirectionalParticleWidth(2)
        .d3Force('charge', null)  // 会在下面重新设置
        .warmupTicks(50)
        .cooldownTicks(100);
    
    // 设置力参数
    graph.d3Force('charge').strength(-120);
    
    // 事件处理
    if (onNodeClick) {
        graph.onNodeClick(onNodeClick);
    }
    if (onNodeHover) {
        graph.onNodeHover(onNodeHover);
    }
    
    return graph;
}

/**
 * 更新图数据
 */
export function 更新图数据(graph, graphData) {
    if (graph) {
        graph.graphData(graphData);
    }
}

/**
 * 销毁图实例
 */
export function 销毁图(graph) {
    if (graph) {
        graph._destructor && graph._destructor();
    }
}

// 格式化文件大小
function formatSize(bytes) {
    if (!bytes) return '';
    if (bytes < 1024) return `${bytes}B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)}MB`;
}
