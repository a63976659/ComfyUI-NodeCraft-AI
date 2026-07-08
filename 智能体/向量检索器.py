"""轻量级 TF-IDF 向量检索器，作为 BM25 关键词检索的语义补充

设计目标：
  - 纯 Python 实现，无需额外依赖（无 numpy/sklearn）
  - TF-IDF 向量化 + 余弦相似度匹配
  - 支持磁盘缓存，与 BM25 缓存机制对齐
  - 中文逐字分词 + 英文按词分词，覆盖中英文混合文档

与 BM25 的互补关系：
  - BM25 擅长关键词精确匹配（bigram + 整词术语）
  - TF-IDF 擅长捕捉词频分布的语义相似性（向量空间模型）
  - 混合检索时 BM25 权重 0.6（精确匹配优先），TF-IDF 权重 0.4（语义补充）
"""

import math
import re
import pickle
from collections import defaultdict, Counter
from pathlib import Path

_plugin_root = str(Path(__file__).parent.parent.resolve())
import sys
if _plugin_root not in sys.path:
    sys.path.insert(0, _plugin_root)

from 后端.日志配置 import 获取日志器

logger = 获取日志器("向量检索器")


# ComfyUI 整词术语词典：用于精确分词（与 BM25 保持一致，避免重要术语被切碎）
COMFYUI_TERMS = (
    "自定义节点", "输入类型", "输出类型", "返回类型",
    "潜空间", "隐空间", "采样器", "取样器", "加载器",
    "服务端", "界面", "工作流", "提示词", "脚手架", "项目结构",
    "图像", "图片", "模型", "流程", "节点",
    "INPUT_TYPES", "RETURN_TYPES", "KSampler", "CONDITIONING",
)


