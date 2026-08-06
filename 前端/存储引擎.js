// ═══════════════════════════════════════════════════════════════
// 存储引擎.js — IndexedDB 异步存储引擎
// NodeCraft AI — Luxury Terminal / Neo-Noir Hacker
// ───────────────────────────────────────────────────────────────
// 设计目标：
//   1. 解决 localStorage 同步阻塞主线程、5MB 容量上限问题
//   2. 提供 localStorage 兼容的语义（字符串键值对）
//   3. 自动降级：IndexedDB 不可用 → 透明回落到 localStorage
//   4. 影子缓存：异步写入 IndexedDB 同时同步刷新 localStorage，
//      使现有 同步读取 调用仍可读到最新值，平滑迁移不破坏既有逻辑
// ═══════════════════════════════════════════════════════════════

const DB_NAME = 'NodeCraftAI';
const DB_VERSION = 1;
const STORE_NAME = 'kv_store';
const MIGRATION_FLAG = '_nca_migrated_to_idb';
const KEY_PREFIX = 'NodeCraftAI_';

let dbInstance = null;
let dbOpenPromise = null;
let _idbSupported = null; // 三态: null=未检测, true=支持, false=不支持

function _supportsIDB() {
    if (_idbSupported !== null) return _idbSupported;
    try {
        _idbSupported = (typeof indexedDB !== 'undefined' && indexedDB !== null);
    } catch (_) {
        _idbSupported = false;
    }
    return _idbSupported;
}

function _safeLocalGet(key) {
    try { return localStorage.getItem(key); } catch (_) { return null; }
}
function _safeLocalSet(key, value) {
    try { localStorage.setItem(key, value); } catch (_) { /* quota / 隐私模式 */ }
}
function _safeLocalRemove(key) {
    try { localStorage.removeItem(key); } catch (_) {}
}

function _getDB() {
    if (dbInstance) return Promise.resolve(dbInstance);
    if (dbOpenPromise) return dbOpenPromise;
    if (!_supportsIDB()) return Promise.reject(new Error('IndexedDB not supported'));

    dbOpenPromise = new Promise((resolve, reject) => {
        let request;
        try {
            request = indexedDB.open(DB_NAME, DB_VERSION);
        } catch (e) {
            _idbSupported = false;
            reject(e);
            return;
        }
        request.onupgradeneeded = (e) => {
            const db = e.target.result;
            if (!db.objectStoreNames.contains(STORE_NAME)) {
                db.createObjectStore(STORE_NAME);
            }
        };
        request.onsuccess = (e) => {
            dbInstance = e.target.result;
            // 兼容性：连接被外部 versionchange 关闭时自动失效
            dbInstance.onversionchange = () => {
                try { dbInstance.close(); } catch (_) {}
                dbInstance = null;
                dbOpenPromise = null;
            };
            resolve(dbInstance);
        };
        request.onerror = (e) => {
            dbOpenPromise = null;
            _idbSupported = false;
            reject(e.target.error);
        };
        request.onblocked = () => {
            // 旧连接阻塞升级；不致命，等待回调
        };
    });
    return dbOpenPromise;
}

/**
 * 异步存储 API（IndexedDB 优先，localStorage 影子缓存 + 失败降级）
 */
export const 存储 = {
    /**
     * 异步读取键值（优先 IndexedDB，失败回落 localStorage）
     * @param {string} key
     * @returns {Promise<string|null>}
     */
    async 读取(key) {
        if (_supportsIDB()) {
            try {
                const db = await _getDB();
                return await new Promise((resolve, reject) => {
                    const tx = db.transaction(STORE_NAME, 'readonly');
                    const store = tx.objectStore(STORE_NAME);
                    const req = store.get(key);
                    req.onsuccess = () => {
                        const v = req.result;
                        resolve(v === undefined ? _safeLocalGet(key) : v);
                    };
                    req.onerror = () => reject(req.error);
                });
            } catch (_) {
                return _safeLocalGet(key);
            }
        }
        return _safeLocalGet(key);
    },

    /**
     * 异步写入键值；同步影子写入 localStorage，保持 同步读取 兼容
     * @param {string} key
     * @param {string} value
     * @returns {Promise<void>}
     */
    async 写入(key, value) {
        const v = value == null ? '' : String(value);
        // 影子缓存：先同步写入 localStorage，确保后续 同步读取 立即可见
        _safeLocalSet(key, v);
        if (!_supportsIDB()) return;
        try {
            const db = await _getDB();
            await new Promise((resolve, reject) => {
                const tx = db.transaction(STORE_NAME, 'readwrite');
                const store = tx.objectStore(STORE_NAME);
                const req = store.put(v, key);
                req.onsuccess = () => resolve();
                req.onerror = () => reject(req.error);
            });
        } catch (_) {
            /* 已写入 localStorage，吞掉异常即可 */
        }
    },

    /**
     * 异步删除键
     * @param {string} key
     * @returns {Promise<void>}
     */
    async 删除(key) {
        _safeLocalRemove(key);
        if (!_supportsIDB()) return;
        try {
            const db = await _getDB();
            await new Promise((resolve, reject) => {
                const tx = db.transaction(STORE_NAME, 'readwrite');
                const store = tx.objectStore(STORE_NAME);
                const req = store.delete(key);
                req.onsuccess = () => resolve();
                req.onerror = () => reject(req.error);
            });
        } catch (_) {}
    },

    /**
     * 同步读取（用于必须同步的极少数场景，如主题初始化避免闪烁）
     * 由于 写入/删除 始终同步影子刷新 localStorage，此处可读到最新值
     */
    同步读取(key) {
        return _safeLocalGet(key);
    },

    /**
     * 同步写入（极少数同步场景使用；同时排队异步刷新 IndexedDB）
     */
    同步写入(key, value) {
        const v = value == null ? '' : String(value);
        _safeLocalSet(key, v);
        if (_supportsIDB()) {
            // 触发异步同步到 IndexedDB（fire-and-forget）
            this.写入(key, v).catch(() => {});
        }
    },

    /**
     * 同步删除（极少数同步场景使用）
     */
    同步删除(key) {
        _safeLocalRemove(key);
        if (_supportsIDB()) {
            this.删除(key).catch(() => {});
        }
    },
};

/**
 * 首次运行从 localStorage 迁移业务键到 IndexedDB
 * 仅迁移 NodeCraftAI_ 前缀键，避免污染其他扩展数据
 */
export async function 迁移旧数据() {
    if (!_supportsIDB()) return;
    if (_safeLocalGet(MIGRATION_FLAG)) return;

    try {
        const db = await _getDB();
        // 收集待迁移条目
        const entries = [];
        for (let i = 0; i < localStorage.length; i++) {
            const key = localStorage.key(i);
            if (key && key.startsWith(KEY_PREFIX)) {
                const value = _safeLocalGet(key);
                if (value !== null) entries.push([key, value]);
            }
        }

        await new Promise((resolve, reject) => {
            const tx = db.transaction(STORE_NAME, 'readwrite');
            const store = tx.objectStore(STORE_NAME);
            entries.forEach(([k, v]) => store.put(v, k));
            tx.oncomplete = () => resolve();
            tx.onerror = () => reject(tx.error);
            tx.onabort = () => reject(tx.error);
        });

        _safeLocalSet(MIGRATION_FLAG, '1');
    } catch (e) {
        // 迁移失败不影响应用运行：localStorage 仍可作为降级存储
        try { console.warn('[NodeCraftAI] IndexedDB 迁移失败，将继续使用 localStorage', e); } catch (_) {}
    }
}
