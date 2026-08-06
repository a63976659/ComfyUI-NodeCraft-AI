"""
知识库检索模块 — 提供 BM25 和 TFIDF 双引擎检索能力

缓存层次：L1 BM25（精确关键词） → L2 TFIDF（语义扩展） → L3 原始 Markdown（兜底）

BM25/TFIDF 选择标准：
- BM25：适用于精确关键词匹配，速度快，适合短查询
- TFIDF：适用于语义相似度匹配，覆盖更广

版本签名失效策略：
- 基于知识库文件内容的哈希签名
- 文件内容变化时自动失效缓存，触发重建索引
"""
import hashlib
import json
import math
import os
import pickle
import re
import sys
import threading
import time
from functools import lru_cache
from pathlib import Path

_plugin_root = str(Path(__file__).parent.parent.resolve())
if _plugin_root not in sys.path:
    sys.path.insert(0, _plugin_root)
from 后端.日志配置 import 获取日志器
from 后端.系统环境映射 import get_plugin_root
from 智能体.向量检索器 import TFIDF检索器

logger = 获取日志器("知识库检索")


# ─── ComfyUI 同义词表与术语词典（模块常量） ──────────────

# 同义词映射（双向扩展）：用于查询时自动扩充等价关键词
# 中文键服务中文查询扩英文术语；英文键服务英文对话扩中文正文词（知识库正文为中文）
# 注意：_扩展查询 只取每条前 2 个同义词，最重要的词放前面
SYNONYMS = {
    # ── 中文 → 英文术语 ──
    "自定义节点": ["custom node", "custom_node", "节点"],
    "custom node": ["自定义节点", "custom_node", "节点"],
    "输入": ["input", "INPUT_TYPES", "输入类型"],
    "输出": ["output", "RETURN_TYPES", "输出类型", "返回类型"],
    "工作流": ["workflow", "流程"],
    "模型": ["model", "MODEL", "checkpoint"],
    "图片": ["image", "IMAGE", "图像"],
    "潜空间": ["latent", "LATENT", "隐空间"],
    "条件": ["conditioning", "CONDITIONING", "提示词"],
    "采样器": ["sampler", "KSampler", "取样器"],
    "加载器": ["loader", "加载", "load"],
    "前端": ["frontend", "widget", "UI", "界面"],
    "后端": ["backend", "server", "服务端"],
    "迁移": ["migration", "migrate", "升级", "v1到v3"],
    "脚手架": ["scaffold", "模板", "template", "项目结构"],
    # ── 英文口语 → 中文（UI 与排版）──
    "layout": ["排版", "布局"],
    "panel": ["面板", "界面"],
    "overlap": ["叠压", "重叠"],
    "widget": ["控件", "面板"],
    "slider": ["滑块", "控件"],
    "textarea": ["文本框", "多行"],
    "multiline": ["多行", "文本框"],
    "dropdown": ["下拉框", "COMBO"],
    "checkbox": ["复选框", "勾选"],
    "button": ["按钮"],
    "dialog": ["对话框", "弹窗"],
    "popup": ["弹窗", "对话框"],
    "sidebar": ["侧边栏"],
    "menu": ["菜单", "顶部菜单"],
    "toolbar": ["工具栏"],
    "status bar": ["状态栏"],
    "statusbar": ["状态栏"],
    "settings": ["设置", "设置面板"],
    "shortcut": ["快捷键"],
    "hotkey": ["快捷键"],
    "canvas": ["画布", "绘制"],
    "hidden": ["隐藏"],
    "hide": ["隐藏"],
    "preview": ["预览"],
    "tooltip": ["提示"],
    "drag": ["拖拽"],
    "click": ["点击"],
    "scroll": ["滚动"],
    "resize": ["尺寸", "高度"],
    "height": ["高度"],
    "width": ["宽度"],
    "align": ["对齐"],
    "style": ["样式"],
    "color": ["颜色"],
    "theme": ["主题"],
    "font": ["字体"],
    "background": ["背景"],
    "render": ["渲染", "绘制"],
    "draw": ["绘制"],
    # ── 英文口语 → 中文（节点开发）──
    "node": ["节点"],
    "input": ["输入"],
    "output": ["输出"],
    "image": ["图片", "图像"],
    "mask": ["遮罩", "MASK"],
    "model": ["模型"],
    "loader": ["加载器", "加载"],
    "load": ["加载"],
    "save": ["保存"],
    "register": ["注册"],
    "template": ["模板"],
    "example": ["示例"],
    "category": ["分类", "CATEGORY"],
    "slot": ["插槽", "输入"],
    "connect": ["连接"],
    "event": ["事件", "监听"],
    "listener": ["监听"],
    "callback": ["回调"],
    "execute": ["执行"],
    "frontend": ["前端", "界面"],
    "backend": ["后端", "服务端"],
    "api": ["接口"],
    "route": ["路由", "接口"],
    "endpoint": ["接口", "路由"],
    "workflow": ["工作流", "流程"],
    "package": ["打包"],
    "packaging": ["打包"],
    "publish": ["发布"],
    "install": ["安装"],
    # ── 英文口语 → 中文（问题排查与优化）──
    "error": ["报错", "错误"],
    "bug": ["问题", "报错"],
    "fix": ["修复"],
    "crash": ["崩溃", "报错"],
    "debug": ["调试", "排查"],
    "test": ["测试"],
    "performance": ["性能", "优化"],
    "slow": ["性能", "卡顿"],
    "optimize": ["优化"],
    "memory": ["内存", "显存"],
    "vram": ["显存"],
    "gpu": ["显存", "GPU"],
    "cache": ["缓存"],
    "refresh": ["刷新"],
    "update": ["更新"],
}

