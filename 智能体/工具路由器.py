from pathlib import Path
import sys
import math
import re
import json
from functools import lru_cache

_plugin_root = str(Path(__file__).parent.parent.resolve())
if _plugin_root not in sys.path:
    sys.path.insert(0, _plugin_root)
from 后端.系统环境映射 import get_plugin_root
from 后端.文件读写操作 import (
    read_plugin_file as _read_plugin_file,
    write_plugin_file as _write_plugin_file,
    scan_plugin_file_tree as _scan_plugin_file_tree,
)
from 后端.日志配置 import 获取日志器

logger = 获取日志器("工具路由器")


# ─── 文件操作工具定义（用于优化插件模式） ───────────────
FILE_TOOLS = [
    {
        "name": "read_plugin_file",
        "description": "读取当前插件中指定文件的内容",
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "相对于插件根目录的文件路径"}
            },
            "required": ["file_path"]
        }
    },
    {
        "name": "write_plugin_file",
        "description": "修改当前插件中指定文件的内容",
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "相对于插件根目录的文件路径"},
                "content": {"type": "string", "description": "要写入的完整文件内容"}
            },
            "required": ["file_path", "content"]
        }
    },
    {
        "name": "list_plugin_files",
        "description": "列出当前插件的所有文件和目录结构",
        "parameters": {
            "type": "object",
            "properties": {}
        }
    }
]


# ─── ComfyUI 同义词表与术语词典（模块常量） ──────────────

# 同义词映射（双向扩展）：用于查询时自动扩充等价关键词
SYNONYMS = {
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
}

# ComfyUI 整词术语词典：用于精确分词，避免如"自定义节点"被切碎为 bigram
COMFYUI_TERMS = (
    "自定义节点", "输入类型", "输出类型", "返回类型",
    "潜空间", "隐空间", "采样器", "取样器", "加载器",
    "服务端", "界面", "工作流", "提示词", "脚手架", "项目结构",
    "图像", "图片", "模型", "流程", "节点",
    "INPUT_TYPES", "RETURN_TYPES", "KSampler", "CONDITIONING",
)


def _扩展查询(query: str) -> str:
    """将查询中的关键词扩展为同义词（O(n) 时间，n 为同义词表大小）

    取每条命中条目的前 2 个同义词追加，避免膨胀过大。
    """
    if not query:
        return query
    query_lower = query.lower()
    expanded_terms = [query]
    for term, synonyms in SYNONYMS.items():
        if term.lower() in query_lower:
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
        """中英文分词（英文按空格/标点，中文逐字+bigram，叠加 ComfyUI 整词术语）"""
        text_lower = text.lower()
        # 英文token
        english_tokens = re.findall(r'[a-zA-Z_][a-zA-Z0-9_]*', text_lower)
        # 中文字符
        chinese_chars = re.findall(r'[\u4e00-\u9fff]', text)
        # 中文bigram
        chinese_bigrams = [chinese_chars[i] + chinese_chars[i + 1]
                           for i in range(len(chinese_chars) - 1)]
        # ComfyUI 整词术语精确匹配（避免被 bigram 切碎）
        term_tokens = [term.lower() for term in COMFYUI_TERMS if term.lower() in text_lower]
        return english_tokens + chinese_chars + chinese_bigrams + term_tokens

    def add_document(self, doc_id, title, content):
        """添加文档到索引"""
        self.documents.append((doc_id, title, content))
        self._indexed = False

    def build_index(self):
        """构建倒排索引"""
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
        results = []
        for doc_idx, score in scores[:top_k]:
            doc_id, title, content = self.documents[doc_idx]
            # 生成摘要片段（取前300字符）
            snippet = content[:300].strip()
            if len(content) > 300:
                snippet += "..."
            results.append((doc_id, title, score, snippet))

        return results


# ─── 知识库索引单例（lazy init） ────────────────────────────

# Tab 逻辑标识符到中文文件夹名的映射（前端传入英文 tab，后端落盘为中文目录）
TAB_FOLDER_MAP = {
    "develop": "开发插件",
    "optimize": "优化插件",
    "visualize": "功能可视化",
}

_bm25_instances: dict = {"develop": None, "optimize": None, "visualize": None}
_bm25_知识库路径们: dict = {"develop": None, "optimize": None, "visualize": None}


def _split_into_chunks(file_path: Path, content: str):
    """
    将 Markdown 文档按 ## 标题分割为 chunk

    Returns:
        [(chunk_id, chunk_title, chunk_content)] 列表
    """
    relative_path = file_path.name
    chunks = []

    # 按 ## 标题分割
    sections = re.split(r'^(##\s+.+)$', content, flags=re.MULTILINE)

    if len(sections) <= 1:
        # 没有 ## 标题，整个文档作为一个 chunk
        title = file_path.stem
        chunks.append((str(file_path), title, content.strip()))
        return chunks

    # sections 格式: [前导内容, 标题1, 内容1, 标题2, 内容2, ...]
    # 处理前导内容（如果有）
    if sections[0].strip():
        chunks.append((
            f"{relative_path}#intro",
            f"{file_path.stem} - 概述",
            sections[0].strip()
        ))

    # 处理各个section
    for i in range(1, len(sections), 2):
        heading = sections[i].strip().lstrip('#').strip()
        body = sections[i + 1].strip() if i + 1 < len(sections) else ""
        if body:  # 只索引有内容的段落
            chunk_id = f"{relative_path}#{heading}"
            chunk_title = f"{file_path.stem} - {heading}"
            chunks.append((chunk_id, chunk_title, body))

    return chunks


