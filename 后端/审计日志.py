"""
审计日志模块 - 结构化 API 调用记录
"""
import json
import time
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, List
from aiohttp import web


class AuditLogger:
    """审计日志记录器"""

    def __init__(self, log_dir: Path, retention_days: int = 30):
        self.log_dir = log_dir
        self.retention_days = retention_days
        self._ensure_log_dir()
        self._cleanup_old_logs()

    def _ensure_log_dir(self):
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
        except (PermissionError, OSError) as e:
            import sys
            import tempfile
            print(f"[AuditLogger] 日志目录创建失败: {e}，将使用临时目录", file=sys.stderr)
            self.log_dir = Path(tempfile.gettempdir()) / "nca_audit_logs"
            self.log_dir.mkdir(parents=True, exist_ok=True)

    def _get_log_file(self) -> Path:
        """当天日志文件: audit_2026-06-13.jsonl"""
        today = datetime.now().strftime("%Y-%m-%d")
        return self.log_dir / f"audit_{today}.jsonl"

    def log(self, entry: dict):
        """写入一条审计记录

        Bug 13 修复：捕获 IO/OS 错误（如磁盘满、权限不足、目录被删除等），
        审计日志写入失败不应导致业务请求失败，仅以 stderr 告警降级处理。
        """
        entry['timestamp'] = datetime.now().isoformat()
        log_file = self._get_log_file()
        try:
            with open(log_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(entry, ensure_ascii=False) + '\n')
        except (IOError, OSError) as e:
            import sys
            print(f"[AuditLogger] 写入审计日志失败: {e}", file=sys.stderr)

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
                with open(log_file, 'r', encoding='utf-8') as f:
                    for line in f:
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


@web.middleware
async def audit_middleware(request, handler):
    """审计中间件 - 自动记录所有 API 调用"""
    start_time = time.time()

    # 提取请求信息
    client_ip = request.remote or request.headers.get('X-Forwarded-For', 'unknown')
    user = request.get('user', {}).get('username', 'anonymous') if isinstance(request.get('user'), dict) else 'anonymous'
    method = request.method
    path = request.path

    try:
        response = await handler(request)
        duration_ms = round((time.time() - start_time) * 1000, 2)

        # 记录审计日志
        audit_logger = request.app.get('audit_logger')
        if audit_logger and path.startswith('/ai-coder'):
            audit_logger.log({
                'user': user,
                'action': f"{method} {path}",
                'method': method,
                'endpoint': path,
                'status': response.status,
                'duration_ms': duration_ms,
                'client_ip': client_ip,
            })

        return response
    except Exception as e:
        duration_ms = round((time.time() - start_time) * 1000, 2)
        audit_logger = request.app.get('audit_logger')
        if audit_logger and path.startswith('/ai-coder'):
            audit_logger.log({
                'user': user,
                'action': f"{method} {path}",
                'method': method,
                'endpoint': path,
                'status': 500,
                'duration_ms': duration_ms,
                'client_ip': client_ip,
                'error': str(e),
            })
        raise