# ComfyUI 整词术语词典：用于精确分词，避免如"自定义节点"被切碎为 bigram
COMFYUI_TERMS = (
    "自定义节点", "输入类型", "输出类型", "返回类型",
    "潜空间", "隐空间", "采样器", "取样器", "加载器",
    "服务端", "界面", "工作流", "提示词", "脚手架", "项目结构",
    "图像", "图片", "模型", "流程", "节点",
    "INPUT_TYPES", "RETURN_TYPES", "KSampler", "CONDITIONING",
)

# 分词器版本：变更分词逻辑时递增，用于失效旧 pickle 缓存（P2-13）
_TOKENIZER_VERSION = 2


def _扩展查询(query: str) -> str:
    """将查询中的关键词扩展为同义词（O(n) 时间，n 为同义词表大小）

    取每条命中条目的前 2 个同义词追加，避免膨胀过大。
    纯英文键按整词边界匹配，避免 "tab" 误命中 "stable" 这类子串误扩展；
    含中文的键保持子串匹配（中文无词边界）。
    """
    if not query:
        return query
    query_lower = query.lower()
    expanded_terms = [query]
    for term, synonyms in SYNONYMS.items():
        term_lower = term.lower()
        if term_lower.isascii():
            if not re.search(
                r'(?<![a-z0-9_])' + re.escape(term_lower) + r'(?![a-z0-9_])',
                query_lower,
            ):
                continue
        elif term_lower not in query_lower:
            continue
        expanded_terms.extend(synonyms[:2])
    return " ".join(expanded_terms)


# ─── BM25 检索引擎 ───────────────────────────────────────

class BM25Index:
    """轻量级 BM25 检索引擎（纯 Python 实现）"""

    def __init__(self, k1=1.5, b=0.75):
        self.k1 = k1
        self.b = b
        self.documents = []      # [(doc_id, title, content)]
        self.doc_freqs = {}      # term -> document frequency
        self.doc_lengths = []    # 每个文档的词数
        self.avg_doc_length = 0
        self.inverted_index = {} # term -> [(doc_idx, term_freq)]
        self._indexed = False

    def tokenize(self, text):
        """中英文分词（英文按空格/标点，中文 bigram，叠加 ComfyUI 整词术语）

        P2-13：移除中文单字降噪（单字在中文里几乎无区分度，且抬高文档长度
        稀释 BM25 得分）；不足 2 字时回退逐字，保证单字查询仍可命中。
        """
        text_lower = text.lower()
        # 英文token
        english_tokens = re.findall(r'[a-zA-Z_][a-zA-Z0-9_]*', text_lower)
        # 中文字符
        chinese_chars = re.findall(r'[\u4e00-\u9fff]', text)
        # 中文bigram（不足 2 字回退逐字）
        if len(chinese_chars) >= 2:
            chinese_tokens = [chinese_chars[i] + chinese_chars[i + 1]
                              for i in range(len(chinese_chars) - 1)]
        else:
            chinese_tokens = chinese_chars
        # ComfyUI 整词术语精确匹配（避免被 bigram 切碎）
        term_tokens = [term.lower() for term in COMFYUI_TERMS if term.lower() in text_lower]
        return english_tokens + chinese_tokens + term_tokens

    def add_document(self, doc_id, title, content):
        """添加文档到索引"""
        self.documents.append((doc_id, title, content))
        self._indexed = False

    def build_index(self, cache_path=None):
        """构建倒排索引（支持磁盘缓存）

        Returns:
            bool: True=命中磁盘缓存直接加载；False=实际重新构建
        """
        # 尝试从缓存加载
        if cache_path is not None:
            try:
                with open(cache_path, "rb") as f:
                    cache_data = pickle.load(f)
                # 分词器版本不匹配时主动失效，走下方既有重建路径（P2-13）
                if cache_data.get("tokenizer_version") != _TOKENIZER_VERSION:
                    raise KeyError(
                        f"tokenizer_version 不匹配: "
                        f"{cache_data.get('tokenizer_version')} != {_TOKENIZER_VERSION}"
                    )
                self.documents = cache_data["documents"]
                self.inverted_index = cache_data["inverted_index"]
                self.doc_freqs = cache_data["doc_freqs"]
                self.doc_lengths = cache_data["doc_lengths"]
                self.avg_doc_length = cache_data["avg_doc_length"]
                self._indexed = True
                logger.info(f"BM25 索引从缓存加载: {cache_path.name}")
                return True
            except (OSError, pickle.UnpicklingError, EOFError, KeyError) as e:
                logger.warning(f"BM25 缓存加载失败，重新构建: {e}")

        self.inverted_index = {}
        self.doc_freqs = {}
        self.doc_lengths = []

        for doc_idx, (doc_id, title, content) in enumerate(self.documents):
            # 标题权重加倍：将标题重复拼接以提升标题中词汇的权重
            full_text = f"{title} {title} {content}"
            tokens = self.tokenize(full_text)
            self.doc_lengths.append(len(tokens))

            # 统计当前文档中每个term的词频
            term_freqs = {}
            for token in tokens:
                term_freqs[token] = term_freqs.get(token, 0) + 1

            # 更新倒排索引和文档频率
            for term, freq in term_freqs.items():
                if term not in self.inverted_index:
                    self.inverted_index[term] = []
                self.inverted_index[term].append((doc_idx, freq))
                self.doc_freqs[term] = self.doc_freqs.get(term, 0) + 1

        total_length = sum(self.doc_lengths) if self.doc_lengths else 0
        self.avg_doc_length = total_length / len(self.documents) if self.documents else 0
        self._indexed = True

        # 保存缓存
        if cache_path is not None:
            try:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_data = {
                    "tokenizer_version": _TOKENIZER_VERSION,
                    "documents": self.documents,
                    "inverted_index": self.inverted_index,
                    "doc_freqs": self.doc_freqs,
                    "doc_lengths": self.doc_lengths,
                    "avg_doc_length": self.avg_doc_length,
                }
                with open(cache_path, "wb") as f:
                    pickle.dump(cache_data, f, protocol=pickle.HIGHEST_PROTOCOL)
                logger.info(f"BM25 索引已缓存到: {cache_path.name}")
            except (OSError, pickle.PicklingError) as e:
                logger.warning(f"BM25 索引缓存保存失败: {e}")

        return False

    def _bm25_score(self, query_tokens, doc_idx):
        """计算单个文档的 BM25 得分"""
        score = 0.0
        doc_len = self.doc_lengths[doc_idx]
        n_docs = len(self.documents)

        for term in query_tokens:
            if term not in self.inverted_index:
                continue

            df = self.doc_freqs[term]
            # IDF 计算（带平滑）
            idf = math.log((n_docs - df + 0.5) / (df + 0.5) + 1.0)

            # 查找当前文档中该 term 的词频
            tf = 0
            for (idx, freq) in self.inverted_index[term]:
                if idx == doc_idx:
                    tf = freq
                    break

            if tf == 0:
                continue

            # BM25 TF 归一化
            tf_norm = (tf * (self.k1 + 1)) / (
                tf + self.k1 * (1 - self.b + self.b * doc_len / self.avg_doc_length)
            )
            score += idf * tf_norm

        return score

    def search(self, query, top_k=3, min_score=1.0):
        """
        检索最相关的文档

        Args:
            query: 查询字符串
            top_k: 返回最多几条结果
            min_score: 最低相关度阈值

        Returns:
            [(doc_id, title, score, content_snippet)] 列表
        """
        if not self._indexed:
            self.build_index()

        if not self.documents:
            return []

        query_tokens = self.tokenize(query)
        if not query_tokens:
            return []

        # 仅对包含查询词的文档计算得分（优化性能）
        candidate_docs = set()
        for term in query_tokens:
            if term in self.inverted_index:
                for (doc_idx, _) in self.inverted_index[term]:
                    candidate_docs.add(doc_idx)

        if not candidate_docs:
            return []

        # 计算候选文档得分
        scores = []
        for doc_idx in candidate_docs:
            score = self._bm25_score(query_tokens, doc_idx)
            if score >= min_score:
                scores.append((doc_idx, score))

        # 按得分排序
        scores.sort(key=lambda x: x[1], reverse=True)

        # 返回 top_k 结果
        _SNIPPET_SIZE = 1000  # 普通 snippet 长度，保留更多上下文
        results = []
        for rank, (doc_idx, score) in enumerate(scores[:top_k]):
            doc_id, title, content = self.documents[doc_idx]
            content = content or ""  # 防御空内容
            # 高相关性返回 chunk 全文（条件放宽）：
            #   - top-1 结果且得分 > 5（最相关命中通常应返回全文）
            #   - 或得分 > 10 且内容不超过 3000 字符
            if (rank == 0 and score > 5) or (score > 10 and len(content) < 3000):
                snippet = content
            else:
                snippet = content[:_SNIPPET_SIZE].strip()
                if len(content) > _SNIPPET_SIZE:
                    snippet += "\n...[更多内容已省略]"
            results.append((doc_id, title, score, snippet))

        return results


