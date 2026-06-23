// ═══════════════════════════════════════════════════════════════
// i18n.js — 轻量级国际化框架（中英双语 / 可扩展）
// NodeCraft AI — Luxury Terminal / Neo-Noir Hacker
// ───────────────────────────────────────────────────────────────
// 设计目标：
//   • 零依赖、单文件、按 key 插值，不引入运行时框架
//   • 默认中文，未匹配键自动回退到中文文案；中文也无则原样返回 key
//   • 通过 localStorage 持久化语言选择，刷新后保持
//   • 切换语言时派发 'nca-language-changed' 事件，便于面板按需重渲染
// ═══════════════════════════════════════════════════════════════

const _STORAGE_KEY = 'NodeCraftAI_language';

// ─── 语言包定义 ───────────────────────────────────────────────
const 语言包 = {
    'zh-CN': {
        // ── 通用按钮 / 状态 ──────────────────────────────
        'common.confirm':       '确认',
        'common.cancel':        '取消',
        'common.save':          '保存',
        'common.delete':        '删除',
        'common.create':        '创建',
        'common.close':         '关闭',
        'common.loading':       '加载中...',
        'common.saving':        '保存中...',
        'common.creating':      '创建中...',
        'common.error':         '出错了',
        'common.success':       '成功',
        'common.retry':         '重试',
        'common.search':        '搜索',
        'common.searching':     '搜索中...',
        'common.network_error': '网络错误',
        'common.copy':          '复制',
        'common.copied':        '✓ 已复制',
        'common.ready':         '就绪',

        // ── 品牌 ─────────────────────────────────────────
        'brand.name':           '节点梦工厂',
        'brand.tooltip':        'AI 编程助手 — 快速创建 ComfyUI 插件',
        'brand.about_desc':     '🤖 AI 驱动的 ComfyUI 插件开发助手',
        'brand.enter':          '进入 →',

        // ── 标签页 ───────────────────────────────────────
        'tab.develop':          '开发插件',
        'tab.optimize':         '优化插件',
        'tab.visualize':        '功能可视化',

        // ── 会话 ─────────────────────────────────────────
        'session.new':          '新建会话',
        'session.delete':       '删除会话',
        'session.rename':       '重命名',
        'session.empty':        '暂无会话记录',
        'session.label':        '会话',
        'session.deleted':      '会话已删除',
        'session.deleted_with_folder': '会话和文件夹已删除',
        'session.confirm_delete': '确定删除「{title}」？',
        'session.delete_only':  '仅删除会话',
        'session.delete_with_folder': '删除会话和文件夹',
        'session.confirm_delete_folder': '⚠ 危险操作！确定要同时删除文件夹「{folder}」吗？该目录下所有文件将被永久删除，不可恢复！',
        'session.delete_back':  '返回',
        'session.delete_confirm_btn': '确认删除',
        'session.untitled':     '未命名',
        'session.search_placeholder': '搜索会话...',
        'session.no_match':     '— 无匹配 —',
        'session.cancel':       '取消当前会话',
        'session.package':      '打包插件',
        'session.default_develop':   '新会话',
        'session.default_optimize':  '插件优化会话',
        'session.default_visualize': '功能可视化会话',
        'project.new_or_template':   '新建项目 / 从模板创建',

        // ── 聊天 ─────────────────────────────────────────
        'chat.placeholder':     '描述你想创建的节点功能...',
        'chat.send':            '发送',
        'chat.stop':            '停止生成',
        'chat.thinking':        '思考中...',
        'chat.timeout':         '请求超时，请检查网络连接',
        'chat.attach':          '添加文件',
        'chat.welcome_subtitle': '高级 ComfyUI 插件开发助手。选择已有项目或开始对话。',
        'chat.attach_max':      '最多只能添加 {max} 个文件',
        'chat.vision_warning':  '⚠ 当前模型不支持图片分析，图片将仅作为文件名传递',

        // ── 设置 ─────────────────────────────────────────
        'settings.title':       '⚙ 设置',
        'settings.tab.model':   '📦 模型设置',
        'settings.tab.github':  '🔗 GitHub',
        'settings.tab.monitor': '📊 监控',
        'settings.model_source': '模型来源',
        'settings.local':       '本地',
        'settings.api':         'API',
        'settings.local_model': '本地模型',
        'settings.model_path':  '模型路径',
        'settings.default_path': '↺ 默认路径',
        'settings.api_config':  'API 配置',
        'settings.base_url':    '接口地址',
        'settings.model_name':  '模型',
        'settings.api_key':     'API 密钥',
        'settings.gen_params':  '生成参数',
        'settings.temperature': '温度',
        'settings.max_tokens':  '最大令牌',
        'settings.market':      '🏪 模型市场',
        'settings.market_search_placeholder': '搜索 HuggingFace 模型...',
        'settings.save':        '保存设置',
        'settings.saved':       '✓ 设置已保存',
        'settings.language':    '语言',
        'settings.language_section': '🌐 界面语言',

        // ── 项目 ─────────────────────────────────────────
        'project.empty':        '📁 未选择项目',
        'project.new':          '⚡ 新建项目',
        'project.create':       '创建插件',
        'project.from_template': '📋 从模板创建',
        'project.created':      '✓ 项目已创建',
        'project.name_label':   '插件名称',
        'project.name_placeholder': 'my-custom-node',
        'project.name_invalid': '仅允许英文字母、数字、下划线、短横线',
        'project.name_required': '请输入合法的插件名称',
        'project.create_failed': '创建失败',
        'project.sync':         '⬆ 同步',
        'project.sync_tip':     '同步到 GitHub',
        'project.package':      '📦 打包',
        'project.package_tip':  '打包插件为 zip',

        // ── 可视化 ───────────────────────────────────────
        'visual.analyze':       '开始分析',
        'visual.loading3d':     '正在加载 3D 可视化库...',

        // ── 账号 ─────────────────────────────────────────
        'account.title':        '✦ 账号',

        // ── 监控 ─────────────────────────────────────────
        'monitor.qps':          'QPS',
        'monitor.p90':          'P90 延迟',
        'monitor.error_rate':   '错误率',
        'monitor.connections':  '连接数',
        'monitor.inferences':   '推理/分钟',
        'monitor.uptime':       '运行时间',
    },

    'en': {
        // ── Common ───────────────────────────────────────
        'common.confirm':       'Confirm',
        'common.cancel':        'Cancel',
        'common.save':          'Save',
        'common.delete':        'Delete',
        'common.create':        'Create',
        'common.close':         'Close',
        'common.loading':       'Loading...',
        'common.saving':        'Saving...',
        'common.creating':      'Creating...',
        'common.error':         'Error',
        'common.success':       'Success',
        'common.retry':         'Retry',
        'common.search':        'Search',
        'common.searching':     'Searching...',
        'common.network_error': 'Network error',
        'common.copy':          'Copy',
        'common.copied':        '✓ Copied',
        'common.ready':         'Ready',

        // ── Brand ────────────────────────────────────────
        'brand.name':           'NodeCraft AI',
        'brand.tooltip':        'AI Coding Assistant — Build ComfyUI Plugins Fast',
        'brand.about_desc':     '🤖 AI-powered ComfyUI plugin development assistant',
        'brand.enter':          'Enter →',

        // ── Tabs ─────────────────────────────────────────
        'tab.develop':          'Develop',
        'tab.optimize':         'Optimize',
        'tab.visualize':        'Visualize',

        // ── Session ──────────────────────────────────────
        'session.new':          'New Session',
        'session.delete':       'Delete Session',
        'session.rename':       'Rename',
        'session.empty':        'No sessions yet',
        'session.label':        'Sessions',
        'session.deleted':      'Session deleted',
        'session.deleted_with_folder': 'Session and folder deleted',
        'session.confirm_delete': 'Delete "{title}"?',
        'session.delete_only':  'Delete session only',
        'session.delete_with_folder': 'Delete session & folder',
        'session.confirm_delete_folder': "⚠ DANGER! Delete folder '{folder}' too? All files inside will be permanently removed!",
        'session.delete_back':  'Back',
        'session.delete_confirm_btn': 'Confirm delete',
        'session.untitled':     'Untitled',
        'session.search_placeholder': 'Search sessions...',
        'session.no_match':     '— No matches —',
        'session.cancel':       'Cancel session',
        'session.package':      'Package plugin',
        'session.default_develop':   'New Session',
        'session.default_optimize':  'Plugin Optimization',
        'session.default_visualize': 'Feature Visualization',
        'project.new_or_template':   'New Project / From Template',

        // ── Chat ─────────────────────────────────────────
        'chat.placeholder':     'Describe the node you want to build...',
        'chat.send':            'Send',
        'chat.stop':            'Stop',
        'chat.thinking':        'Thinking...',
        'chat.timeout':         'Request timeout, please check your network',
        'chat.attach':          'Attach files',
        'chat.welcome_subtitle': 'Advanced ComfyUI plugin assistant. Pick a project or start a chat.',
        'chat.attach_max':      'You can attach up to {max} files',
        'chat.vision_warning':  '⚠ Current model does not support image analysis; images will be passed as filenames only',

        // ── Settings ─────────────────────────────────────
        'settings.title':       '⚙ Settings',
        'settings.tab.model':   '📦 Model',
        'settings.tab.github':  '🔗 GitHub',
        'settings.tab.monitor': '📊 Monitor',
        'settings.model_source': 'Model Source',
        'settings.local':       'Local',
        'settings.api':         'API',
        'settings.local_model': 'Local Model',
        'settings.model_path':  'Model Path',
        'settings.default_path': '↺ Default Path',
        'settings.api_config':  'API Config',
        'settings.base_url':    'Base URL',
        'settings.model_name':  'Model',
        'settings.api_key':     'API Key',
        'settings.gen_params':  'Generation',
        'settings.temperature': 'Temperature',
        'settings.max_tokens':  'Max Tokens',
        'settings.market':      '🏪 Model Market',
        'settings.market_search_placeholder': 'Search HuggingFace models...',
        'settings.save':        'Save Settings',
        'settings.saved':       '✓ Settings saved',
        'settings.language':    'Language',
        'settings.language_section': '🌐 Interface Language',

        // ── Project ──────────────────────────────────────
        'project.empty':        '📁 No project',
        'project.new':          '⚡ New Project',
        'project.create':       'Create Plugin',
        'project.from_template': '📋 From Template',
        'project.created':      '✓ Project created',
        'project.name_label':   'Plugin Name',
        'project.name_placeholder': 'my-custom-node',
        'project.name_invalid': 'Only letters, digits, underscore and hyphen allowed',
        'project.name_required': 'Please enter a valid plugin name',
        'project.create_failed': 'Failed to create',
        'project.sync':         '⬆ Sync',
        'project.sync_tip':     'Sync to GitHub',
        'project.package':      '📦 Package',
        'project.package_tip':  'Package as zip',

        // ── Visualize ────────────────────────────────────
        'visual.analyze':       'Start Analysis',
        'visual.loading3d':     'Loading 3D visualization...',

        // ── Account ──────────────────────────────────────
        'account.title':        '✦ Account',

        // ── Monitor ──────────────────────────────────────
        'monitor.qps':          'QPS',
        'monitor.p90':          'P90 Latency',
        'monitor.error_rate':   'Error Rate',
        'monitor.connections':  'Connections',
        'monitor.inferences':   'Inferences/min',
        'monitor.uptime':       'Uptime',
    },
};

