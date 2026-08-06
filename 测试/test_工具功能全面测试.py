"""工具功能全面测试 — 覆盖 14 个文件操作工具 + 工具路由器 + 辅助函数。

测试范围：
  1. 文件读写操作层（后端/文件读写操作.py）
     - read_plugin_file / write_plugin_file / apply_patch
     - search_plugin_file / grep_plugin_files / find_plugin_files
     - scan_plugin_file_tree / rename_plugin_file / delete_plugin_file
     - check_python_syntax / execute_command / update_readme_changelog
  2. 工具路由器层（智能体/工具路由器.py）
     - 执行工具（async 入口）/ _格式化任务计划 / _截断工具结果
     - _解析安全路径 / _补丁变更摘要 / ToolRouter.get_file_tools
  3. 模型客户端工具（智能体/模型客户端工具.py）
     - _strip_thinking_stream / _tool_log_summary / _计算参数哈希
     - _是否合理重复

测试策略：
  - 使用临时目录模拟插件路径，测试结束自动清理
  - 纯静态/同步测试为主，async 工具用 asyncio.run 包装
  - 不依赖 ComfyUI 运行时，可直接 pytest 或 python 执行

运行方式：
    pytest 测试/test_工具功能全面测试.py -v
    python 测试/test_工具功能全面测试.py
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

# ── 路径初始化（与项目其他测试保持一致） ──────────────────────
项目根 = Path(__file__).resolve().parent.parent
_plugin_root = str(项目根)
if _plugin_root not in sys.path:
    sys.path.insert(0, _plugin_root)

# ── 导入被测模块 ──────────────────────────────────────────────
from 后端.文件读写操作 import (
    read_plugin_file,
    write_plugin_file,
    apply_patch,
    search_plugin_file,
    grep_plugin_files,
    find_plugin_files,
    scan_plugin_file_tree,
    rename_plugin_file,
    delete_plugin_file,
    check_python_syntax,
    execute_command,
    update_readme_changelog,
)
from 智能体.工具路由器 import (
    _格式化任务计划,
    _截断工具结果,
    _解析安全路径,
    _补丁变更摘要,
    ToolRouter,
    执行工具,
)
from 智能体.模型客户端工具 import (
    _strip_thinking_stream,
    _tool_log_summary,
    _计算参数哈希,
    _是否合理重复,
)


# ═══════════════════════════════════════════════════════════════
# 辅助工具：临时插件目录
# ═══════════════════════════════════════════════════════════════

def _创建临时插件目录():
    """创建带示例文件的临时插件目录，返回 TemporaryDirectory 对象"""
    tmp = tempfile.mkdtemp(prefix="nca_test_")
    # 创建基础文件
    (Path(tmp) / "test.py").write_text("def hello():\n    print('hello')\n", encoding="utf-8")
    (Path(tmp) / "utils.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (Path(tmp) / "README.md").write_text("# Test Plugin\n\n## 更新介绍\n\n", encoding="utf-8")
    # 创建子目录
    sub = Path(tmp) / "sub"
    sub.mkdir()
    (sub / "nested.py").write_text("class Nested:\n    pass\n", encoding="utf-8")
    return tmp


def _清理临时目录(tmp):
    """安全清理临时目录"""
    import shutil
    try:
        shutil.rmtree(tmp, ignore_errors=True)
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════
# 一、read_plugin_file 测试
# ═══════════════════════════════════════════════════════════════

def test_read_完整文件():
    tmp = _创建临时插件目录()
    try:
        ok, content = read_plugin_file(tmp, "test.py")
        assert ok, f"读取失败: {content}"
        assert "def hello():" in content
        assert "print('hello')" in content
    finally:
        _清理临时目录(tmp)


def test_read_分段读取():
    tmp = _创建临时插件目录()
    try:
        ok, content = read_plugin_file(tmp, "test.py", start_line=1, end_line=1)
        assert ok
        assert "def hello():" in content
        assert "print" not in content  # 第 1 行只有 def
    finally:
        _清理临时目录(tmp)


def test_read_不存在的文件():
    tmp = _创建临时插件目录()
    try:
        ok, msg = read_plugin_file(tmp, "not_exist.py")
        assert not ok
        assert "不存在" in msg
    finally:
        _清理临时目录(tmp)


def test_read_路径穿越攻击():
    tmp = _创建临时插件目录()
    try:
        ok, msg = read_plugin_file(tmp, "../../etc/passwd")
        assert not ok
        assert "安全错误" in msg
    finally:
        _清理临时目录(tmp)


def test_read_目录而非文件():
    tmp = _创建临时插件目录()
    try:
        ok, msg = read_plugin_file(tmp, "sub")
        assert not ok
        assert "不是文件" in msg
    finally:
        _清理临时目录(tmp)


def test_read_起始行超过总行数():
    tmp = _创建临时插件目录()
    try:
        ok, content = read_plugin_file(tmp, "test.py", start_line=999, end_line=1000)
        assert ok
        assert "超过文件总行数" in content
    finally:
        _清理临时目录(tmp)


# ═══════════════════════════════════════════════════════════════
# 二、write_plugin_file 测试
# ═══════════════════════════════════════════════════════════════

def test_write_新建文件():
    tmp = _创建临时插件目录()
    try:
        ok, msg = write_plugin_file(tmp, "new_file.py", "x = 1\n")
        assert ok, f"写入失败: {msg}"
        assert (Path(tmp) / "new_file.py").read_text(encoding="utf-8") == "x = 1\n"
    finally:
        _清理临时目录(tmp)


def test_write_覆盖已有文件():
    tmp = _创建临时插件目录()
    try:
        ok, msg = write_plugin_file(tmp, "test.py", "y = 2\n")
        assert ok
        content = (Path(tmp) / "test.py").read_text(encoding="utf-8")
        assert "y = 2" in content
        assert "def hello" not in content  # 全量覆盖
    finally:
        _清理临时目录(tmp)


def test_write_自动创建子目录():
    tmp = _创建临时插件目录()
    try:
        ok, msg = write_plugin_file(tmp, "deep/nested/dir/file.py", "z = 3\n")
        assert ok
        assert (Path(tmp) / "deep/nested/dir/file.py").exists()
    finally:
        _清理临时目录(tmp)


def test_write_路径穿越():
    tmp = _创建临时插件目录()
    try:
        ok, msg = write_plugin_file(tmp, "../../hack.py", "bad")
        assert not ok
        assert "安全错误" in msg
    finally:
        _清理临时目录(tmp)


# ═══════════════════════════════════════════════════════════════
# 三、apply_patch 测试
# ═══════════════════════════════════════════════════════════════

def test_patch_SEARCH_REPLACE格式():
    tmp = _创建临时插件目录()
    try:
        target = Path(tmp) / "test.py"
        patch = "<<<<<<< SEARCH\ndef hello():\n    print('hello')\n=======\ndef hello():\n    print('world')\n>>>>>>> REPLACE\n"
        apply_patch(target, patch)
        content = target.read_text(encoding="utf-8")
        assert "print('world')" in content
        assert "print('hello')" not in content
    finally:
        _清理临时目录(tmp)


def test_patch_文件不存在():
    tmp = _创建临时插件目录()
    try:
        target = Path(tmp) / "ghost.py"
        patch = "<<<<<<< SEARCH\nfoo\n=======\nbar\n>>>>>>> REPLACE\n"
        try:
            apply_patch(target, patch)
            assert False, "应该抛出 FileNotFoundError"
        except FileNotFoundError:
            pass
    finally:
        _清理临时目录(tmp)


def test_patch_SEARCH不匹配():
    tmp = _创建临时插件目录()
    try:
        target = Path(tmp) / "test.py"
        patch = "<<<<<<< SEARCH\n不存在的原文\n=======\n新内容\n>>>>>>> REPLACE\n"
        try:
            apply_patch(target, patch)
            assert False, "应该抛出 ValueError"
        except ValueError:
            pass
    finally:
        _清理临时目录(tmp)


# ═══════════════════════════════════════════════════════════════
# 四、search_plugin_file 测试
# ═══════════════════════════════════════════════════════════════

def test_search_关键词匹配():
    tmp = _创建临时插件目录()
    try:
        ok, result = search_plugin_file(tmp, "test.py", "hello")
        assert ok
        assert "hello" in result
    finally:
        _清理临时目录(tmp)


def test_search_正则表达式():
    tmp = _创建临时插件目录()
    try:
        ok, result = search_plugin_file(tmp, "test.py", r"def \w+\(\)", use_regex=True)
        assert ok
        assert "hello" in result
    finally:
        _清理临时目录(tmp)


def test_search_无匹配():
    tmp = _创建临时插件目录()
    try:
        ok, result = search_plugin_file(tmp, "test.py", "zzz_not_found")
        # 无匹配也是成功（只是结果为空）
        assert ok
    finally:
        _清理临时目录(tmp)


def test_search_不存在的文件():
    tmp = _创建临时插件目录()
    try:
        ok, msg = search_plugin_file(tmp, "ghost.py", "hello")
        assert not ok
    finally:
        _清理临时目录(tmp)


# ═══════════════════════════════════════════════════════════════
# 五、grep_plugin_files 测试
# ═══════════════════════════════════════════════════════════════

def test_grep_跨文件搜索():
    tmp = _创建临时插件目录()
    try:
        ok, result = grep_plugin_files(tmp, "def")
        assert ok
        assert "test.py" in result or "utils.py" in result
    finally:
        _清理临时目录(tmp)


def test_grep_带glob过滤():
    tmp = _创建临时插件目录()
    try:
        ok, result = grep_plugin_files(tmp, "class", glob_filter="*.py")
        assert ok
        assert "nested.py" in result
    finally:
        _清理临时目录(tmp)


def test_grep_无匹配():
    tmp = _创建临时插件目录()
    try:
        ok, result = grep_plugin_files(tmp, "zzz_no_match_xyz")
        assert ok  # 搜索成功但无结果
    finally:
        _清理临时目录(tmp)


# ═══════════════════════════════════════════════════════════════
# 六、find_plugin_files 测试
# ═══════════════════════════════════════════════════════════════

def test_find_所有Python文件():
    tmp = _创建临时插件目录()
    try:
        ok, result = find_plugin_files(tmp, "*.py")
        assert ok
        assert "test.py" in result
        assert "utils.py" in result
    finally:
        _清理临时目录(tmp)


def test_find_递归查找():
    tmp = _创建临时插件目录()
    try:
        ok, result = find_plugin_files(tmp, "**/*.py")
        assert ok
        assert "nested.py" in result
    finally:
        _清理临时目录(tmp)


def test_find_无匹配():
    tmp = _创建临时插件目录()
    try:
        ok, result = find_plugin_files(tmp, "*.java")
        assert ok  # 成功但结果为空
    finally:
        _清理临时目录(tmp)


# ═══════════════════════════════════════════════════════════════
# 七、scan_plugin_file_tree 测试
# ═══════════════════════════════════════════════════════════════

def test_scan_文件树结构():
    tmp = _创建临时插件目录()
    try:
        tree = scan_plugin_file_tree(tmp)
        assert isinstance(tree, list)
        assert len(tree) > 0
        # 检查包含目录和文件
        names = [item.get("name") for item in tree]
        assert "test.py" in names or "utils.py" in names
        assert "sub" in names
    finally:
        _清理临时目录(tmp)


def test_scan_空目录():
    tmp = tempfile.mkdtemp(prefix="nca_test_empty_")
    try:
        tree = scan_plugin_file_tree(tmp)
        assert tree == [] or tree is None
    finally:
        _清理临时目录(tmp)


# ═══════════════════════════════════════════════════════════════
# 八、rename_plugin_file 测试
# ═══════════════════════════════════════════════════════════════

def test_rename_正常重命名():
    tmp = _创建临时插件目录()
    try:
        ok, msg = rename_plugin_file(tmp, "test.py", "renamed.py")
        assert ok, f"重命名失败: {msg}"
        assert (Path(tmp) / "renamed.py").exists()
        assert not (Path(tmp) / "test.py").exists()
    finally:
        _清理临时目录(tmp)


def test_rename_源文件不存在():
    tmp = _创建临时插件目录()
    try:
        ok, msg = rename_plugin_file(tmp, "ghost.py", "new.py")
        assert not ok
        assert "不存在" in msg
    finally:
        _清理临时目录(tmp)


def test_rename_路径穿越():
    tmp = _创建临时插件目录()
    try:
        ok, msg = rename_plugin_file(tmp, "test.py", "../../escape.py")
        assert not ok
        assert "安全错误" in msg
    finally:
        _清理临时目录(tmp)


# ═══════════════════════════════════════════════════════════════
# 九、delete_plugin_file 测试
# ═══════════════════════════════════════════════════════════════

def test_delete_正常删除():
    tmp = _创建临时插件目录()
    try:
        assert (Path(tmp) / "test.py").exists()
        ok, msg = delete_plugin_file(tmp, "test.py")
        assert ok, f"删除失败: {msg}"
        assert not (Path(tmp) / "test.py").exists()
        # 应该有 .bak 备份
        assert (Path(tmp) / "test.py.bak").exists()
    finally:
        _清理临时目录(tmp)


def test_delete_不存在的文件():
    tmp = _创建临时插件目录()
    try:
        ok, msg = delete_plugin_file(tmp, "ghost.py")
        assert not ok
        assert "不存在" in msg
    finally:
        _清理临时目录(tmp)


def test_delete_路径穿越():
    tmp = _创建临时插件目录()
    try:
        ok, msg = delete_plugin_file(tmp, "../../important.py")
        assert not ok
        assert "安全错误" in msg
    finally:
        _清理临时目录(tmp)


# ═══════════════════════════════════════════════════════════════
# 十、check_python_syntax 测试
# ═══════════════════════════════════════════════════════════════

def test_syntax_正确语法():
    tmp = _创建临时插件目录()
    try:
        ok, msg = check_python_syntax(tmp, "test.py")
        assert ok, f"语法检查失败: {msg}"
        assert "语法检查通过" in msg
    finally:
        _清理临时目录(tmp)


def test_syntax_错误语法():
    tmp = _创建临时插件目录()
    try:
        (Path(tmp) / "bad.py").write_text("def broken(\n", encoding="utf-8")
        ok, msg = check_python_syntax(tmp, "bad.py")
        assert not ok
        assert "语法错误" in msg
    finally:
        _清理临时目录(tmp)


def test_syntax_非Python文件():
    tmp = _创建临时插件目录()
    try:
        (Path(tmp) / "readme.txt").write_text("hello", encoding="utf-8")
        ok, msg = check_python_syntax(tmp, "readme.txt")
        assert not ok
        assert "不是 Python 文件" in msg
    finally:
        _清理临时目录(tmp)


def test_syntax_路径穿越():
    tmp = _创建临时插件目录()
    try:
        ok, msg = check_python_syntax(tmp, "../../hack.py")
        assert not ok
        assert "安全错误" in msg
    finally:
        _清理临时目录(tmp)


# ═══════════════════════════════════════════════════════════════
# 十一、execute_command 测试
# ═══════════════════════════════════════════════════════════════

def test_cmd_python验证():
    tmp = _创建临时插件目录()
    try:
        ok, result = execute_command(tmp, 'python -c "print(1+1)"', timeout=10)
        assert ok, f"命令执行失败: {result}"
        assert "2" in result
    finally:
        _清理临时目录(tmp)


def test_cmd_拒绝危险命令():
    tmp = _创建临时插件目录()
    try:
        ok, msg = execute_command(tmp, "rm -rf /")
        assert not ok
        assert "不允许" in msg or "危险" in msg
    finally:
        _清理临时目录(tmp)


def test_cmd_拒绝管道操作():
    tmp = _创建临时插件目录()
    try:
        ok, msg = execute_command(tmp, 'python -c "x" | cat')
        assert not ok
    finally:
        _清理临时目录(tmp)


def test_cmd_空命令():
    tmp = _创建临时插件目录()
    try:
        ok, msg = execute_command(tmp, "")
        assert not ok
        assert "不能为空" in msg
    finally:
        _清理临时目录(tmp)


def test_cmd_git_status():
    tmp = _创建临时插件目录()
    try:
        ok, result = execute_command(tmp, "git status")
        # git status 可能成功也可能不在 git 仓库中，但不应被白名单拒绝
        # 如果不在 git 仓库，返回的可能是 git 的错误信息，但命令本身被允许执行
        assert ok or "不允许" not in str(result)
    finally:
        _清理临时目录(tmp)


# ═══════════════════════════════════════════════════════════════
# 十二、update_readme_changelog 测试
# ═══════════════════════════════════════════════════════════════

def test_readme_追加更新记录():
    tmp = _创建临时插件目录()
    try:
        result = update_readme_changelog(tmp, "新增了测试功能")
        assert result["success"], f"更新失败: {result['message']}"
        content = (Path(tmp) / "README.md").read_text(encoding="utf-8")
        assert "新增了测试功能" in content
    finally:
        _清理临时目录(tmp)


def test_readme_无README文件():
    tmp = tempfile.mkdtemp(prefix="nca_test_noreadme_")
    try:
        result = update_readme_changelog(tmp, "测试")
        # 应该自动创建或返回失败（取决于实现）
        assert isinstance(result, dict)
        assert "success" in result
    finally:
        _清理临时目录(tmp)


# ═══════════════════════════════════════════════════════════════
# 十三、工具路由器 — _格式化任务计划 测试
# ═══════════════════════════════════════════════════════════════

def test_plan_正常步骤():
    steps = [
        {"step": "创建文件", "status": "completed"},
        {"step": "编写代码", "status": "in_progress"},
        {"step": "测试验证", "status": "pending"},
    ]
    result = _格式化任务计划(steps)
    assert "☑" in result  # completed
    assert "▶" in result  # in_progress
    assert "☐" in result  # pending
    assert "1/3 已完成" in result


def test_plan_全部完成():
    steps = [
        {"step": "步骤A", "status": "completed"},
        {"step": "步骤B", "status": "completed"},
    ]
    result = _格式化任务计划(steps)
    assert "2/2 已完成" in result
    assert "无需再调用" in result


def test_plan_空数组():
    result = _格式化任务计划([])
    assert "❌" in result
    assert "非空数组" in result


def test_plan_超过20步():
    steps = [{"step": f"步骤{i}", "status": "pending"} for i in range(21)]
    result = _格式化任务计划(steps)
    assert "❌" in result
    assert "步骤过多" in result


def test_plan_非法状态():
    steps = [{"step": "测试", "status": "invalid_status"}]
    result = _格式化任务计划(steps)
    assert "❌" in result
    assert "status 非法" in result


def test_plan_缺少step描述():
    steps = [{"status": "pending"}]
    result = _格式化任务计划(steps)
    assert "❌" in result
    assert "缺少 step 描述" in result


# ═══════════════════════════════════════════════════════════════
# 十四、工具路由器 — _解析安全路径 测试
# ═══════════════════════════════════════════════════════════════

def test_safe_path_正常路径():
    tmp = _创建临时插件目录()
    try:
        result = _解析安全路径(tmp, "test.py")
        assert isinstance(result, Path)
        assert str(result).startswith(str(Path(tmp).resolve()))
    finally:
        _清理临时目录(tmp)


def test_safe_path_路径穿越():
    tmp = _创建临时插件目录()
    try:
        _解析安全路径(tmp, "../../etc/passwd")
        assert False, "应该抛出 ValueError"
    except ValueError as e:
        assert "安全错误" in str(e) or "超出" in str(e)
    finally:
        _清理临时目录(tmp)


# ═══════════════════════════════════════════════════════════════
# 十五、工具路由器 — _补丁变更摘要 测试
# ═══════════════════════════════════════════════════════════════

def test_patch_summary_SEARCH_REPLACE():
    patch = "<<<<<<< SEARCH\nold\n=======\nnew\n>>>>>>> REPLACE\n"
    result = _补丁变更摘要(patch)
    assert "1 个替换块" in result


def test_patch_summary_多个替换块():
    patch = (
        "<<<<<<< SEARCH\na\n=======\nb\n>>>>>>> REPLACE\n"
        "<<<<<<< SEARCH\nc\n=======\nd\n>>>>>>> REPLACE\n"
    )
    result = _补丁变更摘要(patch)
    assert "2 个替换块" in result


def test_patch_summary_unified_diff():
    patch = "+added line\n-removed line\n+another added\n"
    result = _补丁变更摘要(patch)
    assert "新增" in result
    assert "删除" in result


# ═══════════════════════════════════════════════════════════════
# 十六、工具路由器 — _截断工具结果 测试
# ═══════════════════════════════════════════════════════════════

def test_truncate_短内容不截断():
    result = _截断工具结果("短内容", "test_tool")
    assert result == "短内容"


def test_truncate_超长内容被截断():
    long_content = "x" * 100000  # 远超阈值
    result = _截断工具结果(long_content, "unknown_tool")
    assert len(result) < len(long_content)
    assert "截断" in result


# ═══════════════════════════════════════════════════════════════
# 十七、工具路由器 — ToolRouter 测试
# ═══════════════════════════════════════════════════════════════

def test_router_初始化():
    router = ToolRouter()
    assert router.文件工具 is not None
    assert len(router.文件工具) > 0


def test_router_get_file_tools_默认():
    router = ToolRouter()
    tools = router.get_file_tools()
    tool_names = {t["name"] for t in tools}
    # 默认（develop）应包含全部工具
    assert "write_plugin_file" in tool_names
    assert "edit_file" in tool_names
    assert "batch_edit" in tool_names


def test_router_get_file_tools_visualize():
    router = ToolRouter()
    tools = router.get_file_tools(active_tab="visualize")
    tool_names = {t["name"] for t in tools}
    # visualize 只应包含只读工具
    assert "read_plugin_file" in tool_names
    assert "grep_plugin_files" in tool_names
    assert "write_plugin_file" not in tool_names
    assert "edit_file" not in tool_names
    assert "batch_edit" not in tool_names


def test_router_工具定义完整性():
    """每个工具定义必须包含 name / description / parameters"""
    router = ToolRouter()
    for tool in router.文件工具:
        assert "name" in tool, f"工具缺少 name: {tool}"
        assert "description" in tool, f"工具缺少 description: {tool['name']}"
        assert "parameters" in tool, f"工具缺少 parameters: {tool['name']}"
        params = tool["parameters"]
        assert params.get("type") == "object", f"{tool['name']} 的 parameters.type 不是 object"


def test_router_retrieve_knowledge_空消息():
    router = ToolRouter()
    result = router.retrieve_knowledge("")
    assert result == ""


def test_router_retrieve_knowledge_短闲聊():
    router = ToolRouter()
    result = router.retrieve_knowledge("你好")
    assert result == ""  # 短消息无技术词，跳过检索


# ═══════════════════════════════════════════════════════════════
# 十八、执行工具（async 入口）测试
# ═══════════════════════════════════════════════════════════════

def _run_async(coro):
    """兼容 Python 3.9+ 的 asyncio.run 包装"""
    return asyncio.run(coro)


def test_exec_read():
    tmp = _创建临时插件目录()
    try:
        result = _run_async(执行工具("read_plugin_file", {"file_path": "test.py"}, tmp))
        assert "📄" in result
        assert "hello" in result
    finally:
        _清理临时目录(tmp)


def test_exec_write():
    tmp = _创建临时插件目录()
    try:
        result = _run_async(执行工具("write_plugin_file", {"file_path": "new.py", "content": "a = 1\n"}, tmp))
        assert "✅" in result
    finally:
        _清理临时目录(tmp)


def test_exec_list():
    tmp = _创建临时插件目录()
    try:
        result = _run_async(执行工具("list_plugin_files", {}, tmp))
        assert "📁" in result
    finally:
        _清理临时目录(tmp)


def test_exec_search():
    tmp = _创建临时插件目录()
    try:
        result = _run_async(执行工具("search_plugin_file", {"file_path": "test.py", "pattern": "hello"}, tmp))
        assert "🔍" in result or "hello" in result
    finally:
        _清理临时目录(tmp)


def test_exec_grep():
    tmp = _创建临时插件目录()
    try:
        result = _run_async(执行工具("grep_plugin_files", {"pattern": "def"}, tmp))
        assert "def" in result
    finally:
        _清理临时目录(tmp)


def test_exec_find():
    tmp = _创建临时插件目录()
    try:
        result = _run_async(执行工具("find_plugin_files", {"glob_pattern": "*.py"}, tmp))
        assert ".py" in result
    finally:
        _清理临时目录(tmp)


def test_exec_check_syntax():
    tmp = _创建临时插件目录()
    try:
        result = _run_async(执行工具("check_python_syntax", {"file_path": "test.py"}, tmp))
        assert "✅" in result or "语法检查通过" in result
    finally:
        _清理临时目录(tmp)


def test_exec_update_plan():
    tmp = _创建临时插件目录()
    try:
        steps = [{"step": "测试步骤", "status": "pending"}]
        result = _run_async(执行工具("update_plan", {"steps": steps}, tmp))
        assert "📋" in result
        assert "测试步骤" in result
    finally:
        _清理临时目录(tmp)


def test_exec_unknown_tool():
    tmp = _创建临时插件目录()
    try:
        result = _run_async(执行工具("nonexistent_tool", {}, tmp))
        assert "❌" in result
        assert "未知" in result
    finally:
        _清理临时目录(tmp)


def test_exec_空plugin_path():
    result = _run_async(执行工具("read_plugin_file", {"file_path": "test.py"}, ""))
    assert "❌" in result
    assert "plugin_path" in result


def test_exec_edit():
    tmp = _创建临时插件目录()
    try:
        patch = "<<<<<<< SEARCH\ndef hello():\n    print('hello')\n=======\ndef hello():\n    print('edited')\n>>>>>>> REPLACE\n"
        result = _run_async(执行工具("edit_file", {"file_path": "test.py", "patch": patch}, tmp))
        assert "✅" in result
        content = (Path(tmp) / "test.py").read_text(encoding="utf-8")
        assert "edited" in content
    finally:
        _清理临时目录(tmp)


def test_exec_rename():
    tmp = _创建临时插件目录()
    try:
        result = _run_async(执行工具("rename_plugin_file", {"source_path": "test.py", "dest_path": "renamed.py"}, tmp))
        assert "✅" in result
    finally:
        _清理临时目录(tmp)


def test_exec_delete():
    tmp = _创建临时插件目录()
    try:
        result = _run_async(执行工具("delete_plugin_file", {"file_path": "test.py"}, tmp))
        assert "✅" in result
    finally:
        _清理临时目录(tmp)


def test_exec_batch_edit():
    tmp = _创建临时插件目录()
    try:
        ops = [
            {"action": "create", "file_path": "batch1.py", "content": "x = 1\n"},
            {"action": "write", "file_path": "batch2.py", "content": "y = 2\n"},
        ]
        result = _run_async(执行工具("batch_edit", {"operations": ops}, tmp))
        assert "批量操作完成" in result
        assert "成功: 2" in result
    finally:
        _清理临时目录(tmp)


def test_exec_batch_edit_超过20个():
    tmp = _创建临时插件目录()
    try:
        ops = [{"action": "create", "file_path": f"f{i}.py", "content": ""} for i in range(21)]
        result = _run_async(执行工具("batch_edit", {"operations": ops}, tmp))
        assert "❌" in result
        assert "不能超过 20" in result
    finally:
        _清理临时目录(tmp)


def test_exec_update_readme():
    tmp = _创建临时插件目录()
    try:
        result = _run_async(执行工具("update_readme", {"changelog_entry": "修复了某个bug"}, tmp))
        assert "✅" in result
    finally:
        _清理临时目录(tmp)


def test_exec_缺少必需参数():
    tmp = _创建临时插件目录()
    try:
        # read 缺少 file_path
        result = _run_async(执行工具("read_plugin_file", {}, tmp))
        assert "❌" in result
        assert "file_path" in result
    finally:
        _清理临时目录(tmp)


# ═══════════════════════════════════════════════════════════════
# 十九、模型客户端工具 — _strip_thinking_stream 测试
# ═══════════════════════════════════════════════════════════════

def test_thinking_剥离thinking标签():
    text, buf, in_think = _strip_thinking_stream("正常文本<thinking>隐藏内容</thinking>后续文本", "", False)
    assert "正常文本" in text
    assert "隐藏内容" not in text
    assert "后续文本" in text
    assert not in_think


def test_thinking_剥离think标签():
    text, buf, in_think = _strip_thinking_stream("<think>隐藏</think>可见", "", False)
    assert "隐藏" not in text
    assert "可见" in text


def test_thinking_无标签():
    text, buf, in_think = _strip_thinking_stream("纯文本内容", "", False)
    assert text == "纯文本内容"
    assert not in_think


def test_thinking_跨chunk标签():
    # 第一个 chunk 以 <think 结尾
    text1, buf1, in1 = _strip_thinking_stream("可见<think", "", False)
    assert "可见" in text1
    assert not in1  # 还没关闭
    # 第二个 chunk 包含关闭标签
    text2, buf2, in2 = _strip_thinking_stream(">隐藏</think>可见2", buf1, in1)
    assert "隐藏" not in text2
    assert "可见2" in text2


def test_thinking_开标签后未关闭():
    text, buf, in_think = _strip_thinking_stream("前<thinking>未关闭", "", False)
    assert "前" in text
    assert "未关闭" not in text
    assert in_think  # 仍在 thinking 块内


# ═══════════════════════════════════════════════════════════════
# 二十、模型客户端工具 — _tool_log_summary 测试
# ═══════════════════════════════════════════════════════════════

def test_log_write():
    result = _tool_log_summary("write_plugin_file", {"file_path": "test.py", "content": "x" * 1000})
    assert "test.py" in result
    assert "1000" in result


def test_log_edit():
    result = _tool_log_summary("edit_file", {"file_path": "a.py", "patch": "yyy"})
    assert "a.py" in result


def test_log_read():
    result = _tool_log_summary("read_plugin_file", {"file_path": "b.py"})
    assert "b.py" in result


def test_log_batch():
    ops = [{"action": "create", "file_path": f"f{i}.py"} for i in range(7)]
    result = _tool_log_summary("batch_edit", {"operations": ops})
    assert "+2个" in result  # 超过5个显示后缀


def test_log_unknown():
    result = _tool_log_summary("unknown_tool", {"key1": "val"})
    assert "key1" in result


# ═══════════════════════════════════════════════════════════════
# 二十一、模型客户端工具 — _计算参数哈希 测试
# ═══════════════════════════════════════════════════════════════

def test_hash_相同参数相同哈希():
    h1 = _计算参数哈希("read_plugin_file", {"file_path": "a.py"})
    h2 = _计算参数哈希("read_plugin_file", {"file_path": "a.py"})
    assert h1 == h2


def test_hash_不同参数不同哈希():
    h1 = _计算参数哈希("read_plugin_file", {"file_path": "a.py"})
    h2 = _计算参数哈希("read_plugin_file", {"file_path": "b.py"})
    assert h1 != h2


def test_hash_长度固定8位():
    h = _计算参数哈希("test", {})
    assert len(h) == 8


# ═══════════════════════════════════════════════════════════════
# 二十二、模型客户端工具 — _是否合理重复 测试
# ═══════════════════════════════════════════════════════════════

def test_repeat_execute_command_合理():
    assert _是否合理重复("execute_command", {"command": "ls"}, set()) is True


def test_repeat_read_文件被修改过():
    assert _是否合理重复("read_plugin_file", {"file_path": "a.py"}, {"a.py"}) is True


def test_repeat_read_文件未被修改():
    assert _是否合理重复("read_plugin_file", {"file_path": "a.py"}, set()) is False


def test_repeat_write_不合理():
    assert _是否合理重复("write_plugin_file", {"file_path": "a.py"}, set()) is False


# ═══════════════════════════════════════════════════════════════
# 二十三、综合场景测试
# ═══════════════════════════════════════════════════════════════

def test_完整工作流_创建读写删除():
    """模拟一个完整的插件开发工作流"""
    tmp = _创建临时插件目录()
    try:
        # 1. 列出文件
        tree = scan_plugin_file_tree(tmp)
        assert len(tree) > 0

        # 2. 写入新文件
        ok, _ = write_plugin_file(tmp, "feature.py", "def feature():\n    return True\n")
        assert ok

        # 3. 读取验证
        ok, content = read_plugin_file(tmp, "feature.py")
        assert ok and "feature" in content

        # 4. 搜索关键词
        ok, result = search_plugin_file(tmp, "feature.py", "feature")
        assert ok and "feature" in result

        # 5. 语法检查
        ok, msg = check_python_syntax(tmp, "feature.py")
        assert ok

        # 6. 增量编辑
        target = Path(tmp) / "feature.py"
        apply_patch(target, "<<<<<<< SEARCH\n    return True\n=======\n    return False\n>>>>>>> REPLACE\n")
        ok, content = read_plugin_file(tmp, "feature.py")
        assert "return False" in content

        # 7. 重命名
        ok, _ = rename_plugin_file(tmp, "feature.py", "feature_v2.py")
        assert ok

        # 8. 删除
        ok, _ = delete_plugin_file(tmp, "feature_v2.py")
        assert ok
        assert not (Path(tmp) / "feature_v2.py").exists()
    finally:
        _清理临时目录(tmp)


def test_完整async工作流():
    """通过 async 入口走完整流程"""
    tmp = _创建临时插件目录()
    try:
        # 写文件
        r = _run_async(执行工具("write_plugin_file", {"file_path": "async_test.py", "content": "v = 1\n"}, tmp))
        assert "✅" in r

        # 读文件
        r = _run_async(执行工具("read_plugin_file", {"file_path": "async_test.py"}, tmp))
        assert "v = 1" in r

        # 更新计划
        r = _run_async(执行工具("update_plan", {"steps": [
            {"step": "写入文件", "status": "completed"},
            {"step": "验证内容", "status": "completed"},
        ]}, tmp))
        assert "2/2 已完成" in r
    finally:
        _清理临时目录(tmp)


# ═══════════════════════════════════════════════════════════════
# 独立运行入口
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
    用例 = [(名, 函数) for 名, 函数 in sorted(globals().items()) if 名.startswith("test_")]
    失败数 = 0
    通过数 = 0
    for 名, 函数 in 用例:
        try:
            函数()
            通过数 += 1
            print(f"  PASS  {名}")
        except AssertionError as e:
            失败数 += 1
            print(f"  FAIL  {名}\n        {e}")
        except Exception as e:
            失败数 += 1
            print(f"  ERROR {名}\n        {type(e).__name__}: {e}")
    print(f"\n{'='*60}")
    print(f"总计: {通过数 + 失败数} | 通过: {通过数} | 失败: {失败数}")
    print(f"{'='*60}")
    raise SystemExit(1 if 失败数 else 0)
