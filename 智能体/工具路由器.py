from pathlib import Path
import sys
import math
import re
import json
import time
import asyncio
import pickle
import threading
import hashlib
from functools import lru_cache

_plugin_root = str(Path(__file__).parent.parent.resolve())
if _plugin_root not in sys.path:
    sys.path.insert(0, _plugin_root)
from 后端.系统环境映射 import get_plugin_root
from 后端.文件读写操作 import (
    read_plugin_file as _read_plugin_file,
    write_plugin_file as _write_plugin_file,
    scan_plugin_file_tree as _scan_plugin_file_tree,
    apply_patch as _apply_patch,
    _备份文件 as _备份文件,
    search_plugin_file as _search_plugin_file,
)
from 后端.日志配置 import 获取日志器
from 智能体.向量检索器 import TFIDF检索器

logger = 获取日志器("工具路由器")


# ─── 文件操作工具定义（用于优化插件模式） ───────────────
FILE_TOOLS = [
    {
        "name": "read_plugin_file",
        "description": "读取当前插件中指定文件的内容。支持 start_line/end_line 分段读取大文件。",
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "相对于插件根目录的文件路径"},
                "start_line": {"type": "integer", "description": "起始行号（1-based，可选，默认从头开始）"},
                "end_line": {"type": "integer", "description": "结束行号（1-based，包含该行，可选，默认到文件末尾）"}
            },
            "required": ["file_path"]
        }
    },
    {
        "name": "write_plugin_file",
        "description": "全量写入当前插件中指定文件的内容（覆盖整个文件，适用于新建文件或大幅重写）",
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
        "name": "edit_file",
        "description": (
            "增量编辑文件。仅修改需要变更的部分，无需输出完整文件内容。适用于大文件的局部修改，更高效。\n"
            "补丁使用 unified diff 格式，包含 ---/+++ 文件头和 @@ hunk 头。\n"
            "每个 hunk 中：以空格开头的行是上下文（不变），以 - 开头的行是要删除的行，以 + 开头的行是要新增的行。\n"
            "格式示例:\n"
            "```\n"
            "--- a/file.py\n"
            "+++ b/file.py\n"
            "@@ -10,3 +10,4 @@\n"
            " old line\n"
            "-old line to remove\n"
            "+new line to add\n"
            "+another new line\n"
            " old line\n"
            "```"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "要编辑的文件路径（相对于插件目录）"
                },
                "patch": {
                    "type": "string",
                    "description": "Unified diff 格式的补丁内容"
                }
            },
            "required": ["file_path", "patch"]
        }
    },
    {
        "name": "search_plugin_file",
        "description": "在文件中搜索关键词或正则表达式，返回匹配行的行号和上下文。用于快速定位代码段，配合 read_plugin_file 的分段读取使用。",
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "相对于插件根目录的文件路径"},
                "pattern": {"type": "string", "description": "搜索关键词或正则表达式"},
                "use_regex": {"type": "boolean", "description": "是否使用正则表达式模式（默认false，即普通关键词搜索）"},
                "context_lines": {"type": "integer", "description": "每个匹配前后显示的上下文行数（默认3，最大10）"}
            },
            "required": ["file_path", "pattern"]
        }
    },
    {
        "name": "list_plugin_files",
        "description": "列出当前插件的所有文件和目录结构",
        "parameters": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "batch_edit",
        "description": (
            "批量编辑多个文件。支持创建新文件、修改现有文件（全量写入或增量补丁）、删除文件。"
            "适用于一次操作多个文件的场景，如创建完整的插件项目结构。单次最多 20 个文件操作。\n"
            "操作类型说明：\n"
            "- create: 创建新文件（文件已存在则跳过，不覆盖）\n"
            "- write: 全量写入文件（覆盖已有内容）\n"
            "- edit: 增量编辑（content 参数为 unified diff 补丁，格式同 edit_file 工具）\n"
            "- delete: 删除文件（删除前自动创建 .bak 备份）"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "operations": {
                    "type": "array",
                    "description": "文件操作列表",
                    "items": {
                        "type": "object",
                        "properties": {
                            "action": {
                                "type": "string",
                                "enum": ["create", "write", "edit", "delete"],
                                "description": "操作类型：create=创建新文件, write=全量写入, edit=增量补丁, delete=删除文件"
                            },
                            "file_path": {
                                "type": "string",
                                "description": "文件路径（相对于插件目录）"
                            },
                            "content": {
                                "type": "string",
                                "description": "文件内容（create/write 时必填）或 unified diff 补丁（edit 时必填）"
                            }
                        },
                        "required": ["action", "file_path"]
                    }
                }
            },
            "required": ["operations"]
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

    def build_index(self, cache_path=None):
        """构建倒排索引（支持磁盘缓存）"""
        # 尝试从缓存加载
        if cache_path is not None:
            try:
                with open(cache_path, "rb") as f:
                    cache_data = pickle.load(f)
                self.documents = cache_data["documents"]
                self.inverted_index = cache_data["inverted_index"]
                self.doc_freqs = cache_data["doc_freqs"]
                self.doc_lengths = cache_data["doc_lengths"]
                self.avg_doc_length = cache_data["avg_doc_length"]
                self._indexed = True
                logger.info(f"BM25 索引从缓存加载: {cache_path.name}")
                return
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


def _generate_index_signature(tab: str) -> str:
    """基于 FAQ 文件（踩坑记录）的索引内容生成内容签名

    知识库 Markdown 已迁移至云端直检，本地不再存储；
    签名仅基于影响索引结果的字段（title/problem/solution/tags），
    排除 access_count/sync_status/updated_at 等可变元数据，
    避免访问频次写入导致每次重启缓存失效。
    """
    h = hashlib.sha256()
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
    """仅加载本地踩坑记录 FAQ（知识库 Markdown 已迁移至云端直检）

    返回 [(doc_id, title, content), ...]，供 BM25 和 TF-IDF 索引共用。
    """
    文档列表 = []

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


def _get_current_token() -> str:
    """获取当前登录用户的 ranking_token（从 设置.json 读取）"""
    try:
        from 后端.文件读写操作 import load_settings
        return load_settings().get("ranking_token", "") or ""
    except Exception:
        return ""


# 知识库检索失败原因 → 中文提醒文案映射（前端常驻失败通知条依赖）
KB_FAILURE_MESSAGES = {
    "timeout": "知识库连接超时，本次回答可能不完整，请联系维护人员",
    "unavailable": "知识库服务暂时不可用，本次回答可能不完整，请联系维护人员",
    "error": "知识库连接失败，本次回答可能不完整，请联系维护人员",
}


def kb_failure_message(reason: str) -> str:
    """将失败原因映射为中文提醒文案（未知原因归为通用 error 文案）"""
    return KB_FAILURE_MESSAGES.get(reason, KB_FAILURE_MESSAGES["error"])


def _调用云端知识库搜索(query: str, tab: str, top_k: int = 3):
    """同步调用云端知识库搜索 API

    保留阻塞式检索与失败降级（不重试、不中断会话），仅额外上报失败信号。

    Returns:
        tuple(results, failed, reason):
            results: list of (doc_id, title, score, snippet) 元组
            failed: bool，是否检索失败
            reason: str，失败原因（timeout/unavailable/error），成功时为 ""
    """
    import requests
    try:
        # /api/open/knowledge/search 端点部署在 NCA ModelScope Space
        from 后端.计费代理 import NCA_CLOUD_URL
        from 后端.文件读写操作 import load_settings
        settings = load_settings()
        ranking_token = settings.get("ranking_token", "") or ""
        if not ranking_token:
            logger.debug("云端知识库检索跳过：无可用 token")
            # 无有效 token 视为失败（error），供上层提醒
            return [], True, "error"
        # NCA Space 双认证：X-Auth-Token(业务 ranking_token) + Authorization(网关 SDK Token)
        headers = {
            "Content-Type": "application/json",
            "X-Auth-Token": ranking_token,
        }
        sdk_token = settings.get("modelscope_sdk_token", "") or ""
        if sdk_token:
            headers["Authorization"] = f"Bearer {sdk_token}"
        resp = requests.post(
            f"{NCA_CLOUD_URL.rstrip('/')}/api/open/knowledge/search",
            json={"query": query, "tab": tab, "top_k": top_k},
            headers=headers,
            timeout=5,
        )
        if resp.status_code == 200:
            # HTTP 200 即视为成功（即使空结果）
            data = resp.json()
            results = [(r["doc_id"], r["title"], r["score"], r["snippet"])
                       for r in data.get("results", [])]
            return results, False, ""
        elif resp.status_code == 503:
            logger.debug("云端知识库索引未就绪，跳过")
            return [], True, "unavailable"
        else:
            logger.warning(f"云端知识库检索返回 {resp.status_code}")
            return [], True, "error"
    except requests.Timeout:
        logger.warning("云端知识库检索超时")
        return [], True, "timeout"
    except Exception as e:
        logger.warning(f"云端知识库检索失败: {e}")
        return [], True, "error"


def _get_bm25_index(tab: str = "develop") -> "BM25Index":
    """获取或构建指定 Tab 的 BM25 索引（lazy init）

    仅索引本地踩坑记录 FAQ（知识库 Markdown 已由云端直检）。
    FAQ 文件数为 0 时跳过构建，返回 None。
    """
    global _bm25_instances

    # 如果已构建，直接返回（失效由 invalidate_bm25_cache / invalidate_faq_index 触发）
    if _bm25_instances.get(tab) is not None:
        return _bm25_instances[tab]
    # 已确认无 FAQ 文档，不再重复构建
    global _bm25_no_docs
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
        index.build_index(cache_path=cache_path)
        logger.info(f"[BM25] Tab {tab} 索引构建完成，共 {len(文档列表)} 篇 FAQ")
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
        index.build_index(文档列表, cache_path=cache_path)
        logger.info(f"[TF-IDF] Tab {tab} 索引构建完成，共 {len(文档列表)} 篇 FAQ")
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

    若 TF-IDF 结果为空，回退到纯 BM25 结果（保持原有分数）。
    """
    if not bm25_results and not tfidf_results:
        return []

    # TF-IDF 为空时回退到纯 BM25（保持原始分数，不做归一化）
    if not tfidf_results:
        return list(bm25_results)

    # BM25 为空时使用纯 TF-IDF
    if not bm25_results:
        return list(tfidf_results)

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

    # 加权合并并排序
    result = []
    for doc_id, title, bm25_norm, tfidf_score, snippet in merged.values():
        combined = 0.6 * bm25_norm + 0.4 * tfidf_score
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


# ─── 工具路由器 ─────────────────────────────────────────────

class ToolRouter:
    """知识库检索与意图路由"""

    def __init__(self):
        self.文件工具 = FILE_TOOLS
        # 云端知识库检索失败信号（每次 retrieve_knowledge 调用会重置）
        self.kb_retrieval_failed = False
        self.kb_failure_reason = ""

    def retrieve_knowledge(self, user_message, active_tab="develop", top_k=3):
        """
        检索相关知识库文档与踩坑记录

        流程：
          1. 调用云端知识库搜索 API 获取 Markdown 知识库结果
          2. 本地 BM25 检索踩坑记录 FAQ（仅 develop/optimize）
          3. 合并格式化返回

        Args:
            top_k: 云端知识库检索返回条数（默认 3，保持向后兼容）；
                   代码审查按深度映射 quick=1/standard=3/deep=5

        Returns:
            str: 检索到的相关文档段落；无相关内容时返回空字符串
        """
        if not user_message or not user_message.strip():
            return ""

        # 每次调用开头重置失败信号
        self.kb_retrieval_failed = False
        self.kb_failure_reason = ""

        原始查询 = user_message.strip()

        # 1. 云端知识库检索（top_k 由调用方按审查深度传入，默认 3）
        云端结果, kb_failed, kb_reason = _调用云端知识库搜索(原始查询, active_tab, top_k=top_k)
        # 检索失败时置位信号（不中断会话、不重试，仅供上层提醒）
        if kb_failed:
            self.kb_retrieval_failed = True
            self.kb_failure_reason = kb_reason

        # 2. 本地 FAQ 检索（仅 develop/optimize）
        faq_结果 = []
        if active_tab in ("develop", "optimize"):
            扩展查询 = _扩展查询(原始查询)
            bm25_results = _cached_bm25_search(扩展查询, tab=active_tab, top_k=5, min_score=1.0)
            faq_结果 = [(doc_id, title, score, snippet)
                       for doc_id, title, score, snippet in bm25_results
                       if isinstance(doc_id, str) and doc_id.startswith("faq://")][:2]

        # 3. 格式化合并
        parts = []
        for doc_id, title, score, snippet in 云端结果:
            parts.append(
                f"## 参考文档: {title}\n"
                f"(相关度: {score:.2f})\n\n"
                f"{snippet}"
            )
        if faq_结果:
            faq_块 = ["## 踩坑记录参考\n以下是历史踩坑记录，请参考避免相同问题："]
            for doc_id, title, score, snippet in faq_结果:
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


# 工具执行结果最大字符数（超出则截断，避免上下文溢出）
_MAX_TOOL_RESULT_CHARS = 4000


def _截断工具结果(result: str) -> str:
    """若结果超过 _MAX_TOOL_RESULT_CHARS 字符则截断并附加提示"""
    if len(result) <= _MAX_TOOL_RESULT_CHARS:
        return result
    total = len(result)
    truncated = result[:_MAX_TOOL_RESULT_CHARS]
    return truncated + f"\n...[结果已截断，共 {total} 字符，仅显示前 {_MAX_TOOL_RESULT_CHARS} 字符]"


# 文件写操作锁（防止并发写同一文件）
_文件写锁_dict: dict[str, asyncio.Lock] = {}

def _获取文件写锁(file_path: str) -> asyncio.Lock:
    """按文件名获取独立的写锁，避免并发写同一文件"""
    if file_path not in _文件写锁_dict:
        _文件写锁_dict[file_path] = asyncio.Lock()
    return _文件写锁_dict[file_path]


def _解析安全路径(plugin_path: str, relative_path: str) -> Path:
    """解析相对路径为绝对路径并进行安全校验

    确保解析后的路径仍在插件目录范围内，防止路径穿越攻击。

    Args:
        plugin_path: 插件根目录的完整路径
        relative_path: 相对于插件根目录的文件路径

    Returns:
        Path: 解析后的安全绝对路径

    Raises:
        ValueError: 路径超出插件目录范围
    """
    plugin_root = Path(plugin_path).resolve()
    full_path = (plugin_root / relative_path).resolve()
    try:
        full_path.relative_to(plugin_root)
    except ValueError:
        raise ValueError(f"安全错误：文件路径 '{relative_path}' 超出插件目录范围")
    return full_path


async def 执行工具(tool_name: str, tool_args: dict, plugin_path: str) -> str:
    """
    根据工具名和参数执行对应的文件操作。
    写操作加锁保护，防止并行工具调用时并发写同一文件。

    Args:
        tool_name: 工具名称 (read_plugin_file / write_plugin_file / list_plugin_files)
        tool_args: 工具参数字典
        plugin_path: 插件的绝对路径

    Returns:
        执行结果的文本描述
    """
    # 语义化日志：只显示操作摘要，不输出具体内容
    from 智能体.API模型客户端 import _tool_log_summary
    logger.info(f"执行工具: {tool_name} {_tool_log_summary(tool_name, tool_args)}")

    if not plugin_path:
        return "❌ 错误：未指定插件路径（plugin_path 为空），无法执行文件操作。"

    tool_args = tool_args or {}

    # 写操作加锁保护（write_plugin_file 和 edit_file 均为写操作）
    if tool_name in ("write_plugin_file", "edit_file"):
        file_path = tool_args.get("file_path", "")
        lock = _获取文件写锁(file_path)
        async with lock:
            result = await _执行工具内部(tool_name, tool_args, plugin_path)
        # 锁释放后，若无其他等待者（锁未被占用）则从字典中清理，避免内存泄漏
        if not lock.locked():
            _文件写锁_dict.pop(file_path, None)
        return result
    elif tool_name == "batch_edit":
        # 批量操作使用专用锁，覆盖整个批量操作（非每个子操作单独加锁）
        lock = _获取文件写锁("__batch_edit__")
        async with lock:
            result = await _执行工具内部(tool_name, tool_args, plugin_path)
        if not lock.locked():
            _文件写锁_dict.pop("__batch_edit__", None)
        return result
    else:
        return await _执行工具内部(tool_name, tool_args, plugin_path)


async def _执行工具内部(tool_name: str, tool_args: dict, plugin_path: str) -> str:
    """内部工具执行逻辑（写操作由外层加锁保护）"""
    try:
        if tool_name == "read_plugin_file":
            file_path = tool_args.get("file_path")
            if not file_path:
                return "❌ 错误：缺少必需参数 file_path，请提供要读取的文件路径"
            start_line = tool_args.get("start_line")
            end_line = tool_args.get("end_line")
            success, result = _read_plugin_file(plugin_path, file_path, start_line, end_line)
            if success:
                raw = f"📄 文件 {file_path} 内容：\n\n{result}"
                return _截断工具结果(raw)
            return f"❌ 读取失败：{result}"

        if tool_name == "write_plugin_file":
            file_path = tool_args.get("file_path")
            content = tool_args.get("content")
            if not file_path:
                return "❌ 错误：缺少必需参数 file_path，请提供要写入的文件路径"
            if content is None:
                return "❌ 错误：缺少必需参数 content，请提供要写入的文件内容"
            success, message = _write_plugin_file(plugin_path, file_path, content)
            return ("✅ " if success else "❌ ") + str(message)

        if tool_name == "edit_file":
            file_path = tool_args.get("file_path")
            patch = tool_args.get("patch")
            if not file_path:
                return "❌ 错误：缺少必需参数 file_path，请提供要编辑的文件路径"
            if not patch or not patch.strip():
                return "❌ 错误：缺少必需参数 patch，请提供 unified diff 格式的补丁内容"
            # 构建完整文件路径并调用 apply_patch
            full_path = (Path(plugin_path) / file_path).resolve()
            try:
                full_path.relative_to(Path(plugin_path).resolve())
            except ValueError:
                return "❌ 错误：文件路径超出插件目录范围"
            try:
                result_content = _apply_patch(full_path, patch)
                # 统计变更行数
                added = sum(1 for line in patch.splitlines() if line.startswith('+') and not line.startswith('+++'))
                removed = sum(1 for line in patch.splitlines() if line.startswith('-') and not line.startswith('---'))
                summary = f"✅ 增量修改成功: {file_path}（新增 {added} 行，删除 {removed} 行）"
                return _截断工具结果(summary)
            except FileNotFoundError:
                return f"❌ 文件不存在: {file_path}，请先用 read_plugin_file 确认文件路径"
            except ValueError as ve:
                return f"❌ 补丁应用失败: {str(ve)}"

        if tool_name == "list_plugin_files":
            tree = _scan_plugin_file_tree(plugin_path)
            if not tree:
                return "📁 插件目录为空或不存在"
            formatted = _格式化文件树(tree)
            raw = f"📁 插件文件结构：\n\n{formatted}"
            return _截断工具结果(raw)

        if tool_name == "search_plugin_file":
            file_path = tool_args.get("file_path")
            pattern = tool_args.get("pattern")
            if not file_path:
                return "❌ 错误：缺少必需参数 file_path"
            if not pattern:
                return "❌ 错误：缺少必需参数 pattern"
            use_regex = tool_args.get("use_regex", False)
            context_lines = min(tool_args.get("context_lines", 3), 10)
            success, result = _search_plugin_file(plugin_path, file_path, pattern, use_regex, context_lines)
            if success:
                return _截断工具结果(f"🔍 {result}")
            return f"❌ 搜索失败：{result}"

        if tool_name == "batch_edit":
            operations = tool_args.get("operations", [])
            if not operations:
                return "❌ 错误：未提供任何操作"
            if len(operations) > 20:
                return f"❌ 错误：单次批量操作不能超过 20 个文件（当前: {len(operations)}）"

            results = []  # [(status, path, action, message)]
            success_count = 0
            fail_count = 0

            for i, op in enumerate(operations):
                action = op.get("action", "")
                file_path_str = op.get("file_path", "")
                content = op.get("content", "")

                if not file_path_str:
                    results.append(("❌", "(路径为空)", action, "缺少 file_path 参数"))
                    fail_count += 1
                    continue

                # 路径安全校验
                try:
                    full_path = _解析安全路径(plugin_path, file_path_str)
                except ValueError as e:
                    results.append(("❌", file_path_str, action, str(e)))
                    fail_count += 1
                    continue

                try:
                    if action == "create":
                        if full_path.exists():
                            results.append(("❌", file_path_str, "create", "文件已存在，已跳过"))
                            fail_count += 1
                            continue
                        success, message = _write_plugin_file(plugin_path, file_path_str, content)
                        if success:
                            results.append(("✅", file_path_str, "create", "已创建"))
                            success_count += 1
                        else:
                            results.append(("❌", file_path_str, "create", message))
                            fail_count += 1

                    elif action == "write":
                        success, message = _write_plugin_file(plugin_path, file_path_str, content)
                        if success:
                            results.append(("✅", file_path_str, "write", "已写入"))
                            success_count += 1
                        else:
                            results.append(("❌", file_path_str, "write", message))
                            fail_count += 1

                    elif action == "edit":
                        if not content or not content.strip():
                            results.append(("❌", file_path_str, "edit", "缺少补丁内容"))
                            fail_count += 1
                            continue
                        try:
                            _apply_patch(full_path, content)
                            added = sum(1 for line in content.splitlines()
                                        if line.startswith('+') and not line.startswith('+++'))
                            removed = sum(1 for line in content.splitlines()
                                          if line.startswith('-') and not line.startswith('---'))
                            results.append(("✅", file_path_str, "edit", f"已修改（+{added} -{removed}）"))
                            success_count += 1
                        except FileNotFoundError:
                            results.append(("❌", file_path_str, "edit", "文件不存在"))
                            fail_count += 1
                        except ValueError as ve:
                            results.append(("❌", file_path_str, "edit", str(ve)))
                            fail_count += 1

                    elif action == "delete":
                        if not full_path.exists():
                            results.append(("❌", file_path_str, "delete", "文件不存在"))
                            fail_count += 1
                            continue
                        # 删除前创建 .bak 备份
                        _备份文件(full_path)
                        try:
                            full_path.unlink()
                            results.append(("✅", file_path_str, "delete", "已删除（已备份 .bak）"))
                            success_count += 1
                        except OSError as oe:
                            results.append(("❌", file_path_str, "delete", str(oe)))
                            fail_count += 1

                    else:
                        results.append(("❌", file_path_str, action, "未知操作类型"))
                        fail_count += 1

                except (OSError, ValueError, TypeError) as e:
                    results.append(("❌", file_path_str, action, f"{type(e).__name__}: {str(e)}"))
                    fail_count += 1

            # 构建 Markdown 表格汇总
            summary = f"## 批量操作完成\n\n"
            summary += f"**成功: {success_count} | 失败: {fail_count} | 总计: {len(operations)}**\n\n"
            summary += "| # | 文件路径 | 操作 | 结果 | 状态 |\n"
            summary += "|---|---------|------|------|------|\n"
            for i, (status, path, action, message) in enumerate(results):
                summary += f"| {i + 1} | `{path}` | {action} | {message} | {status} |\n"

            return _截断工具结果(summary)

        return f"❌ 错误：未知的工具名称 '{tool_name}'"

    except (OSError, ValueError, TypeError) as e:
        return f"❌ 工具执行异常：{type(e).__name__}: {str(e)}"