// ─── 当前语言（从 localStorage 读取，默认中文） ──────────────────
let 当前语言 = (() => {
    try {
        const 存储值 = localStorage.getItem(_STORAGE_KEY);
        if (存储值 && 语言包[存储值]) return 存储值;
    } catch (_) { /* localStorage 不可用时静默回退 */ }
    return 'zh-CN';
})();

/**
 * 翻译函数
 * @param {string} key - 语言包键名（如 'common.confirm'）
 * @param {object} [params] - 插值参数 {name: 'xxx'}
 * @returns {string}
 */
export function t(key, params) {
    const pack = 语言包[当前语言] || 语言包['zh-CN'];
    let text = pack[key];
    if (text == null) text = 语言包['zh-CN'][key];
    if (text == null) text = key;

    if (params && typeof params === 'object') {
        for (const [k, v] of Object.entries(params)) {
            text = text.replace(new RegExp(`\\{${k}\\}`, 'g'), v);
        }
    }
    return text;
}

/**
 * 切换语言并派发全局事件（便于面板订阅刷新）
 * @param {string} lang - 语言代码（'zh-CN' | 'en'）
 */
export function 设置语言(lang) {
    if (!语言包[lang]) return false;
    if (lang === 当前语言) return true;
    当前语言 = lang;
    try { localStorage.setItem(_STORAGE_KEY, lang); } catch (_) {}
    try {
        window.dispatchEvent(new CustomEvent('nca-language-changed', { detail: { lang } }));
    } catch (_) {}
    return true;
}

export function 获取当前语言() {
    return 当前语言;
}

/**
 * 在 zh-CN / en 之间切换并返回切换后的语言代码
 */
export function 切换语言() {
    const next = 当前语言 === 'zh-CN' ? 'en' : 'zh-CN';
    设置语言(next);
    return next;
}

export function 获取支持语言列表() {
    return [
        { code: 'zh-CN', name: '中文' },
        { code: 'en',    name: 'English' },
    ];
}

/**
 * 注册语言切换监听器，返回取消函数
 * @param {(lang:string)=>void} handler
 */
export function 监听语言切换(handler) {
    const wrapper = (e) => handler(e?.detail?.lang || 当前语言);
    window.addEventListener('nca-language-changed', wrapper);
    return () => window.removeEventListener('nca-language-changed', wrapper);
}