# ─── 知识库索引单例（lazy init） ────────────────────────────

_bm25_instances: dict = {"develop": None, "optimize": None, "visualize": None}
_tfidf_instances: dict = {"develop": None, "optimize": None, "visualize": None}
# 已确认无 FAQ 文档的 Tab 集合（哨兵：避免每次查询都触发空索引构建）
_bm25_no_docs: set = set()
_tfidf_no_docs: set = set()


# Tab → 本地知识库文件夹映射（前端传逻辑标识符，后端映射到落盘目录）
TAB_FOLDER_MAP = {
    "develop": "开发插件",
    "optimize": "优化插件",
    "visualize": "功能可视化",
}


def _split_markdown_sections(content: str, max_chars: int = 1200) -> list:
    """将 Markdown 文本按 ATX 标题切分为段落块

    每个段落块包含其标题行与后续正文；过长段落按 max_chars 二次切分，
    避免单块过大稀释检索权重。返回 [(section_title, section_text), ...]
    """
    if not content or not content.strip():
        return []
    heading_re = re.compile(r'^(#{1,6})\s+(.*)$')
    sections = []
    current_title = ""
    current_lines = []

    def _append(title, lines):
        text = "\n".join(lines).strip()
        if not text:
            return
        if len(text) > max_chars:
            for i in range(0, len(text), max_chars):
                chunk = text[i:i + max_chars]
                if chunk.strip():
                    sections.append((title, chunk))
        else:
            sections.append((title, text))

    for line in content.split("\n"):
        m = heading_re.match(line)
        if m:
            _append(current_title, current_lines)
            current_title = m.group(2).strip()
            current_lines = [line]
        else:
            current_lines.append(line)
    _append(current_title, current_lines)
    return sections


