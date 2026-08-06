"""
审计日志模块 - 结构化 API 调用记录
"""
import asyncio
import json
import logging
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

from aiohttp import web

logger = logging.getLogger(__name__)

_单文件大小上限 = 50 * 1024 * 1024  # 50MB


class AuditLogger:
    """审计日志记录器"""

    def __init__(self, log_dir: Path, retention_days: int = 30):
        self.log_dir = log_dir
        self.retention_days = retention_days
        # 写入锁：async_log 经 asyncio.to_thread 在线程池并发调用 log，
        # 无锁并发 append 会交错写入产生损坏行（实测已出现残片记录）
        self._write_lock = threading.Lock()
        self._ensure_log_dir()
        self._cleanup_old_logs()

    def _ensure_log_dir(self):
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
        except (PermissionError, OSError) as e:
            import sys
            import tempfile
            # 降级输出：logger 不可用时的兜底（审计日志目录初始化失败，日志系统自身可能尚未就绪）
            sys.stderr.write(f"[AuditLogger] 日志目录创建失败: {e}，将使用临时目录\n")
            self.log_dir = Path(tempfile.gettempdir()) / "nca_audit_logs"
            self.log_dir.mkdir(parents=True, exist_ok=True)

    def _get_log_file(self) -> Path:
        """当天日志文件: audit_2026-06-13.jsonl"""
        today = datetime.now().strftime("%Y-%m-%d")
        return self.log_dir / f"audit_{today}.jsonl"

    def _获取日志文件路径(self, 日期=None):
        """获取当前日志文件路径，如果超过大小上限则滚动到新文件"""
        日期 = 日期 or datetime.now().strftime("%Y-%m-%d")
        基础路径 = self.log_dir / f"audit_{日期}.jsonl"

        # 如果基础文件未超限，直接使用
        if not 基础路径.exists() or 基础路径.stat().st_size < _单文件大小上限:
            return 基础路径

        # 基础文件超限，查找下一个可用的滚动文件
        序号 = 1
        while True:
            滚动路径 = self.log_dir / f"audit_{日期}_{序号}.jsonl"
            if not 滚动路径.exists() or 滚动路径.stat().st_size < _单文件大小上限:
                return 滚动路径
            序号 += 1

    def log(self, entry: dict):
        """写入一条审计记录

        Bug 13 修复：捕获 IO/OS 错误（如磁盘满、权限不足、目录被删除等），
        审计日志写入失败不应导致业务请求失败，仅以 stderr 告警降级处理。
        """
        entry['timestamp'] = datetime.now().isoformat()
        log_file = self._获取日志文件路径()
        try:
            # 加锁串行写入：防止线程池并发 append 交错打坏 JSONL 行
            with self._write_lock:
                with open(log_file, 'a', encoding='utf-8') as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + '\n')
        except (IOError, OSError) as e:
            import sys
            # 降级输出：logger 不可用时的兜底（审计日志写入失败，此处不可再依赖同类机制）
            sys.stderr.write(f"[AuditLogger] 写入审计日志失败: {e}\n")

    async def async_log(self, entry: dict):
        """异步写入一条审计记录（不阻塞事件循环）。

        使用 asyncio.to_thread 将同步文件 I/O 委托到线程池执行。
        中间件应优先使用此方法而非同步的 log 方法。
        """
        await asyncio.to_thread(self.log, entry)

    def _cleanup_old_logs(self):
        """清理超过保留期的日志文件"""
        cutoff = datetime.now() - timedelta(days=self.retention_days)
        for log_file in self.log_dir.glob("audit_*.jsonl"):
            try:
                date_str = log_file.stem.replace("audit_", "")
                file_date = datetime.strptime(date_str, "%Y-%m-%d")
                if file_date < cutoff:
                    log_file.unlink()
            except (ValueError, OSError):
                pass

    def _get_date_range_files(self, start_date: Optional[datetime], end_date: Optional[datetime]) -> List[Path]:
        """获取指定日期范围内的日志文件列表，按日期排序"""
        log_files = []
        for log_file in self.log_dir.glob("audit_*.jsonl"):
            try:
                date_str = log_file.stem.replace("audit_", "")
                file_date = datetime.strptime(date_str, "%Y-%m-%d")
                if start_date and file_date.date() < start_date.date():
                    continue
                if end_date and file_date.date() > end_date.date():
                    continue
                log_files.append((file_date, log_file))
            except (ValueError, OSError):
                continue
        # 按日期倒序排列，优先读取最近的文件
        log_files.sort(key=lambda x: x[0], reverse=True)
        return [f for _, f in log_files]

    @staticmethod
    def _tail_read(file_path: Path, n_lines: int) -> List[str]:
        """从文件尾部读取最后 N 行（避免大文件全量加载到内存）"""
        with open(file_path, 'rb') as f:
            f.seek(0, 2)
            file_size = f.tell()
            block_size = 8192
            position = file_size
            data = b''
            while position > 0:
                read_size = min(block_size, position)
                position -= read_size
                f.seek(position)
                data = f.read(read_size) + data
                if data.count(b'\n') >= n_lines:
                    break
            all_lines = data.split(b'\n')
            # 文件末尾若有空行则移除
            if all_lines and all_lines[-1] == b'':
                all_lines = all_lines[:-1]
            result = []
            for line in all_lines[-n_lines:]:
                try:
                    result.append(line.decode('utf-8'))
                except UnicodeDecodeError:
                    continue
            return result

    def query(self, user: str = None, action: str = None,
              start_time: str = None, end_time: str = None,
              limit: int = 100) -> List[dict]:
        """
        查询审计记录

        Args:
            user: 按用户名过滤
            action: 按操作类型（endpoint）过滤，支持部分匹配
            start_time: 起始时间，ISO 格式字符串
            end_time: 结束时间，ISO 格式字符串
            limit: 返回最大条数，默认100

        Returns:
            按时间倒序排列的审计记录列表
        """
        results = []

        # 解析时间范围
        start_dt = None
        end_dt = None
        if start_time:
            try:
                start_dt = datetime.fromisoformat(start_time)
            except ValueError:
                pass
        if end_time:
            try:
                end_dt = datetime.fromisoformat(end_time)
            except ValueError:
                pass

        # 获取日期范围内的日志文件
        log_files = self._get_date_range_files(start_dt, end_dt)

        for log_file in log_files:
            if len(results) >= limit:
                break

            file_entries = []
            try:
                # 检查文件大小，大文件使用尾部读取优化
                file_size = log_file.stat().st_size
                if file_size > 50 * 1024 * 1024:  # 50MB
                    logger.warning(f"日志文件 {log_file.name} 超过50MB（{file_size // 1024 // 1024}MB）")

                if file_size > 10 * 1024 * 1024:  # 10MB
                    # 大文件：仅从尾部读取最近 N 行，避免全量加载
                    raw_lines = self._tail_read(log_file, max(limit * 5, 500))
                else:
                    # 小文件：全量读取
                    with open(log_file, 'r', encoding='utf-8') as f:
                        raw_lines = f.readlines()

                for line in raw_lines:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    # 按时间范围过滤
                    if start_dt or end_dt:
                        entry_time_str = entry.get('timestamp', '')
                        try:
                            entry_time = datetime.fromisoformat(entry_time_str)
                        except ValueError:
                            continue
                        if start_dt and entry_time < start_dt:
                            continue
                        if end_dt and entry_time > end_dt:
                            continue

                    # 按用户名过滤
                    if user and entry.get('user') != user:
                        continue

                    # 按操作类型过滤（支持部分匹配）
                    if action:
                        entry_action = entry.get('action', '')
                        entry_endpoint = entry.get('endpoint', '')
                        if action not in entry_action and action not in entry_endpoint:
                            continue

                    file_entries.append(entry)
            except OSError:
                continue

            # 文件内条目按时间倒序
            file_entries.sort(
                key=lambda x: x.get('timestamp', ''), reverse=True
            )
            results.extend(file_entries)

        # 最终结果按时间倒序，取 limit 条
        results.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
        return results[:limit]