class TFIDF检索器:
    """轻量级 TF-IDF 向量检索，作为 BM25 的语义补充

    使用 TF-IDF 加权 + 余弦相似度进行文档匹配，
    与 BM25 的关键词精确匹配形成互补。

    文档格式与 BM25Index 一致：(doc_id, title, content) 三元组。
    """

    def __init__(self):
        self.documents = []          # 文档列表 [(doc_id, title, content)]
        self.vocabulary = {}         # 词汇表 {词: 索引}
        self.idf_weights = {}        # IDF 权重 {词: idf值}
        self.doc_vectors = []        # 文档 TF-IDF 向量 [dict{词: 权重}]
        self.doc_norms = []          # 文档向量 L2 范数
        self._built = False

    def _分词(self, text: str) -> list:
        """中英文混合分词

        英文：按空格和标点分词，转小写
        中文：按字分词（单字模式），不依赖分词库
        ComfyUI 术语：整词匹配，避免重要术语被切碎

        示例："ComfyUI 节点开发" → ["comfyui", "节", "点", "开", "发", "节点"]
        """
        if not text:
            return []
        text_lower = text.lower()
        tokens = []
        # 英文单词
        for match in re.finditer(r'[a-zA-Z_][a-zA-Z0-9_]*', text_lower):
            tokens.append(match.group())
        # 中文字符（逐字）
        for match in re.finditer(r'[\u4e00-\u9fff]', text):
            tokens.append(match.group())
        # ComfyUI 整词术语精确匹配
        for term in COMFYUI_TERMS:
            term_lower = term.lower()
            if term_lower in text_lower:
                tokens.append(term_lower)
        return tokens

    def build_index(self, 文件列表: list, cache_path: Path = None):
        """构建 TF-IDF 索引

        Args:
            文件列表: [(doc_id, title, content), ...] 与 BM25 文档格式一致
            cache_path: 缓存文件路径（可选，传入则支持磁盘缓存）
        """
        # 尝试从缓存加载
        if cache_path is not None:
            try:
                with open(cache_path, "rb") as f:
                    cache_data = pickle.load(f)
                self.documents = cache_data["documents"]
                self.vocabulary = cache_data["vocabulary"]
                self.idf_weights = cache_data["idf_weights"]
                self.doc_vectors = cache_data["doc_vectors"]
                self.doc_norms = cache_data["doc_norms"]
                self._built = True
                logger.info(f"TF-IDF 索引从缓存加载: {cache_path.name}")
                return
            except (OSError, pickle.UnpicklingError, EOFError, KeyError) as e:
                logger.warning(f"TF-IDF 缓存加载失败，重新构建: {e}")

        self.documents = 文件列表

        if not 文件列表:
            self._built = True
            return

        # 1. 分词并构建词汇表（标题权重加倍，与 BM25 策略一致）
        doc_tokens = []
        for doc_id, title, content in 文件列表:
            full_text = f"{title} {title} {content}"
            tokens = self._分词(full_text)
            doc_tokens.append(tokens)
            for token in tokens:
                if token not in self.vocabulary:
                    self.vocabulary[token] = len(self.vocabulary)

        # 2. 计算 IDF
        doc_count = len(文件列表)
        df = defaultdict(int)  # 文档频率
        for tokens in doc_tokens:
            unique_tokens = set(tokens)
            for token in unique_tokens:
                df[token] += 1

        for token, freq in df.items():
            self.idf_weights[token] = math.log((doc_count + 1) / (freq + 1)) + 1

        # 3. 计算每个文档的 TF-IDF 向量
        self.doc_vectors = []
        self.doc_norms = []
        for tokens in doc_tokens:
            tf = Counter(tokens)
            vector = {}
            token_count = len(tokens) if tokens else 1
            for token, count in tf.items():
                tf_weight = count / token_count
                vector[token] = tf_weight * self.idf_weights.get(token, 0)

            # L2 范数
            norm = math.sqrt(sum(w ** 2 for w in vector.values())) or 1.0
            self.doc_vectors.append(vector)
            self.doc_norms.append(norm)

        self._built = True
        logger.info(
            f"TF-IDF 索引构建完成: {doc_count} 篇文档, "
            f"词汇表 {len(self.vocabulary)} 词"
        )

        # 保存缓存
        if cache_path is not None:
            try:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                with open(cache_path, "wb") as f:
                    pickle.dump({
                        "documents": self.documents,
                        "vocabulary": self.vocabulary,
                        "idf_weights": self.idf_weights,
                        "doc_vectors": self.doc_vectors,
                        "doc_norms": self.doc_norms,
                    }, f, protocol=pickle.HIGHEST_PROTOCOL)
                logger.info(f"TF-IDF 索引已缓存到: {cache_path.name}")
            except (OSError, pickle.PicklingError) as e:
                logger.warning(f"TF-IDF 索引缓存保存失败: {e}")

    def search(self, query: str, top_k: int = 5) -> list:
        """搜索查询，返回 [(doc_id, title, 相似度分数, snippet), ...]

        使用余弦相似度进行匹配，分数范围 [0, 1]。
        仅计算与查询有共同词的文档（优化性能）。
        """
        if not self._built or not self.documents:
            return []

        # 查询向量化
        query_tokens = self._分词(query)
        if not query_tokens:
            return []

        query_tf = Counter(query_tokens)
        query_vector = {}
        for token, count in query_tf.items():
            tf_weight = count / len(query_tokens)
            idf = self.idf_weights.get(
                token, math.log((len(self.documents) + 1) / 1) + 1
            )
            query_vector[token] = tf_weight * idf

        query_norm = math.sqrt(sum(w ** 2 for w in query_vector.values())) or 1.0

        # 计算余弦相似度
        scores = []
        for i, doc_vec in enumerate(self.doc_vectors):
            # 点积（仅遍历查询向量中的词，优化性能）
            dot_product = sum(
                query_vector.get(token, 0) * doc_vec.get(token, 0)
                for token in query_vector
            )
            if dot_product == 0:
                continue
            # 余弦相似度
            cosine_sim = dot_product / (query_norm * self.doc_norms[i])
            doc_id, title, content = self.documents[i]
            scores.append((doc_id, title, cosine_sim, content or ""))

        # 按相似度排序并返回 top_k
        scores.sort(key=lambda x: x[2], reverse=True)
        return scores[:top_k]