def _加载本地知识库Markdown(tab: str) -> list:
    """加载指定 Tab 对应本地知识库文件夹下的所有 Markdown 文档

    Tab → 文件夹映射见 TAB_FOLDER_MAP；每个 .md 文件按标题切分为多个段落块，
    doc_id 形如 kb://{文件夹}/{相对路径}#{段落序号}，供 BM25/TF-IDF 索引共用。

    返回 [(doc_id, title, content), ...]
    """
    文档列表 = []
    文件夹名 = TAB_FOLDER_MAP.get(tab)
    if not 文件夹名:
        return 文档列表
    kb_dir = Path(get_plugin_root()) / "知识库" / 文件夹名
    if not kb_dir.exists():
        return 文档列表
    for md_file in sorted(kb_dir.rglob("*.md")):
        if md_file.name.startswith("."):
            continue
        try:
            content = md_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            logger.warning(f"加载知识库文档 {md_file} 失败: {e}")
            continue
        rel = md_file.relative_to(kb_dir).as_posix()
        文件标题 = md_file.stem
        for idx, (section_title, section_text) in enumerate(_split_markdown_sections(content)):
            if section_title and section_title != 文件标题:
                标题 = f"{文件标题} - {section_title}"
            else:
                标题 = 文件标题
            doc_id = f"kb://{文件夹名}/{rel}#{idx}"
            文档列表.append((doc_id, 标题, section_text))
    return 文档列表


def _generate_index_signature(tab: str) -> str:
    """基于本地知识库 Markdown + FAQ 文件内容生成索引内容签名

    - 知识库 Markdown：hash 相对路径 + mtime + size（内容变更即失效）
    - FAQ（develop/optimize）：仅 hash 影响索引结果的字段（title/problem/solution/tags），
      排除 access_count/sync_status/updated_at 等可变元数据，
      避免访问频次写入导致每次重启缓存失效。
    """
    h = hashlib.sha256()

    # 本地知识库 Markdown 文件签名
    文件夹名 = TAB_FOLDER_MAP.get(tab)
    if 文件夹名:
        kb_dir = Path(get_plugin_root()) / "知识库" / 文件夹名
        if kb_dir.exists():
            for f in sorted(kb_dir.rglob("*.md")):
                if f.name.startswith("."):
                    continue
                try:
                    st = f.stat()
                    h.update(f.relative_to(kb_dir).as_posix().encode())
                    h.update(str(int(st.st_mtime)).encode())
                    h.update(str(st.st_size).encode())
                except OSError:
                    h.update(f.name.encode())

    # 踩坑记录 FAQ 签名（仅 develop/optimize）
    if tab in ("develop", "optimize"):
        faq_path = Path(get_plugin_root()) / "数据" / "踩坑记录"
        if faq_path.exists():
            for f in sorted(faq_path.glob("*.json")):
                if f.name.endswith(".deleted.json") or f.name.startswith("."):
                    continue
                h.update(f.name.encode())
                try:
                    with open(f, "r", encoding="utf-8") as fp:
                        data = json.load(fp)
                    # 仅 hash 影响索引结果的字段，排除可变元数据
                    h.update((data.get("title", "") or "").encode())
                    h.update((data.get("problem", "") or "").encode())
                    h.update((data.get("solution", "") or "").encode())
                    tags = data.get("tags", []) or []
                    h.update("\t".join(sorted(tags)).encode())
                except (OSError, json.JSONDecodeError):
                    h.update(str(int(f.stat().st_mtime)).encode())
    return h.hexdigest()[:12]


def _加载知识库文档(tab: str) -> list:
    """加载本地知识库 Markdown + 踩坑记录 FAQ

    返回 [(doc_id, title, content), ...]，供 BM25 和 TF-IDF 索引共用。
    - 知识库 Markdown：知识库/{开发插件|优化插件|功能可视化}/**/*.md，doc_id 前缀 kb://
    - 踩坑记录 FAQ：数据/踩坑记录/*.json（仅 develop/optimize），doc_id 前缀 faq://
    """
    文档列表 = []

    # 加载本地知识库 Markdown（按 Tab 映射文件夹）
    文档列表.extend(_加载本地知识库Markdown(tab))

    # 加载踩坑记录 FAQ（仅 develop 和 optimize Tab）
    if tab in ("develop", "optimize"):
        faq_dir = Path(get_plugin_root()) / "数据" / "踩坑记录"
        if faq_dir.exists():
            for faq_file in sorted(faq_dir.glob("*.json")):
                # 与 _generate_index_signature 过滤规则保持一致
                if faq_file.name.endswith(".deleted.json") or faq_file.name.startswith("."):
                    continue
                try:
                    with open(faq_file, "r", encoding="utf-8") as fp:
                        faq_data = json.load(fp)

                    faq_id = faq_data.get("id", faq_file.stem)
                    title = faq_data.get("title", "")
                    problem = faq_data.get("problem", "")
                    solution = faq_data.get("solution", "")
                    tags = faq_data.get("tags", []) or []

                    doc_content = f"{problem}\n{solution}"
                    if tags:
                        doc_content += "\n" + " ".join(tags)

                    doc_id = f"faq://{faq_id}"
                    文档列表.append((doc_id, title, doc_content))
                except (json.JSONDecodeError, OSError) as e:
                    logger.warning(f"加载 FAQ 文件 {faq_file} 失败: {e}")

    return 文档列表



# 知识库检索失败原因 → 中文提醒文案映射（前端常驻失败通知条依赖）
KB_FAILURE_MESSAGES = {
    "timeout": "知识库连接超时，本次回答可能不完整，请联系维护人员",
    "unavailable": "知识库服务暂时不可用，本次回答可能不完整，请联系维护人员",
    "error": "知识库连接失败，本次回答可能不完整，请联系维护人员",
}


