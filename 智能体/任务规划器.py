"""
任务规划器 - 在开始编码前对复杂/歧义任务做规划与决策确认

分两级工作：
1. 预筛选：零 LLM 调用的规则快速判断，决定是否值得触发规划。
2. 生成执行计划：调用 LLM 生成结构化规划（复杂度/步骤/风险/关键决策）。

设计参考任务分类器的轻量规则风格，JSON 解析复用 智能体.JSON修复工具，
LLM 调用复用 API模型客户端 的非流式接口 generate_response。
"""
import json
import re
import logging
from pathlib import Path

# JSON修复工具当前对外导出的函数名为 _修复截断JSON，此处以别名 修复JSON 使用
from .JSON修复工具 import _修复截断JSON as 修复JSON

logger = logging.getLogger("NodeCraftAI.任务规划器")


# ============================================================
#  防护机制（模块级）
# ============================================================
# 会话级规划计数器（用 dict 存储，key 为 session_id）
_会话规划计数 = {}  # session_id -> count
_MAX_PLANNING_PER_SESSION = 3


def 检查规划配额(session_id: str) -> bool:
    """检查该会话是否仍有规划配额（未超过 _MAX_PLANNING_PER_SESSION）。

    Args:
        session_id: 会话标识

    Returns:
        True 表示还能继续规划，False 表示已达上限
    """
    if not session_id:
        return True
    return _会话规划计数.get(session_id, 0) < _MAX_PLANNING_PER_SESSION


def 记录规划(session_id: str) -> int:
    """为指定会话累加一次规划计数。

    Args:
        session_id: 会话标识

    Returns:
        累加后的当前计数
    """
    if not session_id:
        return 0
    _会话规划计数[session_id] = _会话规划计数.get(session_id, 0) + 1
    return _会话规划计数[session_id]


def 重置规划计数(session_id: str = None):
    """重置规划计数：传入 session_id 只清该会话，否则清空全部。"""
    if session_id:
        _会话规划计数.pop(session_id, None)
    else:
        _会话规划计数.clear()


# ============================================================
#  预筛选：规则维度定义
# ============================================================
# 歧义信号词
_歧义信号词 = ["或者", "还是", "哪个好", "有几种方案", "两种", "选择"]
# 范围不确定
_范围不确定词 = ["大概", "可能需要", "整体", "全部重构", "所有文件"]
# 互斥方案（关键词 + 句式）
_互斥关键词 = ["哪种方式", "哪种方案", "方案还是", "哪个方案", "两个方案"]
_互斥句式 = re.compile(r"用.{1,12}还是.{1,12}")
# 架构变更
_架构变更词 = ["架构", "重构", "迁移", "重新设计", "重写"]

# 用户明确要求直接执行（即使命中触发词也跳过规划）
_跳过词 = ["直接做", "别问了", "不用规划", "直接执行", "马上做", "立即执行", "无需规划"]

# 文件/模块名引用识别（含中文文件名，如 任务分类器.py）
_文件引用正则 = re.compile(
    r"[\w\u4e00-\u9fa5]+\.(?:py|js|jsx|ts|tsx|css|json|md|txt|html|vue|yaml|yml)",
    re.IGNORECASE,
)

# 短消息阈值 & 高复杂度阈值
_短消息阈值 = 30
_长消息阈值 = 200


def _统计文件引用(user_message: str) -> int:
    """统计消息中出现的不重复文件名引用数量。"""
    if not user_message:
        return 0
    return len(set(m.lower() for m in _文件引用正则.findall(user_message)))


def _检测互斥方案(user_message: str) -> bool:
    """检测消息是否表达了互斥实现方案的选择困惑。"""
    if any(kw in user_message for kw in _互斥关键词):
        return True
    if _互斥句式.search(user_message):
        return True
    return False


