// ═══════════════════════════════════════════════════════════════
// 可视化引擎.js — 3D 力导向图渲染（原生 Three.js 实现）
// 节点梦工厂 — 插件依赖可视化
// ═══════════════════════════════════════════════════════════════

import * as THREE from './lib/three.module.js';
import { OrbitControls } from './lib/OrbitControls.js';

// 本地静态 import，模块加载时即完成，无需异步等待
let libLoaded = true;

// ─── 颜色 / 尺寸映射 ─────────────────────────────────────────
const STATUS_COLORS = {
    normal:  { main: 0x00ff88, emissive: 0x00aa55, glow: '#00ff88', lightMain: 0x059669, lightEmissive: 0x047857 },
    error:   { main: 0xff2255, emissive: 0xcc0033, glow: '#ff2255', lightMain: 0xdc2626, lightEmissive: 0xb91c1c },
    warning: { main: 0xffaa00, emissive: 0xcc7700, glow: '#ffaa00', lightMain: 0xd97706, lightEmissive: 0xb45309 },
};

const SIZE_MAP = {
    root: 3.5, directory: 2.4, app: 2.8, script: 1.3,
    config: 1.2, model: 1.6, component: 1.3, web: 1.2,
    style: 1.1, doc: 1.0, asset: 1.4, binary: 1.2, lock: 1.0, file: 1.1, entry: 2.0,
};

/**
 * 是否已加载 3D 可视化库
 */
export function 是否已加载可视化库() {
    return libLoaded;
}

/**
 * 异步加载 Three.js + OrbitControls
 * 本地静态 import 在模块加载时即完成，此函数仅保持 API 兼容。
 * @param {Function} [onStart] - 兼容旧回调（本地加载无需回调，但保留调用以防外部依赖）
 */
export async function 加载可视化库(onStart) {
    return true;
}

// 兼容旧名称
export const 加载ForceGraph3D = 加载可视化库;

// ─── 纹理工具 ─────────────────────────────────────────────────

/** 生成径向渐变发光纹理 */
function createGlowTexture(color) {
    const s = 128;
    const c = document.createElement('canvas');
    c.width = s; c.height = s;
    const ctx = c.getContext('2d');
    const g = ctx.createRadialGradient(s / 2, s / 2, 0, s / 2, s / 2, s / 2);
    g.addColorStop(0, color + 'cc');
    g.addColorStop(0.25, color + '44');
    g.addColorStop(0.5, color + '11');
    g.addColorStop(1, 'transparent');
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, s, s);
    return new THREE.CanvasTexture(c);
}

/** 生成文字标签纹理 */
function createLabelTexture(text, color = '#c0d8f0') {
    const c = document.createElement('canvas');
    const ctx = c.getContext('2d');
    const fs = 36;
    ctx.font = `600 ${fs}px Rajdhani, sans-serif`;
    const w = ctx.measureText(text).width + 24;
    const h = fs + 16;
    c.width = w; c.height = h;
    ctx.font = `600 ${fs}px Rajdhani, sans-serif`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillStyle = color;
    ctx.shadowColor = color === '#c0d8f0' ? 'rgba(0,0,0,0.7)' : 'rgba(0,0,0,0.15)';
    ctx.shadowBlur = 4;
    ctx.fillText(text, w / 2, h / 2);
    const tex = new THREE.CanvasTexture(c);
    tex.minFilter = THREE.LinearFilter;
    return { texture: tex, aspect: w / h };
}

// ─── 力导向布局 ───────────────────────────────────────────────

function runLayout(nodes, edges, nodeMap, iterations = 350) {
    const repulsion = 600, attraction = 0.015, centering = 0.008, damping = 0.88;
    for (let iter = 0; iter < iterations; iter++) {
        const temp = 1 - iter / iterations;
        // 斥力
        for (let i = 0; i < nodes.length; i++) {
            for (let j = i + 1; j < nodes.length; j++) {
                const a = nodes[i], b = nodes[j];
                let dx = a.x - b.x, dy = a.y - b.y, dz = a.z - b.z;
                let dist = Math.sqrt(dx * dx + dy * dy + dz * dz) + 0.1;
                let f = repulsion / (dist * dist) * temp;
                let fx = dx / dist * f, fy = dy / dist * f, fz = dz / dist * f;
                a.vx += fx; a.vy += fy; a.vz += fz;
                b.vx -= fx; b.vy -= fy; b.vz -= fz;
            }
        }
        // 吸引力
        edges.forEach(e => {
            const a = nodeMap[e.source], b = nodeMap[e.target];
            if (!a || !b) return;
            let dx = b.x - a.x, dy = b.y - a.y, dz = b.z - a.z;
            let dist = Math.sqrt(dx * dx + dy * dy + dz * dz) + 0.1;
            let f = attraction * dist * temp;
            a.vx += dx / dist * f; a.vy += dy / dist * f; a.vz += dz / dist * f;
            b.vx -= dx / dist * f; b.vy -= dy / dist * f; b.vz -= dz / dist * f;
        });
        // 中心回归 + 阻尼 + 位移
        nodes.forEach(n => {
            n.vx -= n.x * centering; n.vy -= n.y * centering; n.vz -= n.z * centering;
            n.vx *= damping; n.vy *= damping; n.vz *= damping;
            n.x += n.vx; n.y += n.vy; n.z += n.vz;
        });
    }
}