def kb_failure_message(reason: str) -> str:
    """将失败原因映射为中文提醒文案（未知原因归为通用 error 文案）"""
    return KB_FAILURE_MESSAGES.get(reason, KB_FAILURE_MESSAGES["error"])


def _计算知识库版本签名(知识库目录: str) -> str:
    """基于知识库文件的 mtime 计算版本签名，用于判断缓存是否过期

    遍历目录下所有 .md/.txt 文件的 mtime，生成 md5 摘要前 12 位作为版本标识。
    与 _generate_index_signature 互补：后者用于缓存文件命名，
    本函数用于运行时快速判断是否需要重建索引。
    """
    try:
        mtimes = []
        for root, dirs, files in os.walk(知识库目录):
            for f in files:
                if f.endswith(('.md', '.txt')):
                    filepath = os.path.join(root, f)
                    mtimes.append(str(os.path.getmtime(filepath)))
        return hashlib.md5('|'.join(sorted(mtimes)).encode()).hexdigest()[:12]
    except Exception:
        return "unknown"


# 运行时版本签名缓存（Tab -> 签名），用于检测知识库变更
_运行时版本签名: dict = {}


def _get_bm25_index(tab: str = "develop") -> "BM25Index":
    """获取或构建指定 Tab 的 BM25 索引（lazy init）

    仅索引本地踩坑记录 FAQ（知识库 Markdown 已由云端直检）。
    FAQ 文件数为 0 时跳过构建，返回 None。
    """
    global _bm25_instances
    global _bm25_no_docs

    # 运行时版本签名检查：若知识库文件 mtime 变化则自动失效缓存
    global _运行时版本签名
    try:
        文件夹名 = TAB_FOLDER_MAP.get(tab)
        if 文件夹名:
            kb_dir = Path(get_plugin_root()) / "知识库" / 文件夹名
            if kb_dir.exists():
                当前签名 = _计算知识库版本签名(str(kb_dir))
                缓存签名 = _运行时版本签名.get(tab)
                if 缓存签名 and 缓存签名 != 当前签名 and 当前签名 != "unknown":
                    logger.info(f"[BM25] Tab {tab} 知识库文件变更（{缓存签名} -> {当前签名}），重建索引")
                    _bm25_instances[tab] = None
                    if tab in _bm25_no_docs:
                        _bm25_no_docs.discard(tab)
                _运行时版本签名[tab] = 当前签名
    except Exception as e:
        logger.debug(f"[BM25] 版本签名检查异常（忽略）: {e}")

    # 如果已构建，直接返回（失效由 invalidate_bm25_cache / invalidate_faq_index 触发）
    if _bm25_instances.get(tab) is not None:
        return _bm25_instances[tab]
    # 已确认无 FAQ 文档，不再重复构建
    if tab in _bm25_no_docs:
        return None

    # 构建新索引
    index = BM25Index()

    # 统一加载知识库文档（仅 FAQ）
    文档列表 = _加载知识库文档(tab)
    if not 文档列表:
        logger.debug(f"[BM25] Tab {tab} 无 FAQ 文档，跳过索引构建")
        _bm25_no_docs.add(tab)
        return None

    for doc_id, doc_title, doc_content in 文档列表:
        index.add_document(doc_id, doc_title, doc_content)

    # 计算缓存路径：{知识库名}_{内容签名}.pkl，基于 FAQ 文件内容签名精准失效
    cache_dir = Path(get_plugin_root()) / "数据" / "bm25_cache"
    cache_filename = f"{tab}_{_generate_index_signature(tab)}.pkl"
    cache_path = cache_dir / cache_filename

    try:
        命中缓存 = index.build_index(cache_path=cache_path)
        动作 = "就绪（缓存）" if 命中缓存 else "构建完成"
        logger.info(f"[BM25] Tab {tab} 索引{动作}，共 {len(文档列表)} 篇文档")
    except Exception as e:
        logger.warning(f"[BM25] Tab {tab} 索引构建失败: {e}")
        _bm25_no_docs.add(tab)
        return None

    # 清理同一 Tab 的旧缓存文件
    try:
        if cache_dir.exists():
            for old_cache in cache_dir.glob(f"{tab}_*.pkl"):
                if old_cache != cache_path:
                    old_cache.unlink(missing_ok=True)
    except OSError as e:
        logger.debug(f"清理旧 BM25 缓存文件失败（忽略）: {e}")

    _bm25_instances[tab] = index
    return _bm25_instances[tab]