def _get_bm25_index(tab: str = "develop") -> "BM25Index":
    """获取或构建指定 Tab 的 BM25 索引（lazy init）"""
    global _bm25_instances, _bm25_知识库路径们

    # 将逻辑 tab 标识符（develop/optimize/visualize）映射为中文文件夹名
    folder_name = TAB_FOLDER_MAP.get(tab, tab)
    知识库路径 = Path(get_plugin_root()) / "知识库" / folder_name

    # 如果已构建且路径未变，直接返回
    if _bm25_instances.get(tab) is not None and _bm25_知识库路径们.get(tab) == 知识库路径:
        return _bm25_instances[tab]

    # 构建新索引
    index = BM25Index()
    _bm25_知识库路径们[tab] = 知识库路径

    # 加载对应 Tab 的知识库 Markdown 文件
    if 知识库路径.exists():
        for md_file in 知识库路径.rglob("*.md"):
            try:
                content = md_file.read_text(encoding="utf-8")
                chunks = _split_into_chunks(md_file, content)
                for chunk_id, chunk_title, chunk_content in chunks:
                    index.add_document(chunk_id, chunk_title, chunk_content)
            except (OSError, UnicodeDecodeError):
                continue

    # === 加载踩坑记录 FAQ（仅 develop 和 optimize Tab） ===
    if tab in ("develop", "optimize"):
        faq_dir = Path(get_plugin_root()) / "数据" / "踩坑记录"
        if faq_dir.exists():
            for faq_file in sorted(faq_dir.glob("*.json")):
                # 跳过墓碑文件
                if faq_file.name.endswith(".deleted.json"):
                    continue
                try:
                    with open(faq_file, "r", encoding="utf-8") as fp:
                        faq_data = json.load(fp)

                    faq_id = faq_data.get("id", faq_file.stem)
                    title = faq_data.get("title", "")
                    problem = faq_data.get("problem", "")
                    solution = faq_data.get("solution", "")
                    tags = faq_data.get("tags", []) or []

                    # 组装 FAQ 文档内容
                    doc_content = f"{problem}\n{solution}"
                    if tags:
                        doc_content += "\n" + " ".join(tags)

                    doc_id = f"faq://{faq_id}"
                    index.add_document(doc_id, title, doc_content)
                except Exception as e:
                    logger.warning(f"加载 FAQ 文件 {faq_file} 失败: {e}")

    index.build_index()
    _bm25_instances[tab] = index
    return _bm25_instances[tab]


def invalidate_bm25_cache():
    """清除所有 Tab 的 BM25 索引和 LRU 缓存，使下次检索时重建索引

    调用时机：踩坑记录 / 知识库内容发生新增、更新或删除后。
    """
    global _bm25_instances
    for key in _bm25_instances:
        _bm25_instances[key] = None
    try:
        _cached_bm25_search.cache_clear()
    except Exception:
        pass
    logger.info("BM25 索引和缓存已清除，将在下次检索时重建")


@lru_cache(maxsize=256)
def _cached_bm25_search(query: str, tab: str = "develop", top_k: int = 3, min_score: float = 1.0):
    """
    带缓存的 BM25 检索（按 Tab 区分缓存 key）

    Returns:
        tuple of (doc_id, title, score, snippet) 元组
    """
    index = _get_bm25_index(tab)
    results = index.search(query, top_k=top_k, min_score=min_score)
    # lru_cache 需要返回可哈希类型，转为 tuple
    return tuple((doc_id, title, score, snippet) for doc_id, title, score, snippet in results)


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


def _rerank_results(query: str, results):
    """对 BM25 返回结果做二次排序：基于关键词精确匹配做加权

    每命中一个原始查询关键词加 0.5 分，标题命中再额外加 0.3 分。
    """
    keywords = _提取重排关键词(query)
    if not keywords:
        return list(results)

    reranked = []
    for doc_id, title, score, snippet in results:
        bonus = 0.0
        title_lower = title.lower()
        snippet_lower = snippet.lower()
        for kw in keywords:
            if kw in title_lower:
                bonus += 0.8  # 标题命中权重更高
            elif kw in snippet_lower:
                bonus += 0.5
        reranked.append((doc_id, title, score + bonus, snippet))
    reranked.sort(key=lambda x: x[2], reverse=True)
    return reranked


# ─── 工具路由器 ─────────────────────────────────────────────