def 预筛选(user_message: str, active_tab: str = "develop", 消息历史长度: int = 0) -> dict:
    """零 LLM 调用的快速预筛选，判断是否需要触发规划。

    触发维度（命中任意一个且置信度 >= 0.6 即触发）：
      - 歧义信号词
      - 范围不确定
      - 互斥方案
      - 高复杂度（消息>200字 且 引用 2 个以上文件/模块）
      - 架构变更

    跳过条件（即使命中触发词也不规划）：
      - active_tab 不属于 develop/optimize
      - 消息含"直接做/别问了/不用规划/直接执行"等
      - 消息历史长度 > 6（非首轮，已有充足上下文）
      - 消息很短（<30字）且只涉及单文件操作

    Args:
        user_message: 用户输入消息
        active_tab: 当前激活的 Tab（develop/optimize/visualize）
        消息历史长度: 当前会话已有的消息条数

    Returns:
        {"需要规划": bool, "原因": str, "置信度": float}
    """
    结果 = {"需要规划": False, "原因": "", "置信度": 0.0}

    if not user_message or not user_message.strip():
        结果["原因"] = "空消息"
        return 结果

    msg = user_message.strip()
    字数 = len(msg)
    文件引用数 = _统计文件引用(msg)

    # ---------- 跳过条件（优先判断，短路返回） ----------
    if active_tab not in ("develop", "optimize"):
        结果["原因"] = f"非规划Tab（{active_tab}），跳过规划"
        return 结果

    if any(w in msg for w in _跳过词):
        结果["原因"] = "用户明确要求直接执行"
        return 结果

    if 消息历史长度 > 6:
        结果["原因"] = "非首轮，已有充足上下文"
        return 结果

    # 仅当短消息且明确指向单个文件的具体操作时跳过；
    # 0 个文件引用属于泛化/决策类请求（如"用A还是B"），不在此跳过
    if 字数 < _短消息阈值 and 文件引用数 == 1:
        结果["原因"] = "消息简短且仅涉及单文件操作"
        return 结果

    # ---------- 触发维度打分（取最高置信度） ----------
    命中维度 = []
    置信度 = 0.0

    if any(kw in msg for kw in _歧义信号词):
        命中维度.append("歧义信号")
        置信度 = max(置信度, 0.7)

    if any(kw in msg for kw in _范围不确定词):
        命中维度.append("范围不确定")
        置信度 = max(置信度, 0.65)

    if _检测互斥方案(msg):
        命中维度.append("互斥方案")
        置信度 = max(置信度, 0.75)

    if 字数 > _长消息阈值 and 文件引用数 >= 2:
        命中维度.append("高复杂度")
        置信度 = max(置信度, 0.8)

    if any(kw in msg for kw in _架构变更词):
        命中维度.append("架构变更")
        置信度 = max(置信度, 0.7)

    # 多维度命中给予小幅加成（更有把握需要规划）
    if len(命中维度) >= 2:
        置信度 = min(1.0, 置信度 + 0.1)

    结果["置信度"] = round(置信度, 2)

    if 命中维度 and 置信度 >= 0.6:
        结果["需要规划"] = True
        结果["原因"] = "命中触发维度：" + "、".join(命中维度)
    else:
        结果["原因"] = "未命中足够强的触发维度"

    logger.debug(
        f"[预筛选] 需要规划={结果['需要规划']} 置信度={结果['置信度']} "
        f"维度={命中维度} 字数={字数} 文件引用={文件引用数}"
    )
    return 结果


# ============================================================
#  生成执行计划：LLM 规划
# ============================================================
_规划系统提示词 = """你是 ComfyUI 插件开发的项目规划助理。

## 任务
基于用户的编程需求和当前项目上下文，执行以下分析：

1. **复杂度评估**：判断任务复杂度（simple/moderate/complex）
2. **执行计划**：列出具体执行步骤（最多5步，每步一句话）
3. **风险识别**：可能影响现有功能的改动点
4. **关键决策**：如果存在以下情况，生成需要用户确认的问题：
   - 存在互斥的实现方案
   - 改动范围不确定
   - 可能的破坏性变更

## 输出格式（严格 JSON，不要包裹在代码块中）
{
  "complexity": "simple|moderate|complex",
  "plan_steps": ["步骤1", "步骤2", ...],
  "risk_notes": ["风险1", ...],
  "questions": [
    {"question": "问题文本", "options": ["选项A", "选项B"], "default": 0}
  ]
}

## 规则
- 如果任务简单明确，questions 为空数组 []
- 每个问题最多4个选项
- plan_steps 最多5步
- risk_notes 最多3条
- 全部中文输出
- 只输出 JSON，不要任何其他文字"""


def _读取项目上下文(plugin_path) -> str:
    """读取插件文件树/结构摘要，作为规划上下文。

    复用 项目上下文分析器，失败时降级为空字符串（不影响规划）。
    """
    if not plugin_path:
        return ""
    try:
        路径 = Path(plugin_path)
        if not 路径.exists():
            return ""
        from .项目上下文分析 import 项目上下文分析器
        分析器 = 项目上下文分析器()
        return 分析器.分析项目(路径, max_tokens=600) or ""
    except Exception as e:
        logger.debug(f"[生成执行计划] 读取项目上下文失败，忽略: {type(e).__name__}: {e}")
        return ""