def _get_tfidf_index(tab: str = "develop") -> "TFIDF检索器":
    """获取或构建指定 Tab 的 TF-IDF 索引（lazy init）

    仅索引本地踩坑记录 FAQ（知识库 Markdown 已由云端直检）。
    FAQ 文件数为 0 时跳过构建，返回 None。
    """
    global _tfidf_instances

    # 如果已构建，直接返回（失效由 invalidate_bm25_cache / invalidate_faq_index 触发）
    if _tfidf_instances.get(tab) is not None:
        return _tfidf_instances[tab]
    # 已确认无 FAQ 文档，不再重复构建
    global _tfidf_no_docs
    if tab in _tfidf_no_docs:
        return None

    index = TFIDF检索器()

    # 统一加载知识库文档（仅 FAQ）
    文档列表 = _加载知识库文档(tab)
    if not 文档列表:
        logger.debug(f"[TF-IDF] Tab {tab} 无 FAQ 文档，跳过索引构建")
        _tfidf_no_docs.add(tab)
        return None

    # 计算缓存路径：{知识库名}_{内容签名}.pkl，基于 FAQ 文件内容签名精准失效
    cache_dir = Path(get_plugin_root()) / "数据" / "tfidf_cache"
    cache_filename = f"{tab}_{_generate_index_signature(tab)}.pkl"
    cache_path = cache_dir / cache_filename

    try:
        命中缓存 = index.build_index(文档列表, cache_path=cache_path)
        动作 = "就绪（缓存）" if 命中缓存 else "构建完成"
        logger.info(f"[TF-IDF] Tab {tab} 索引{动作}，共 {len(文档列表)} 篇文档")
    except Exception as e:
        logger.warning(f"[TF-IDF] Tab {tab} 索引构建失败: {e}")
        _tfidf_no_docs.add(tab)
        return None

    # 清理同一 Tab 的旧缓存文件
    try:
        if cache_dir.exists():
            for old_cache in cache_dir.glob(f"{tab}_*.pkl"):
                if old_cache != cache_path:
                    old_cache.unlink(missing_ok=True)
    except OSError as e:
        logger.debug(f"清理旧 TF-IDF 缓存文件失败（忽略）: {e}")

    _tfidf_instances[tab] = index
    return _tfidf_instances[tab]


def invalidate_bm25_cache():
    """清除所有 Tab 的 BM25 和 TF-IDF 索引及 LRU 缓存，使下次检索时重建索引

    调用时机：踩坑记录 / 知识库内容发生新增、更新或删除后。
    """
    global _bm25_instances, _tfidf_instances, _bm25_no_docs, _tfidf_no_docs
    for key in _bm25_instances:
        _bm25_instances[key] = None
    for key in _tfidf_instances:
        _tfidf_instances[key] = None
    _bm25_no_docs.clear()
    _tfidf_no_docs.clear()
    try:
        _cached_bm25_search.cache_clear()
    except (AttributeError, TypeError) as e:
        logger.debug(f"BM25 缓存清除失败（忽略）: {e}")
    try:
        _cached_tfidf_search.cache_clear()
    except (AttributeError, TypeError) as e:
        logger.debug(f"TF-IDF 缓存清除失败（忽略）: {e}")
    logger.info("BM25 和 TF-IDF 索引及缓存已清除，将在下次检索时重建")


def invalidate_faq_index():
    """仅清除含 FAQ 的 Tab（develop / optimize）的 BM25 和 TF-IDF 索引及缓存

    比 invalidate_bm25_cache() 更轻量：不影响 visualize Tab 的纯知识库索引。
    调用时机：踩坑记录新增、更新或删除后。
    """
    global _bm25_instances, _tfidf_instances, _bm25_no_docs, _tfidf_no_docs
    for tab in ("develop", "optimize"):
        if tab in _bm25_instances:
            _bm25_instances[tab] = None
        if tab in _tfidf_instances:
            _tfidf_instances[tab] = None
    _bm25_no_docs.discard("develop")
    _bm25_no_docs.discard("optimize")
    _tfidf_no_docs.discard("develop")
    _tfidf_no_docs.discard("optimize")
    try:
        _cached_bm25_search.cache_clear()
    except (AttributeError, TypeError):
        pass
    try:
        _cached_tfidf_search.cache_clear()
    except (AttributeError, TypeError):
        pass
    logger.info("FAQ 相关索引已清除（develop/optimize），下次检索时重建")


@lru_cache(maxsize=256)
def _cached_bm25_search(query: str, tab: str = "develop", top_k: int = 3, min_score: float = 1.0):
    """
    带缓存的 BM25 检索（按 Tab 区分缓存 key）

    Returns:
        tuple of (doc_id, title, score, snippet) 元组；BM25 不可用时返回空元组
    """
    try:
        index = _get_bm25_index(tab)
        if index is None:
            return ()
        results = index.search(query, top_k=top_k, min_score=min_score)
        # lru_cache 需要返回可哈希类型，转为 tuple
        return tuple((doc_id, title, score, snippet) for doc_id, title, score, snippet in results)
    except Exception as e:
        logger.warning(f"BM25 检索失败，回退到空结果: {e}")
        return ()


@lru_cache(maxsize=256)
def _cached_tfidf_search(query: str, tab: str = "develop", top_k: int = 8):
    """带缓存的 TF-IDF 检索（按 Tab 区分缓存 key）

    Returns:
        tuple of (doc_id, title, score, snippet) 元组；TF-IDF 不可用时返回空元组
    """
    try:
        index = _get_tfidf_index(tab)
        if index is None:
            return ()
        results = index.search(query, top_k=top_k)
        return tuple((doc_id, title, score, snippet) for doc_id, title, score, snippet in results)
    except Exception as e:
        logger.warning(f"TF-IDF 检索失败，回退到空结果: {e}")
        return ()


