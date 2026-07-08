"""
模型能力注册表 - 统一管理模型的能力信息

整合之前分散在 API模型客户端.py 和 聊天路由.py 中的 _MODEL_CONTEXT_LIMITS
"""
import logging
logger = logging.getLogger("NodeCraftAI.模型能力注册表")


class 模型能力注册表:
    """统一管理多个模型的能力、上下文窗口和推荐用途"""
    
    # 内置模型能力映射
    _内置模型 = {
        "deepseek-v4-pro": {
            "上下文窗口": 65536,
            "擅长": ["编码", "推理", "通用"],
            "成本": "中",
            "描述": "DeepSeek 旗舰模型，编码和推理能力强"
        },
        "deepseek-chat": {
            "上下文窗口": 65536,
            "擅长": ["通用", "对话"],
            "成本": "低",
            "描述": "DeepSeek 对话模型，性价比高"
        },
        "deepseek-reasoner": {
            "上下文窗口": 65536,
            "擅长": ["推理", "数学", "优化"],
            "成本": "高",
            "描述": "DeepSeek 推理模型，擅长复杂逻辑分析"
        },
        "deepseek-v3": {
            "上下文窗口": 65536,
            "擅长": ["编码", "通用"],
            "成本": "低",
            "描述": "DeepSeek V3，均衡的编码能力"
        },
        "gpt-4o": {
            "上下文窗口": 128000,
            "擅长": ["编码", "创意", "长文本"],
            "成本": "高",
            "描述": "OpenAI 旗舰多模态模型"
        },
        "gpt-4o-mini": {
            "上下文窗口": 128000,
            "擅长": ["通用", "对话"],
            "成本": "低",
            "描述": "OpenAI 轻量模型，快速且经济"
        },
        "gpt-4-turbo": {
            "上下文窗口": 128000,
            "擅长": ["编码", "推理"],
            "成本": "高",
            "描述": "OpenAI GPT-4 Turbo"
        },
        "claude-3-5-sonnet": {
            "上下文窗口": 200000,
            "擅长": ["编码", "长文本", "分析"],
            "成本": "高",
            "描述": "Anthropic Claude 3.5，超长上下文"
        },
        "claude-3-haiku": {
            "上下文窗口": 200000,
            "擅长": ["通用", "快速响应"],
            "成本": "低",
            "描述": "Anthropic Claude Haiku，快速轻量"
        },
        "qwen-plus": {
            "上下文窗口": 131072,
            "擅长": ["编码", "中文", "通用"],
            "成本": "中",
            "描述": "通义千问 Plus，中文能力强"
        },
        "qwen-max": {
            "上下文窗口": 131072,
            "擅长": ["编码", "推理", "中文"],
            "成本": "高",
            "描述": "通义千问 Max，最强推理"
        },
    }
    
    # 默认保守值
    _默认上下文窗口 = 32000
    
    def __init__(self):
        self._自定义模型 = {}
    
    def 注册模型(self, model_name: str, 能力信息: dict):
        """注册自定义模型"""
        self._自定义模型[model_name] = 能力信息
        logger.info(f"注册自定义模型: {model_name}")
    
    def 获取上下文窗口(self, model_name: str) -> int:
        """获取模型的上下文窗口大小"""
        info = self._查找模型(model_name)
        return info.get("上下文窗口", self._默认上下文窗口) if info else self._默认上下文窗口
    
    def 获取推荐模型(self, task_type: str, available_models: list = None) -> str:
        """根据任务类型获取推荐模型
        
        Args:
            task_type: 任务类型（编码/优化/分析/通用）
            available_models: 可用模型列表（为空则返回内置推荐）
        
        Returns:
            推荐的模型名称，无匹配返回空字符串
        """
        if not available_models:
            # 返回内置推荐中的最佳匹配
            for name, info in self._内置模型.items():
                if task_type in info.get("擅长", []):
                    return name
            return ""
        
        # 从可用模型中选择
        best_model = ""
        best_score = 0
        for model in available_models:
            info = self._查找模型(model)
            if info and task_type in info.get("擅长", []):
                # 优先选成本低的
                cost = info.get("成本", "高")
                score = 3 if cost == "低" else 2 if cost == "中" else 1
                if score > best_score:
                    best_score = score
                    best_model = model
        
        return best_model
    
    def 列出可用模型(self) -> list:
        """列出所有可用模型及其能力"""
        all_models = {}
        all_models.update(self._内置模型)
        all_models.update(self._自定义模型)
        return [
            {"name": name, **info}
            for name, info in all_models.items()
        ]
    
    def 获取模型信息(self, model_name: str) -> dict:
        """获取模型信息（公开接口，内部委托 _查找模型）"""
        return self._查找模型(model_name)

    def _查找模型(self, model_name: str) -> dict:
        """查找模型信息（精确匹配 + 前缀匹配）"""
        if not model_name:
            return None
        
        # 精确匹配
        if model_name in self._内置模型:
            return self._内置模型[model_name]
        if model_name in self._自定义模型:
            return self._自定义模型[model_name]
        
        # 前缀匹配
        for key, info in self._内置模型.items():
            if model_name.lower().startswith(key):
                return info
        for key, info in self._自定义模型.items():
            if model_name.lower().startswith(key):
                return info
        
        return None