def _规范化计划(计划: dict) -> dict:
    """对 LLM 输出的计划做字段规范化与上限裁剪。"""
    复杂度 = 计划.get("complexity", "moderate")
    if 复杂度 not in ("simple", "moderate", "complex"):
        复杂度 = "moderate"

    def _裁剪列表(值, 上限):
        if not isinstance(值, list):
            return []
        return [str(x).strip() for x in 值 if str(x).strip()][:上限]

    plan_steps = _裁剪列表(计划.get("plan_steps"), 5)
    risk_notes = _裁剪列表(计划.get("risk_notes"), 3)

    问题列表 = []
    raw_questions = 计划.get("questions")
    if isinstance(raw_questions, list):
        for q in raw_questions:
            if not isinstance(q, dict):
                continue
            问题文本 = str(q.get("question", "")).strip()
            if not 问题文本:
                continue
            选项 = q.get("options")
            选项 = [str(o).strip() for o in 选项 if str(o).strip()][:4] if isinstance(选项, list) else []
            默认 = q.get("default", 0)
            try:
                默认 = int(默认)
            except (TypeError, ValueError):
                默认 = 0
            if 选项 and not (0 <= 默认 < len(选项)):
                默认 = 0
            问题列表.append({"question": 问题文本, "options": 选项, "default": 默认})

    return {
        "complexity": 复杂度,
        "plan_steps": plan_steps,
        "risk_notes": risk_notes,
        "questions": 问题列表,
    }


async def 生成执行计划(user_message: str, plugin_path, llm_client, settings: dict = None):
    """调用 LLM 生成结构化执行计划。

    使用独立的规划系统提示词（区别于工具执行提示词），非流式调用 LLM，
    并用 JSON 修复工具容错解析可能被截断/包裹的输出。

    Args:
        user_message: 用户的编程需求
        plugin_path: 插件根目录（存在时读取文件树作为上下文）
        llm_client: LLM 客户端，需提供 async generate_response(system_prompt, messages)
        settings: 设置字典（透传，暂用于日志/未来扩展）

    Returns:
        规范化后的计划 dict；解析失败或调用异常时返回 None（触发降级）
    """
    if not user_message or not user_message.strip():
        return None
    if llm_client is None or not hasattr(llm_client, "generate_response"):
        logger.warning("[生成执行计划] llm_client 无 generate_response 方法，跳过规划")
        return None

    # 拼接项目上下文
    用户内容 = user_message.strip()
    上下文 = _读取项目上下文(plugin_path)
    if 上下文:
        用户内容 = f"{用户内容}\n\n## 当前项目上下文\n{上下文}"

    消息列表 = [{"role": "user", "content": 用户内容}]

    # 非流式调用 LLM
    try:
        原始输出 = await llm_client.generate_response(_规划系统提示词, 消息列表)
    except Exception as e:
        logger.warning(f"[生成执行计划] LLM 调用异常，降级: {type(e).__name__}: {e}")
        return None

    if not 原始输出 or not isinstance(原始输出, str):
        logger.info("[生成执行计划] LLM 返回空内容，降级")
        return None

    # 客户端错误信息通常以 [错误]/[API 错误]/[连接错误] 等方括号开头
    if 原始输出.lstrip().startswith("["):
        logger.info(f"[生成执行计划] LLM 返回错误信息，降级: {原始输出[:80]}")
        return None

    # 用 JSON 修复工具容错解析
    修复文本 = 修复JSON(原始输出)
    if not 修复文本:
        logger.info("[生成执行计划] JSON 修复失败，降级")
        return None

    try:
        计划 = json.loads(修复文本)
    except (json.JSONDecodeError, ValueError) as e:
        logger.info(f"[生成执行计划] JSON 解析失败，降级: {e}")
        return None

    if not isinstance(计划, dict):
        logger.info("[生成执行计划] 解析结果非对象，降级")
        return None

    规范计划 = _规范化计划(计划)
    logger.info(
        f"[生成执行计划] 复杂度={规范计划['complexity']} "
        f"步骤数={len(规范计划['plan_steps'])} "
        f"风险数={len(规范计划['risk_notes'])} "
        f"问题数={len(规范计划['questions'])}"
    )
    return 规范计划


__all__ = [
    "预筛选",
    "生成执行计划",
    "检查规划配额",
    "记录规划",
    "重置规划计数",
    "_会话规划计数",
    "_MAX_PLANNING_PER_SESSION",
]