// ─── 创建可视化图实例 ─────────────────────────────────────────

/**
 * 创建可视化图实例
 * @param {HTMLElement} container - 渲染容器
 * @param {Object} graphData - { nodes: [...], links: [...] }
 * @param {Object} options - { onNodeClick, onNodeHover }
 * @returns {Object} 图实例（兼容旧 API）
 */
export function 创建可视化图(container, graphData, options = {}) {
    if (!THREE) {
        console.error('[节点梦工厂] Three.js 未加载');
        return null;
    }

    const { onNodeClick, onNodeHover } = options;

    // ── 主题检测：根据容器祖先是否有 .nca-theme-light 切换配色 ──
    const isLight = !!container.closest('.nca-theme-light');
    const theme = isLight ? {
        clearColor: 0xf0f1f8,
        fogColor: 0xf0f1f8,
        fogDensity: 0.002,
        ambientColor: 0xc0c8e0, ambientIntensity: 2.5,
        dirLight1Color: 0xffffff, dirLight1Intensity: 1.8,
        dirLight2Color: 0x8888cc, dirLight2Intensity: 0.6,
        pointLightColor: 0x2563eb, pointLightIntensity: 0.6,
        starColor: 0x8899cc, starOpacity: 0.15,
        gridColor: 0xc0c8d8, gridOpacity: 0.2,
        labelColor: '#1e293b',
        labelBg: 'rgba(255,255,255,0.7)',
    } : {
        clearColor: 0x060a14,
        fogColor: 0x060a14,
        fogDensity: 0.003,
        ambientColor: 0x1a2a4a, ambientIntensity: 2.0,
        dirLight1Color: 0x88bbff, dirLight1Intensity: 1.5,
        dirLight2Color: 0x443366, dirLight2Intensity: 0.8,
        pointLightColor: 0x00e5ff, pointLightIntensity: 1.0,
        starColor: 0x3355aa, starOpacity: 0.5,
        gridColor: 0x0a1a30, gridOpacity: 0.25,
        labelColor: '#c0d8f0',
        labelBg: 'rgba(8,16,32,0.5)',
    };

    // ── 渲染器 ──
    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    // 容器可能刚从 display:none 恢复，布局未完成时 clientWidth 为 0，用回退值避免 0×0 画布
    let initW = container.clientWidth || 800;
    let initH = container.clientHeight || 480;
    // 确保最小尺寸，避免 0×0 画布（容器刚从 hidden 恢复时布局可能未完成）
    initW = Math.max(initW, 300);
    initH = Math.max(initH, 300);
    renderer.setSize(initW, initH);
    renderer.setClearColor(theme.clearColor, 1);
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.2;
    container.appendChild(renderer.domElement);

    // ── 场景 ──
    const scene = new THREE.Scene();
    scene.fog = new THREE.FogExp2(theme.fogColor, theme.fogDensity);

    // ── 相机 ──
    const camera = new THREE.PerspectiveCamera(55, initW / initH, 0.1, 2000);
    camera.position.set(0, 30, 120);

    // ── 轨道控制 ──
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.06;
    controls.rotateSpeed = 0.6;
    controls.zoomSpeed = 1.2;
    controls.minDistance = 15;
    controls.maxDistance = 500;
    controls.target.set(0, 0, 0);

    // ── 光照 ──
    scene.add(new THREE.AmbientLight(theme.ambientColor, theme.ambientIntensity));
    const dLight = new THREE.DirectionalLight(theme.dirLight1Color, theme.dirLight1Intensity);
    dLight.position.set(50, 80, 60);
    scene.add(dLight);
    const dLight2 = new THREE.DirectionalLight(theme.dirLight2Color, theme.dirLight2Intensity);
    dLight2.position.set(-40, -20, -50);
    scene.add(dLight2);
    const pLight = new THREE.PointLight(theme.pointLightColor, theme.pointLightIntensity, 300);
    scene.add(pLight);

    // ── 星空 ──
    const starGeo = new THREE.BufferGeometry();
    const starPos = new Float32Array(1200 * 3);
    for (let i = 0; i < 1200; i++) {
        starPos[i * 3]     = (Math.random() - 0.5) * 800;
        starPos[i * 3 + 1] = (Math.random() - 0.5) * 800;
        starPos[i * 3 + 2] = (Math.random() - 0.5) * 800;
    }
    starGeo.setAttribute('position', new THREE.BufferAttribute(starPos, 3));
    const stars = new THREE.Points(starGeo, new THREE.PointsMaterial({
        color: theme.starColor, size: 0.6, transparent: true, opacity: theme.starOpacity,
        sizeAttenuation: true, blending: THREE.AdditiveBlending, depthWrite: false,
    }));
    scene.add(stars);

    // ── 网格 ──
    const grid = new THREE.GridHelper(300, 60, theme.gridColor, theme.gridColor);
    grid.position.y = -50;
    grid.material.transparent = true;
    grid.material.opacity = theme.gridOpacity;
    scene.add(grid);

    // ── 节点 / 边组 ──
    const nodeGroup = new THREE.Group();
    scene.add(nodeGroup);
    const edgeGroup = new THREE.Group();
    scene.add(edgeGroup);

    // ── 数据准备 ──
    const rawNodes = graphData.nodes || [];
    const rawLinks = graphData.links || [];

    // 将 links 统一为 source/target 格式
    const edges = rawLinks.map(l => ({
        source: l.source,
        target: l.target,
        type: l.type || 'containment',
        status: l.status || 'normal',
        label: l.label || '',
        reason: l.reason || '',
    }));

    // 构建节点数据（带初始随机位置）
    const nodes = rawNodes.map(n => ({
        id: n.id,
        name: n.name || n.id,
        type: n.type || 'file',
        size: n.size || 0,
        status: n.status || 'normal',
        group: n.group || '',
        reason: n.reason || '',
        x: (Math.random() - 0.5) * 80,
        y: (Math.random() - 0.5) * 80,
        z: (Math.random() - 0.5) * 80,
        vx: 0, vy: 0, vz: 0,
    }));

    const nodeMap = {};
    nodes.forEach(n => { nodeMap[n.id] = n; });

    // ── 力导向布局 ──
    runLayout(nodes, edges, nodeMap, 350);

    // ── 存储 Three.js 对象引用 ──
    const nodeMeshes = [];     // 所有节点 Mesh
    const glowSprites = [];    // 发光 Sprite 列表
    const nodeDataMap = {};    // nodeId -> { mesh, glow, label, data }

    // ── 自定义颜色函数（由面板后续设置） ──
    let customNodeColorFn = null;
    let customLinkLabelFn = null;

    // ── 创建节点 Mesh ──
    nodes.forEach(n => {
        const sc = STATUS_COLORS[n.status] || STATUS_COLORS.normal;
        const mainColor = isLight ? sc.lightMain : sc.main;
        const emissiveColor = isLight ? sc.lightEmissive : sc.emissive;
        const radius = (SIZE_MAP[n.type] || 1.4) * 0.7;
        n._radius = radius;

        // PBR 球体
        const geo = new THREE.SphereGeometry(radius, 24, 18);
        const mat = new THREE.MeshStandardMaterial({
            color: mainColor,
            emissive: emissiveColor,
            emissiveIntensity: isLight ? 0.3 : 0.6,
            roughness: 0.3,
            metalness: 0.5,
            transparent: true,
            opacity: 0.92,
        });
        const mesh = new THREE.Mesh(geo, mat);
        mesh.position.set(n.x, n.y, n.z);
        mesh.userData = { nodeId: n.id };
        nodeGroup.add(mesh);
        nodeMeshes.push(mesh);
        n._mesh = mesh;

        // 发光 Sprite
        const glowTex = createGlowTexture(sc.glow);
        const spriteMat = new THREE.SpriteMaterial({
            map: glowTex, transparent: true, opacity: isLight ? 0.35 : 0.7,
            blending: isLight ? THREE.NormalBlending : THREE.AdditiveBlending,
            depthWrite: false,
        });
        const sprite = new THREE.Sprite(spriteMat);
        const gs = radius * 5;
        sprite.scale.set(gs, gs, 1);
        sprite.position.copy(mesh.position);
        nodeGroup.add(sprite);
        glowSprites.push({ sprite, node: n, baseScale: gs });
        n._glow = sprite;

        // 文字标签
        const { texture, aspect } = createLabelTexture(n.name || n.id, theme.labelColor);
        const labelMat = new THREE.SpriteMaterial({
            map: texture, transparent: true, opacity: 0.85,
            depthTest: false, sizeAttenuation: true,
        });
        const ls = new THREE.Sprite(labelMat);
        const lh = 2.2;
        ls.scale.set(lh * aspect, lh, 1);
        ls.position.set(n.x, n.y + radius + 2.5, n.z);
        nodeGroup.add(ls);
        n._label = ls;

        nodeDataMap[n.id] = { mesh, glow: sprite, label: ls, data: n };
    });

    // ── 创建边 / 连线 ──
    edges.forEach(e => {
        const a = nodeMap[e.source], b = nodeMap[e.target];
        if (!a || !b) return;
        const isErr = e.status === 'error', isWarn = e.status === 'warning';
        const isDep = e.type === 'dependency' || e.type === 'import';
        const color = isErr ? 0xff2255 : isWarn ? 0xffaa00 : (isLight ? 0x059669 : 0x00ff88);

        const pts = [
            new THREE.Vector3(a.x, a.y, a.z),
            new THREE.Vector3(b.x, b.y, b.z),
        ];

        // 基础线
        const lineGeo = new THREE.BufferGeometry().setFromPoints(pts);
        const lineOpacity = isDep ? (isErr ? 0.95 : isWarn ? 0.8 : 0.6) : (isErr ? 0.7 : isWarn ? 0.5 : 0.25);
        const lineMat = new THREE.LineBasicMaterial({
            color, transparent: true, opacity: lineOpacity,
        });
        const line = new THREE.Line(lineGeo, lineMat);
        edgeGroup.add(line);

        // 管状边
        const path = new THREE.LineCurve3(pts[0], pts[1]);
        const tr = isDep ? (isErr ? 0.25 : isWarn ? 0.2 : 0.15) : (isErr ? 0.14 : isWarn ? 0.1 : 0.06);
        const tubeOpacity = isDep ? (isErr ? 0.8 : isWarn ? 0.6 : 0.35) : (isErr ? 0.5 : isWarn ? 0.35 : 0.12);
        const tubeGeo = new THREE.TubeGeometry(path, 1, tr, 6, false);
        const tubeMat = new THREE.MeshBasicMaterial({
            color, transparent: true, opacity: tubeOpacity,
        });
        const tubeMesh = new THREE.Mesh(tubeGeo, tubeMat);
        edgeGroup.add(tubeMesh);

        e._line = line;
        e._tube = tubeMesh;
        e._baseLineOpacity = lineOpacity;
        e._baseTubeOpacity = tubeOpacity;
    });

    // ── Raycaster 交互 ──
    const raycaster = new THREE.Raycaster();
    const mouse = new THREE.Vector2();
    let hoveredNode = null;
    let selectedNode = null;

    function onMouseMove(event) {
        const rect = renderer.domElement.getBoundingClientRect();
        mouse.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
        mouse.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
        raycaster.setFromCamera(mouse, camera);
        const hits = raycaster.intersectObjects(nodeMeshes);

        if (hits.length > 0) {
            const mesh = hits[0].object;
            const n = nodeMap[mesh.userData.nodeId];
            if (hoveredNode !== n) {
                if (hoveredNode) unhoverNode(hoveredNode);
                hoveredNode = n;
                hoverNode(n);
            }
            renderer.domElement.style.cursor = 'pointer';
            if (typeof onNodeHover === 'function') {
                try { onNodeHover(n, event); } catch (_) {}
            }
        } else {
            if (hoveredNode) {
                unhoverNode(hoveredNode);
                hoveredNode = null;
            }
            renderer.domElement.style.cursor = 'grab';
            if (typeof onNodeHover === 'function') {
                try { onNodeHover(null, event); } catch (_) {}
            }
        }
    }

    function hoverNode(n) {
        n._mesh.material.emissiveIntensity = 1.2;
        n._mesh.scale.set(1.25, 1.25, 1.25);
        n._glow.material.opacity = 1.0;
        edges.forEach(e => {
            if (e.source === n.id || e.target === n.id) {
                if (e._line) e._line.material.opacity = 1.0;
                if (e._tube) e._tube.material.opacity = 0.9;
            }
        });
    }

    function unhoverNode(n) {
        if (!n) return;
        n._mesh.material.emissiveIntensity = isLight ? 0.3 : 0.6;
        n._mesh.scale.set(1, 1, 1);
        n._glow.material.opacity = isLight ? 0.35 : 0.7;
        edges.forEach(e => {
            if (e.source === n.id || e.target === n.id) {
                const isE = e.status === 'error', isW = e.status === 'warning';
                const isD = e.type === 'dependency';
                if (e._line) e._line.material.opacity = isD ? (isE ? 0.95 : isW ? 0.8 : 0.6) : (isE ? 0.7 : isW ? 0.5 : 0.25);
                if (e._tube) e._tube.material.opacity = isD ? (isE ? 0.8 : isW ? 0.6 : 0.35) : (isE ? 0.5 : isW ? 0.35 : 0.12);
            }
        });
    }

    function onClick(event) {
        const rect = renderer.domElement.getBoundingClientRect();
        mouse.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
        mouse.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
        raycaster.setFromCamera(mouse, camera);
        const hits = raycaster.intersectObjects(nodeMeshes);
        if (hits.length > 0) {
            const n = nodeMap[hits[0].object.userData.nodeId];
            selectedNode = n;
            if (typeof onNodeClick === 'function') {
                try { onNodeClick(n); } catch (_) {}
            }
        } else {
            selectedNode = null;
        }
    }

    renderer.domElement.addEventListener('mousemove', onMouseMove);
    renderer.domElement.addEventListener('click', onClick);

    // ── 动画循环 ──
    const clock = new THREE.Clock();
    let animFrameId = null;
    let destroyed = false;

    function animate() {
        if (destroyed) return;
        animFrameId = requestAnimationFrame(animate);
        const t = clock.getElapsedTime();

        // 异常节点脉冲
        nodes.forEach(n => {
            if (!n._mesh) return;
            if (n.status === 'error') {
                n._mesh.material.emissiveIntensity = (hoveredNode === n || selectedNode === n) ? 1.5 : 0.6 + Math.sin(t * 3.5) * 0.4;
                n._glow.material.opacity = 0.5 + Math.sin(t * 3.5) * 0.3;
                const s = 1 + Math.sin(t * 3.5) * 0.06;
                if (hoveredNode !== n) n._mesh.scale.set(s, s, s);
            } else if (n.status === 'warning') {
                n._mesh.material.emissiveIntensity = (hoveredNode === n || selectedNode === n) ? 1.2 : 0.6 + Math.sin(t * 2) * 0.2;
                n._glow.material.opacity = 0.5 + Math.sin(t * 2) * 0.15;
            }
        });

        // 异常边闪烁
        edges.forEach(e => {
            if (e.status === 'error' && e._line) {
                e._line.material.opacity = 0.6 + Math.sin(t * 4) * 0.35;
                if (e._tube) e._tube.material.opacity = 0.4 + Math.sin(t * 4) * 0.35;
            }
        });

        // 星空缓慢旋转
        stars.rotation.y += 0.00008;
        stars.rotation.x += 0.00003;

        controls.update();
        renderer.render(scene, camera);
    }

    animate();

    // ── resize 处理 ──
    function onResize() {
        if (destroyed) return;
        const cw = container.clientWidth;
        const ch = container.clientHeight;
        if (cw > 0 && ch > 0) {
            camera.aspect = cw / ch;
            camera.updateProjectionMatrix();
            renderer.setSize(cw, ch);
        }
    }
    const resizeObserver = new ResizeObserver(onResize);
    resizeObserver.observe(container);

    // ── 搜索/过滤联动状态 ──
    let _searchQuery = '';
    let _activeFilter = 'all';

    function applyFiltering() {
        const q = _searchQuery.toLowerCase();
        const filter = _activeFilter;
        nodes.forEach(n => {
            if (!n._mesh) return;
            let visible = true;
            // 过滤状态
            if (filter !== 'all') visible = n.status === filter;
            // 搜索匹配（多字段：name, id, type/category, desc）
            if (q && visible) {
                const fields = [n.name, n.id, n.type, n.category, n.desc].filter(Boolean).join(' ').toLowerCase();
                visible = fields.includes(q);
            }
            n._mesh.visible = visible;
            n._mesh.material.opacity = visible ? 0.92 : 0.08;
            if (n._glow) { n._glow.visible = visible; n._glow.material.opacity = visible ? 0.7 : 0.03; }
            if (n._label) { n._label.visible = visible; n._label.material.opacity = visible ? 0.85 : 0.08; }
        });
        edges.forEach(e => {
            const sNode = nodeMap[e.source], tNode = nodeMap[e.target];
            const visible = sNode && tNode && sNode._mesh && tNode._mesh && sNode._mesh.visible && tNode._mesh.visible;
            if (e._line) e._line.visible = visible;
            if (e._tube) e._tube.visible = visible;
        });
    }

    // ── 返回兼容 API 对象 ──
    const graphInstance = {
        /** 设置节点颜色函数（兼容旧 API） */
        nodeColor(fn) {
            customNodeColorFn = fn;
            // 应用自定义颜色
            nodes.forEach(n => {
                const color = fn(n);
                if (color && n._mesh) {
                    // 将 CSS 颜色字符串转为 Three.js 颜色
                    const c = new THREE.Color(color);
                    n._mesh.material.color.copy(c);
                    n._mesh.material.emissive.copy(c).multiplyScalar(0.6);
                }
            });
        },
        /** 设置边标签（兼容旧 API） */
        linkLabel(fn) {
            customLinkLabelFn = fn;
            // 预留：可以在后续版本中显示边标签
        },
        /** 设置边方向粒子效果（兼容旧 API，当前为视觉增强边的亮度） */
        linkDirectionalParticles(fn) {
            // 根据 fn 返回值增强对应边的可见性
            edges.forEach(e => {
                const particleCount = fn(e);
                if (particleCount > 0 && e._line) {
                    e._line.material.opacity = Math.min(1.0, e._line.material.opacity + 0.2);
                    if (e._tube) e._tube.material.opacity = Math.min(0.9, e._tube.material.opacity + 0.15);
                }
            });
        },
        /** 搜索节点 — 设置搜索词并应用过滤 */
        searchNodes(query) {
            _searchQuery = query || '';
            applyFiltering();
        },
        /** 过滤节点 — 设置过滤状态并应用过滤 */
        filterNodes(status) {
            _activeFilter = status || 'all';
            applyFiltering();
        },
        /** 获取节点统计数据 */
        getStats() {
            const stats = { total: nodes.length, normal: 0, error: 0, warning: 0 };
            nodes.forEach(n => {
                if (n.status === 'error') stats.error++;
                else if (n.status === 'warning') stats.warning++;
                else stats.normal++;
            });
            return stats;
        },
        /** 内部引用 */
        _scene: scene,
        _camera: camera,
        _renderer: renderer,
        _nodes: nodes,
        _edges: edges,
        _nodeMap: nodeMap,
    };

    // ── 销毁方法（挂载到实例上供 销毁图 调用） ──
    graphInstance._destroy = function () {
        destroyed = true;
        if (animFrameId) cancelAnimationFrame(animFrameId);

        // 移除事件监听
        renderer.domElement.removeEventListener('mousemove', onMouseMove);
        renderer.domElement.removeEventListener('click', onClick);
        resizeObserver.disconnect();

        // 清理 Three.js 资源
        scene.traverse(obj => {
            if (obj.geometry) obj.geometry.dispose();
            if (obj.material) {
                if (obj.material.map) obj.material.map.dispose();
                obj.material.dispose();
            }
        });
        renderer.dispose();

        // 移除 canvas
        if (renderer.domElement.parentNode) {
            renderer.domElement.parentNode.removeChild(renderer.domElement);
        }
    };

    return graphInstance;
}

/**
 * 更新图数据（预留 API）
 */
export function 更新图数据(graph, graphData) {
    // 当前实现：销毁后重建（预留扩展）
    console.warn('[节点梦工厂] 更新图数据 暂未实现动态更新，请重新创建图');
}

/**
 * 销毁图实例（清理 Three.js 资源）
 */
export function 销毁图(graph) {
    if (graph && typeof graph._destroy === 'function') {
        graph._destroy();
    } else if (graph) {
        // 兼容旧的 ForceGraph3D 实例
        graph._destructor && graph._destructor();
    }
}

// 兼容旧名称
export const 销毁图实例 = 销毁图;

/**
 * 格式化文件大小
 */
export function formatSize(bytes) {
    if (!bytes) return '';
    if (bytes < 1024) return `${bytes}B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)}MB`;
}
