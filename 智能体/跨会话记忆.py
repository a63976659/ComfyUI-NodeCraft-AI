"""跨会话记忆系统

持久化用户偏好和项目上下文，让记忆在会话之间延续。

记忆数据存储在 数据/记忆/跨会话记忆.json，结构如下：
{
    "全局记忆": {
        "用户偏好": [{"key": "...", "value": "...", "timestamp": "..."}],
        "常见问题": [{"question": "...", "solution": "...", "timestamp": "..."}]
    },
    "插件记忆": {
        "MyPlugin": {
            "项目信息": {"名称": "...", "节点数": 3, "技术栈": [...], "最后活跃": "..."},
            "上下文": [{"content": "...", "timestamp": "..."}]
        }
    }
}
"""

import json
import os
import re
import sys
import tempfile
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path
from threading import Lock

_plugin_root = str(Path(__file__).parent.parent.resolve())
if _plugin_root not in sys.path:
    sys.path.insert(0, _plugin_root)
from 后端.日志配置 import 获取日志器

logger = 获取日志器("跨会话记忆")

# 记忆条目上限
_MAX_GLOBAL_PREFERENCES = 50
_MAX_GLOBAL_FAQS = 20
_MAX_PLUGIN_CONTEXTS = 20

# 注入文本的最大字符数（约 500 token）
_MAX_INJECTION_CHARS = 2000

# ─── 自动提取关键词 ────────────────────────────────────────

# 用户偏好关键词
_PREFERENCE_KEYWORDS = [
    "我喜欢", "我习惯", "我偏好", "请记住", "默认使用",
    "请默认", "以后都用", "一直用", "倾向于", "更喜欢",
]

# 问题解决关键词
_SOLUTION_KEYWORDS = [
    "解决了", "修复了", "搞定了", "已修复", "已解决",
    "问题解决了", "成功修复", "fix了",
]

# 技术栈关键词
_TECH_STACK_KEYWORDS = [
    "PyTorch", "torch", "numpy", "aiohttp", "requests",
    "PIL", "cv2", "opencv", "transformers", "accelerate",
    "comfy_api", "comfyui", "folder_paths", "execution",
]

# 插件名提取模式
_PLUGIN_NAME_PATTERN = re.compile(
    r'(?:custom_nodes[/\\])([a-zA-Z0-9_-]+)', re.IGNORECASE
)