def _merge_bm25_tfidf(bm25_results, tfidf_results):
    """混合 BM25 与 TF-IDF 检索结果

    加权策略：
      - BM25 分数归一化到 [0, 1]（除以最大分数）
      - TF-IDF 余弦相似度已在 [0, 1] 范围
      - 加权合并：final_score = 0.6 * bm25_norm + 0.4 * tfidf_score
      - BM25 权重更高（关键词精确匹配更重要），TF-IDF 作为语义补充
      - 混合得分 < 0.15 的低相关结果直接丢弃（避免弱相关内容撑大上下文）

    若 TF-IDF 结果为空，回退到纯 BM25 结果（保持原有分数）。
    """
    # 混合得分最低阈值（仅作用于加权合并分支；纯 BM25 回退已有 min_score 把关）
    _MIN_COMBINED_SCORE = 0.15

    if not bm25_results and not tfidf_results:
        return []

    # TF-IDF 为空时回退到纯 BM25（保持原始分数，不做归一化）
    if not tfidf_results:
        return list(bm25_results)

    # BM25 为空时使用纯 TF-IDF（余弦相似度已在 [0,1]，同样过滤低分）
    if not bm25_results:
        return [r for r in tfidf_results if r[2] >= _MIN_COMBINED_SCORE]

    # BM25 分数归一化
    max_bm25 = max(r[2] for r in bm25_results)
    if max_bm25 <= 0:
        max_bm25 = 1.0

    # 合并文档（以 doc_id 为键）
    merged = {}  # doc_id -> [doc_id, title, bm25_norm, tfidf_score, snippet]

    for doc_id, title, score, snippet in bm25_results:
        bm25_norm = score / max_bm25
        merged[doc_id] = [doc_id, title, bm25_norm, 0.0, snippet]

    for doc_id, title, score, snippet in tfidf_results:
        if doc_id in merged:
            merged[doc_id][3] = score
        else:
            merged[doc_id] = [doc_id, title, 0.0, score, snippet]

    # 加权合并并排序（低于阈值的弱相关结果直接丢弃）
    result = []
    for doc_id, title, bm25_norm, tfidf_score, snippet in merged.values():
        combined = 0.6 * bm25_norm + 0.4 * tfidf_score
        if combined < _MIN_COMBINED_SCORE:
            continue
        result.append((doc_id, title, combined, snippet))

    result.sort(key=lambda x: x[2], reverse=True)
    return result


def _提取重排关键词(query: str):
    """从原始查询中提取用于重排的关键词集合（≥2 字 中文片段 / ≥2 字 英文 token）"""
    keywords = set()
    for token in re.findall(r'[a-zA-Z_][a-zA-Z0-9_]+', query.lower()):
        keywords.add(token)
    for segment in re.findall(r'[\u4e00-\u9fff]+', query):
        if len(segment) >= 2:
            keywords.add(segment)
            # 也将整词术语加入（如"自定义节点"）
            for term in COMFYUI_TERMS:
                if term in segment:
                    keywords.add(term.lower())
    return keywords


# 流程/生命周期相关的查询关键词（用于触发结构化内容加分）
_LIFECYCLE_TERMS = {"生命周期", "执行流程", "执行顺序", "lifecycle", "flow", "workflow", "工作流"}

# 跨语言同义词（用于重排时英文查询匹配中文标题，反之亦然）
_RERANK_SYNONYMS = {
    "lifecycle": ["生命周期", "执行流程"],
    "生命周期": ["lifecycle"],
    "flow": ["流程", "执行流程"],
    "workflow": ["工作流", "流程"],
    "execute": ["执行"],
    "lazy": ["懒加载"],
    "懒加载": ["lazy", "check_lazy_status"],
    "layout": ["排版", "布局"],
    "panel": ["面板"],
    "overlap": ["叠压", "混排"],
    "widget": ["控件", "混排"],
    "settings": ["设置"],
    "frontend": ["前端"],
    "template": ["模板"],
    "node": ["节点"],
}

# 结构化内容模式（检测到这些模式表示 chunk 包含流程/步骤文档）
_STRUCT_PATTERNS = [
    (re.compile(r'[┌┐└┘│─╔╗╚╝║═▶←↑↓▼►◄]'), 2.5),   # ASCII 流程图
    (re.compile(r'(?:^|\n)\s*1\.\s+.+(?:\n\s*\d+\.\s+.+){2,}'), 2.0),  # 3+ 步编号序列
    (re.compile(r'\b(?:execute|validate_inputs|fingerprint_inputs|check_lazy_status)\b'), 1.0),  # 生命周期方法
]


def _rerank_results(query: str, results):
    """对 BM25 返回结果做二次排序：关键词匹配 + 结构化内容加权

    评分策略：
      1. 标题命中查询关键词：+1.0 / 词
      2. 内容关键词密度加权（避免大 chunk 因随手提及而虚高）
      3. 流程意图检测：当查询包含生命周期/流程相关词时，
         对包含 ASCII 流程图、编号步骤序列、生命周期方法名的 chunk 额外加分
    """
    keywords = _提取重排关键词(query)
    if not keywords:
        return list(results)

    # 检测查询是否为流程/生命周期意图
    query_lower = query.lower()
    is_lifecycle_query = any(term in query_lower for term in _LIFECYCLE_TERMS)

    # 扩展关键词的跨语言同义词（用于标题匹配）
    extended_kws = set(keywords)
    for kw in keywords:
        for syn in _RERANK_SYNONYMS.get(kw, []):
            extended_kws.add(syn.lower())

    reranked = []
    for doc_id, title, score, snippet in results:
        bonus = 0.0
        title_str = title or ""
        snippet_str = snippet or ""
        title_lower = title_str.lower()
        snippet_lower = snippet_str.lower()

        # 1. 标题命中：高权重，反映 chunk 主题（使用跨语言扩展词）
        for kw in extended_kws:
            if kw in title_lower:
                bonus += 1.0

        # 2. 内容命中：密度加权
        content_hits = sum(1 for kw in keywords if kw in snippet_lower)
        if content_hits > 0:
            content_kchars = max(len(snippet_str) / 1000, 1)
            density = content_hits / content_kchars
            bonus += min(density, 2.0)

        # 3. 流程意图 + 结构化内容加分
        if is_lifecycle_query:
            for pattern, struct_bonus in _STRUCT_PATTERNS:
                if pattern.search(snippet_str):
                    bonus += struct_bonus
            # 标题包含生命周期相关词时额外加分（确保流程文档排名靠前）
            for term in _LIFECYCLE_TERMS:
                if term in title_lower:
                    bonus += 1.5
                    break

        reranked.append((doc_id, title, score + bonus, snippet))
    reranked.sort(key=lambda x: x[2], reverse=True)
    return reranked