def _脱敏用户名(用户名):
    """截断用户名，短用户名也做部分遮蔽"""
    if not 用户名:
        return 用户名
    if len(用户名) <= 2:
        return 用户名[0] + "*"
    if len(用户名) <= 4:
        return 用户名[:2] + "**"
    return 用户名[:4] + "***"


def _脱敏IP(ip地址):
    """IP地址仅保留前两段"""
    if not ip地址:
        return ip地址
    # 处理 X-Forwarded-For 多 IP 的情况，仅取第一个
    ip地址 = ip地址.split(",")[0].strip()
    parts = ip地址.split(".")
    if len(parts) == 4:  # IPv4
        return f"{parts[0]}.{parts[1]}.xxx.xxx"
    if ":" in ip地址:  # IPv6 简化处理
        parts = ip地址.split(":")
        return ":".join(parts[:2]) + "::xxx"
    return ip地址


def _截断错误(错误信息, 最大长度=200):
    """截断错误信息"""
    if not 错误信息:
        return 错误信息
    if len(错误信息) > 最大长度:
        return 错误信息[:最大长度] + "...[truncated]"
    return 错误信息


@web.middleware
async def audit_middleware(request, handler):
    """审计中间件 - 自动记录所有 API 调用"""
    start_time = time.time()

    # 提取请求信息
    client_ip = request.remote or request.headers.get('X-Forwarded-For', 'unknown')
    user = 'anonymous'
    method = request.method
    path = request.path

    try:
        response = await handler(request)
        duration_ms = round((time.time() - start_time) * 1000, 2)

        # 记录审计日志
        audit_logger = request.app.get('audit_logger')
        if audit_logger and path.startswith('/ai-coder'):
            await audit_logger.async_log({
                'user': _脱敏用户名(user),
                'action': f"{method} {path}",
                'method': method,
                'endpoint': path,
                'status': response.status,
                'duration_ms': duration_ms,
                'client_ip': _脱敏IP(client_ip),
            })

        return response
    except Exception as e:
        duration_ms = round((time.time() - start_time) * 1000, 2)
        audit_logger = request.app.get('audit_logger')
        if audit_logger and path.startswith('/ai-coder'):
            await audit_logger.async_log({
                'user': _脱敏用户名(user),
                'action': f"{method} {path}",
                'method': method,
                'endpoint': path,
                'status': 500,
                'duration_ms': duration_ms,
                'client_ip': _脱敏IP(client_ip),
                'error': _截断错误(str(e)),
            })
        raise