class 跨会话记忆管理器:
    """跨会话记忆系统，持久化用户偏好和项目上下文。

    线程安全：内部使用 Lock 保护读写操作，适用于 asyncio.to_thread 调用。
    """

    def __init__(self, 记忆目录: Path):
        self.记忆目录 = 记忆目录
        self.记忆文件 = 记忆目录 / "跨会话记忆.json"
        self._记忆数据 = None  # 懒加载
        self._lock = Lock()

    # ─── 内部数据管理 ────────────────────────────────────────

    def _空结构(self) -> dict:
        """返回空的记忆数据结构"""
        return {
            "全局记忆": {
                "用户偏好": [],
                "常见问题": [],
            },
            "插件记忆": {},
        }

    def _加载记忆(self) -> dict:
        """从文件加载记忆数据（懒加载 + 线程安全）"""
        if self._记忆数据 is not None:
            return self._记忆数据

        with self._lock:
            if self._记忆数据 is not None:
                return self._记忆数据

            if not self.记忆文件.exists():
                self._记忆数据 = self._空结构()
                return self._记忆数据

            try:
                text = self.记忆文件.read_text(encoding="utf-8")
                data = json.loads(text)
                # 补全缺失的层级
                if "全局记忆" not in data:
                    data["全局记忆"] = {"用户偏好": [], "常见问题": []}
                if "用户偏好" not in data["全局记忆"]:
                    data["全局记忆"]["用户偏好"] = []
                if "常见问题" not in data["全局记忆"]:
                    data["全局记忆"]["常见问题"] = []
                if "插件记忆" not in data:
                    data["插件记忆"] = {}
                self._记忆数据 = data
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"记忆文件加载失败，使用空结构: {e}")
                self._记忆数据 = self._空结构()

            return self._记忆数据

    def _保存记忆(self):
        """保存记忆到文件（原子写入：tmp + replace）"""
        if self._记忆数据 is None:
            return

        try:
            self.记忆目录.mkdir(parents=True, exist_ok=True)
            # 原子写入：先写临时文件，再 rename 替换
            fd, tmp_path = tempfile.mkstemp(
                dir=str(self.记忆目录), suffix=".tmp", prefix="memory_"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(self._记忆数据, f, ensure_ascii=False, indent=2)
                # Windows 上 os.replace 可以原子替换
                os.replace(tmp_path, str(self.记忆文件))
            except Exception:
                # 清理临时文件
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except Exception as e:
            logger.error(f"记忆文件保存失败: {e}")

    def _now(self) -> str:
        """当前时间 ISO 格式字符串"""
        return datetime.now().isoformat(timespec="seconds")

    def _截断列表(self, lst: list, max_len: int):
        """保留最新的 max_len 条记录（删除最旧的）"""
        if len(lst) > max_len:
            del lst[:len(lst) - max_len]

    # ─── 公共 API ────────────────────────────────────────────

    def 获取记忆(self, 插件路径: str = None) -> dict:
        """获取记忆数据。

        - 如果指定插件路径，返回该插件的记忆 + 全局记忆
        - 否则只返回全局记忆
        """
        data = self._加载记忆()
        result = {"全局记忆": data["全局记忆"]}

        if 插件路径:
            插件名 = self._提取插件名(插件路径)
            if 插件名 and 插件名 in data["插件记忆"]:
                result["插件记忆"] = {插件名: data["插件记忆"][插件名]}
            else:
                result["插件记忆"] = {}

        return result

    def 记录用户偏好(self, key: str, value: str):
        """记录用户偏好（如常用模型、编程风格偏好等）"""
        if not key or not value:
            return

        with self._lock:
            data = self._加载记忆()
            偏好列表 = data["全局记忆"]["用户偏好"]

            # 如果 key 已存在则更新，否则新增
            for item in 偏好列表:
                if item.get("key") == key:
                    item["value"] = value
                    item["timestamp"] = self._now()
                    self._保存记忆()
                    logger.debug(f"更新用户偏好: {key}={value}")
                    return

            偏好列表.append({
                "key": key,
                "value": value,
                "timestamp": self._now(),
            })
            self._截断列表(偏好列表, _MAX_GLOBAL_PREFERENCES)
            self._保存记忆()
            logger.info(f"记录用户偏好: {key}={value}")

    def 记录项目上下文(self, 插件路径: str, 上下文: dict):
        """记录插件项目的上下文信息

        - 插件名称、描述
        - 主要文件结构
        - 使用的技术栈
        - 常见问题和解决方案
        """
        if not 插件路径 or not 上下文:
            return

        插件名 = self._提取插件名(插件路径)
        if not 插件名:
            return

        with self._lock:
            data = self._加载记忆()
            插件记忆 = data["插件记忆"].setdefault(插件名, {
                "项目信息": {"名称": 插件名},
                "上下文": [],
            })

            # 更新项目信息
            信息 = 插件记忆["项目信息"]
            for k, v in 上下文.items():
                if k == "上下文":
                    continue
                信息[k] = v
            信息["名称"] = 插件名
            信息["最后活跃"] = self._now()

            # 如果有上下文内容，追加到上下文列表
            上下文内容 = 上下文.get("上下文")
            if 上下文内容:
                插件记忆["上下文"].append({
                    "content": 上下文内容,
                    "timestamp": self._now(),
                })
                self._截断列表(插件记忆["上下文"], _MAX_PLUGIN_CONTEXTS)

            self._保存记忆()
            logger.info(f"记录项目上下文: {插件名}")

    def 自动提取记忆(self, 用户消息: str, AI回复: str, 插件路径: str = None):
        """从对话中自动提取有价值的信息存入记忆。

        提取策略：
        1. 用户偏好：检测 "我喜欢"/"请记住"/"默认使用" 等关键词
        2. 项目信息：检测插件名称、技术栈提及
        3. 问题解决方案：检测 "解决了"/"修复了" 等关键词

        轻量级关键词匹配，不调用 LLM，不阻塞响应。
        """
        if not 用户消息:
            return

        提取了记忆 = False
        _待上传 = None  # (问题, 方案摘要, 插件名) 或 None，锁外执行网络上传

        with self._lock:
            data = self._加载记忆()

            # 1. 提取用户偏好
            for 关键词 in _PREFERENCE_KEYWORDS:
                # 查找关键词后的内容（取关键词后到句号/换行为止的片段）
                idx = 用户消息.find(关键词)
                if idx >= 0:
                    片段 = 用户消息[idx:idx + 100]
                    # 截断到句号或换行
                    for 结束符 in ["。", "\n", "；", "!", "?"]:
                        pos = 片段.find(结束符, len(关键词))
                        if pos > 0:
                            片段 = 片段[:pos]
                    片段 = 片段.strip()
                    if len(片段) > len(关键词) + 2:
                        偏好列表 = data["全局记忆"]["用户偏好"]
                        # 避免重复
                        已存在 = any(
                            item.get("value", "").startswith(片段[:20])
                            for item in 偏好列表
                        )
                        if not 已存在:
                            偏好列表.append({
                                "key": f"自动提取-{self._now()[:10]}",
                                "value": 片段,
                                "timestamp": self._now(),
                            })
                            self._截断列表(偏好列表, _MAX_GLOBAL_PREFERENCES)
                            提取了记忆 = True
                            logger.debug(f"自动提取用户偏好: {片段[:30]}...")
                        break  # 每条消息只提取一个偏好

            # 2. 提取技术栈信息
            插件名 = ""
            if 插件路径:
                插件名 = self._提取插件名(插件路径) or ""
                if 插件名:
                    提及的技术栈 = [
                        tech for tech in _TECH_STACK_KEYWORDS
                        if tech.lower() in 用户消息.lower()
                        or tech.lower() in (AI回复 or "").lower()
                    ]
                    if 提及的技术栈:
                        插件记忆 = data["插件记忆"].setdefault(插件名, {
                            "项目信息": {"名称": 插件名},
                            "上下文": [],
                        })
                        信息 = 插件记忆["项目信息"]
                        现有技术栈 = set(信息.get("技术栈", []))
                        现有技术栈.update(提及的技术栈)
                        信息["技术栈"] = list(现有技术栈)
                        信息["名称"] = 插件名
                        信息["最后活跃"] = self._now()
                        提取了记忆 = True

            # 3. 提取问题解决方案（不依赖插件路径，任何对话均可触发）
            for 关键词 in _SOLUTION_KEYWORDS:
                idx = (AI回复 or "").find(关键词)
                if idx < 0:
                    idx = 用户消息.find(关键词)
                if idx >= 0:
                    # 尝试提取问题描述（用户消息的最后一句）
                    用户句子 = 用户消息.rstrip("。.!！?？").split("\n")
                    问题 = 用户句子[-1].strip() if 用户句子 else 用户消息[:50]
                    if len(问题) > 80:
                        问题 = 问题[:80] + "..."

                    常见问题 = data["全局记忆"]["常见问题"]
                    # 避免重复
                    已存在 = any(
                        item.get("question", "").startswith(问题[:20])
                        for item in 常见问题
                    )
                    if not 已存在 and 问题:
                        # 提取解决方案摘要
                        方案摘要 = (AI回复 or "")[:120].strip()
                        if len(AI回复 or "") > 120:
                            方案摘要 += "..."

                        常见问题.append({
                            "question": 问题,
                            "solution": 方案摘要,
                            "timestamp": self._now(),
                            "plugin": 插件名,
                        })
                        self._截断列表(常见问题, _MAX_GLOBAL_FAQS)
                        提取了记忆 = True
                        logger.debug(f"自动提取问题解决方案: {问题[:30]}...")
                        # 收集待上传数据，锁外执行网络请求避免阻塞
                        _待上传 = (问题, 方案摘要, 插件名)
                    break  # 每条消息只提取一个

            if 提取了记忆:
                self._保存记忆()

        # 锁外上传到云端（不阻塞记忆操作）
        if _待上传:
            self._上传常见问题到云端(_待上传[0], _待上传[1], _待上传[2])

    def _上传常见问题到云端(self, 问题: str, 方案: str, 插件名: str = ""):
        """将自动提取的常见问题上传到云端管理后台（待审核）

        使用 urllib 同步请求（本方法在 asyncio.to_thread 上下文中调用）。
        失败仅记录日志，不影响主流程。
        """
        try:
            from 后端.文件读写操作 import load_settings
            from 后端.计费代理 import NCA_CLOUD_URL, NCA_PLUGIN_KEY, _获取modelscope_sdk_token

            settings = load_settings()
            user_id = settings.get("ranking_token", "")

            payload = json.dumps({
                "plugin_key": NCA_PLUGIN_KEY,
                "question": 问题,
                "solution": 方案,
                "plugin": 插件名 or "",
                "user_id": user_id or "",
            }).encode("utf-8")

            headers = {"Content-Type": "application/json"}
            sdk_token = _获取modelscope_sdk_token()
            if sdk_token:
                headers["Authorization"] = f"Bearer {sdk_token}"

            url = f"{NCA_CLOUD_URL}/api/open/faq/submit"
            logger.info(f"[跨会话记忆] 开始上传常见问题到云端: {问题[:30]}...")
            req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    resp_body = resp.read().decode("utf-8", errors="replace")
                    if resp.status == 200:
                        logger.info(f"[跨会话记忆] 常见问题已上传云端待审核: {问题[:30]}... 响应={resp_body[:200]}")
                    else:
                        logger.warning(f"[跨会话记忆] 上传常见问题HTTP {resp.status}: {resp_body[:200]}")
            except urllib.error.HTTPError as he:
                resp_body = he.read().decode("utf-8", errors="replace") if he.fp else ""
                logger.warning(f"[跨会话记忆] 上传常见问题HTTPError {he.code}: {resp_body[:300]}")
            except urllib.error.URLError as ue:
                logger.warning(f"[跨会话记忆] 上传常见问题网络错误: {ue.reason}")
        except Exception as e:
            logger.warning(f"[跨会话记忆] 上传常见问题异常（不影响使用）: {e}", exc_info=True)

    def 获取记忆注入文本(self, 插件路径: str = None) -> str:
        """生成可注入系统提示词的记忆文本。

        格式：
        [跨会话记忆]
        [用户偏好]
        - 偏好使用 DeepSeek 模型
        - 喜欢简洁的代码风格

        [项目记忆: MyPlugin]
        - 技术栈: PyTorch, aiohttp
        - 上次会话解决了节点注册问题
        """
        data = self._加载记忆()
        lines = []
        全局 = data["全局记忆"]

        # 用户偏好
        偏好列表 = 全局.get("用户偏好", [])
        if 偏好列表:
            lines.append("[用户偏好]")
            for item in 偏好列表[-10:]:  # 最多注入 10 条
                value = item.get("value", "")
                if value:
                    lines.append(f"- {value}")
            lines.append("")

        # 常见问题
        问题列表 = 全局.get("常见问题", [])
        if 问题列表:
            lines.append("[历史问题与解决方案]")
            for item in 问题列表[-5:]:  # 最多注入 5 条
                q = item.get("question", "")
                s = item.get("solution", "")
                if q:
                    lines.append(f"- Q: {q}")
                    if s:
                        lines.append(f"  A: {s}")
            lines.append("")

        # 插件记忆
        if 插件路径:
            插件名 = self._提取插件名(插件路径)
            if 插件名 and 插件名 in data["插件记忆"]:
                插件记忆 = data["插件记忆"][插件名]
                信息 = 插件记忆.get("项目信息", {})
                上下文列表 = 插件记忆.get("上下文", [])

                has_info = False
                info_lines = []
                if 信息.get("技术栈"):
                    info_lines.append(f"  技术栈: {', '.join(信息['技术栈'])}")
                    has_info = True
                if 信息.get("最后活跃"):
                    info_lines.append(f"  最后活跃: {信息['最后活跃']}")
                    has_info = True

                if has_info or 上下文列表:
                    lines.append(f"[项目记忆: {插件名}]")
                    lines.extend(info_lines)
                    for item in 上下文列表[-5:]:  # 最多注入 5 条上下文
                        content = item.get("content", "")
                        if content:
                            lines.append(f"- {content}")
                    lines.append("")

        if not lines:
            return ""

        result = "\n".join(lines)

        # 控制注入文本长度
        if len(result) > _MAX_INJECTION_CHARS:
            result = result[:_MAX_INJECTION_CHARS] + "\n...(记忆已截断)"

        return f"\n## 跨会话记忆\n{result}"

    def 清除记忆(self, 插件路径: str = None):
        """清除记忆（全局或指定插件）"""
        with self._lock:
            data = self._加载记忆()

            if 插件路径:
                插件名 = self._提取插件名(插件路径)
                if 插件名 and 插件名 in data["插件记忆"]:
                    del data["插件记忆"][插件名]
                    self._保存记忆()
                    logger.info(f"清除插件记忆: {插件名}")
            else:
                # 清除全局记忆
                data["全局记忆"] = {"用户偏好": [], "常见问题": []}
                self._保存记忆()
                logger.info("清除全局记忆")

    def 删除单条记忆(self, 类型: str, 索引: int, 插件路径: str = None) -> bool:
        """删除单条记忆条目。

        - 类型: "用户偏好" / "常见问题" / "上下文"
        - 索引: 在列表中的位置
        - 插件路径: 仅对 "上下文" 类型有效
        """
        with self._lock:
            data = self._加载记忆()

            if 类型 == "用户偏好":
                偏好列表 = data["全局记忆"]["用户偏好"]
                if 0 <= 索引 < len(偏好列表):
                    del 偏好列表[索引]
                    self._保存记忆()
                    return True
            elif 类型 == "常见问题":
                问题列表 = data["全局记忆"]["常见问题"]
                if 0 <= 索引 < len(问题列表):
                    del 问题列表[索引]
                    self._保存记忆()
                    return True
            elif 类型 == "上下文" and 插件路径:
                插件名 = self._提取插件名(插件路径)
                if 插件名 and 插件名 in data["插件记忆"]:
                    上下文列表 = data["插件记忆"][插件名].get("上下文", [])
                    if 0 <= 索引 < len(上下文列表):
                        del 上下文列表[索引]
                        self._保存记忆()
                        return True

            return False

    # ─── 工具方法 ────────────────────────────────────────────

    def _提取插件名(self, 插件路径: str) -> str:
        """从插件路径中提取插件名"""
        if not 插件路径:
            return ""

        # 尝试从 custom_nodes 路径中提取
        match = _PLUGIN_NAME_PATTERN.search(插件路径)
        if match:
            return match.group(1)

        # 如果是纯名称（无路径分隔符），直接返回
        name = os.path.basename(插件路径.rstrip("/\\"))
        if name:
            return name

        # 最后回退到原始字符串
        return 插件路径