# ─── 主动踩坑检索 ───────────────────

# 会话级去重集合（避免同一 FAQ 在同一进程生命周期内频繁计数）
_faq_access_session_set: set = set()


def _increment_faq_access(faq_ids: list):
    """异步递增 FAQ 的 access_count（在线程池中执行，不阻塞检索）

    注意：此函数不触发缓存失效，避免循环重建索引。
    """
    faq_dir = Path(get_plugin_root()) / "数据" / "踩坑记录"
    for faq_id in faq_ids:
        # 会话去重：同一 FAQ 同进程最多计数 1 次
        if faq_id in _faq_access_session_set:
            continue
        _faq_access_session_set.add(faq_id)

        faq_file = faq_dir / f"{faq_id}.json"
        if not faq_file.exists():
            continue
        try:
            with open(faq_file, "r", encoding="utf-8") as fp:
                data = json.load(fp)
            data["access_count"] = data.get("access_count", 0) + 1
            with open(faq_file, "w", encoding="utf-8") as fp:
                json.dump(data, fp, ensure_ascii=False, indent=2)
        except (json.JSONDecodeError, OSError, IOError) as e:
            logger.debug(f"更新 FAQ 频次失败（忽略）: {faq_id}, {e}")


def retrieve_pitfalls_proactive(user_message: str, task_type: str, active_tab: str = "develop") -> str:
    """代码生成前主动检索踩坑记录，避免重复已知错误。

    仅在编码/优化任务时调用，有 30ms 延迟预算。

    策略：
      1. 使用用户消息做 BM25+TF-IDF 混合检索（复用现有索引）
      2. 仅取 faq:// 前缀结果
      3. 按 access_count + priority 加权重排（高频/高优先踩坑优先）
      4. 格式化为警告段落注入系统提示

    Args:
        user_message: 用户的输入消息
        task_type: 任务类型（"编码" / "优化"）
        active_tab: 当前标签页

    Returns:
        str: 格式化的主动踩坑段落，无结果时返回空字符串
    """
    if not user_message or not user_message.strip():
        return ""

    start_time = time.perf_counter()

    query = user_message.strip()
    # 同义词扩展
    expanded = _扩展查询(query)

    # BM25 检索（top-5，比被动模式的 top-8 少以降低延迟）
    bm25_results = _cached_bm25_search(expanded, tab=active_tab, top_k=5, min_score=1.0)

    # 超时检查
    if (time.perf_counter() - start_time) * 1000 > 30:
        logger.debug("[主动踩坑] BM25 阶段已超时，跳过 TF-IDF")
        tfidf_results = []
    else:
        tfidf_results = _cached_tfidf_search(expanded, tab=active_tab, top_k=5)

    # 混合排序
    raw_results = _merge_bm25_tfidf(bm25_results, tfidf_results)
    if not raw_results:
        return ""

    # 仅保留 FAQ 结果
    faq_results = [(doc_id, title, score, snippet)
                   for doc_id, title, score, snippet in raw_results
                   if isinstance(doc_id, str) and doc_id.startswith("faq://")]

    if not faq_results:
        return ""

    # 加载 FAQ 元数据进行加权排序
    faq_dir = Path(get_plugin_root()) / "数据" / "踩坑记录"
    weighted_results = []
    for doc_id, title, score, snippet in faq_results:
        faq_id = doc_id.replace("faq://", "")
        access_count = 0
        priority = 1.0
        faq_file = faq_dir / f"{faq_id}.json"
        if faq_file.exists():
            try:
                with open(faq_file, "r", encoding="utf-8") as fp:
                    faq_data = json.load(fp)
                access_count = faq_data.get("access_count", 0)
                priority = faq_data.get("priority", 1.0)
            except (json.JSONDecodeError, OSError):
                pass
        # 加权公式：基础分数 × 优先级 × (1 + 频次加成)
        weighted_score = score * priority * (1 + access_count * 0.05)
        weighted_results.append((doc_id, title, weighted_score, snippet, faq_id))

    # 按加权分数降序排列，取 top-3
    weighted_results.sort(key=lambda x: x[2], reverse=True)
    top_results = weighted_results[:3]

    if not top_results:
        return ""

    # 异步更新频次（线程池，不阻塞）
    hit_ids = [r[4] for r in top_results]
    try:
        threading.Thread(target=_increment_faq_access, args=(hit_ids,), daemon=True).start()
    except Exception:
        pass  # 频次追踪失败不影响主流程

    # 格式化输出
    parts = ["## ⚠️ 已知问题（主动预检）\n以下是与当前任务相关的历史踩坑记录，请在编写代码时避免这些问题："]
    for doc_id, title, score, snippet, _ in top_results:
        parts.append(
            f"### ❌ {title}\n"
            f"(相关度: {score:.2f})\n\n"
            f"{snippet}"
        )

    elapsed_ms = (time.perf_counter() - start_time) * 1000
    logger.debug(f"[主动踩坑] 检索完成，命中 {len(top_results)} 条，耗时 {elapsed_ms:.1f}ms")

    return "\n\n".join(parts)