class ToolRouter:
    """知识库检索与意图路由"""

    def __init__(self):
        self.知识库路径 = Path(get_plugin_root()) / "知识库"
        self.文件工具 = FILE_TOOLS

    def retrieve_knowledge(self, user_message, active_tab="develop"):
        """
        根据用户消息使用 BM25 算法检索相关知识库文档与踩坑记录

        流程：
          1. 同义词扩展原始查询（覆盖中英文等价表述）
          2. 用扩展后查询调用带 LRU 缓存的 BM25 检索（取 top-8 候选，容纳 FAQ）
          3. 基于原始查询关键词对候选做精确匹配重排
          4. 按 doc_id 前缀分离知识库与 FAQ，分别取 top-3 / top-2 拼接

        Returns:
            str: 检索到的相关文档段落，拼接为一个字符串；无相关内容时返回空字符串
        """
        if not user_message or not user_message.strip():
            return ""

        原始查询 = user_message.strip()
        # 同义词扩展（扩展后查询作为 LRU 缓存 key，确保缓存有效）
        扩展查询 = _扩展查询(原始查询)

        # 取 top-8 候选用于二次重排，容纳 FAQ 结果
        raw_results = _cached_bm25_search(扩展查询, tab=active_tab, top_k=8, min_score=1.0)
        if not raw_results:
            return ""

        # 基于原始查询做关键词精确匹配重排
        reranked = _rerank_results(原始查询, raw_results)

        # 分离知识库与 FAQ 结果（doc_id 以 faq:// 开头视为踩坑记录）
        kb_results = []
        faq_results = []
        for r in reranked:
            doc_id = r[0]
            if isinstance(doc_id, str) and doc_id.startswith("faq://"):
                faq_results.append(r)
            else:
                kb_results.append(r)

        # 知识库取 top-3，FAQ 取 top-2
        kb_top = kb_results[:3]
        faq_top = faq_results[:2]

        parts = []

        # 格式化知识库结果（保持现有格式）
        for doc_id, title, score, snippet in kb_top:
            parts.append(
                f"## 参考文档: {title}\n"
                f"(相关度: {score:.2f})\n\n"
                f"{snippet}"
            )

        # 格式化 FAQ 结果
        if faq_top:
            faq_块 = ["## 踩坑记录参考\n以下是历史踩坑记录，请参考避免相同问题："]
            for doc_id, title, score, snippet in faq_top:
                faq_块.append(
                    f"### 踩坑记录: {title}\n"
                    f"(相关度: {score:.2f})\n\n"
                    f"{snippet}"
                )
            parts.append("\n\n".join(faq_块))

        return "\n\n---\n\n".join(parts) if parts else ""

    def get_file_tools(self):
        """获取文件操作工具定义（供优化插件模式使用）"""
        return self.文件工具


# ─── 工具执行逻辑（对接 后端/文件读写操作.py） ─────────────

def _格式化文件树(tree: list, indent: int = 0) -> str:
    """将文件树列表格式化为缩进文本"""
    lines = []
    for item in tree:
        prefix = "  " * indent
        item_type = item.get("type")
        # 兼容 "directory" 与 "dir" 两种类型标识
        if item_type in ("directory", "dir"):
            lines.append(f"{prefix}📁 {item.get('name', '')}/")
            children = item.get("children")
            if children:
                lines.append(_格式化文件树(children, indent + 1))
        else:
            lines.append(f"{prefix}📄 {item.get('name', '')}")
    return "\n".join(lines)


async def 执行工具(tool_name: str, tool_args: dict, plugin_path: str) -> str:
    """
    根据工具名和参数执行对应的文件操作。

    Args:
        tool_name: 工具名称 (read_plugin_file / write_plugin_file / list_plugin_files)
        tool_args: 工具参数字典
        plugin_path: 插件的绝对路径

    Returns:
        执行结果的文本描述
    """
    logger.info(f"执行工具: {tool_name}, 参数: {tool_args}")

    if not plugin_path:
        return "❌ 错误：未指定插件路径（plugin_path 为空），无法执行文件操作。"

    tool_args = tool_args or {}

    try:
        if tool_name == "read_plugin_file":
            file_path = tool_args.get("file_path")
            if not file_path:
                return "❌ 错误：缺少必需参数 file_path"
            success, result = _read_plugin_file(plugin_path, file_path)
            if success:
                return f"📄 文件 {file_path} 内容：\n\n{result}"
            return f"❌ 读取失败：{result}"

        if tool_name == "write_plugin_file":
            file_path = tool_args.get("file_path")
            content = tool_args.get("content")
            if not file_path:
                return "❌ 错误：缺少必需参数 file_path"
            if content is None:
                return "❌ 错误：缺少必需参数 content"
            success, message = _write_plugin_file(plugin_path, file_path, content)
            return ("✅ " if success else "❌ ") + str(message)

        if tool_name == "list_plugin_files":
            tree = _scan_plugin_file_tree(plugin_path)
            if not tree:
                return "📁 插件目录为空或不存在"
            formatted = _格式化文件树(tree)
            return f"📁 插件文件结构：\n\n{formatted}"

        return f"❌ 错误：未知的工具名称 '{tool_name}'"

    except Exception as e:
        return f"❌ 工具执行异常：{type(e).__name__}: {str(e)}"
