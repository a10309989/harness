"""Unit tests for tool registry, executor, and built-in tools."""

import pytest
from unittest.mock import patch

from harness.tools.base import BaseTool
from harness.tools.registry import ToolRegistry
from harness.tools.executor import ToolExecutor
from harness.tools.builtin.file_ops import FileReader, FileWriter
from harness.tools.builtin.shell_executor import ShellExecutor
from harness.tools.builtin.code_executor import CodeExecutor
from harness.models.tool import ToolResult


# ─── ToolRegistry ─────────────────────────────────────────────

class TestToolRegistry:
    @pytest.fixture
    def registry(self):
        r = ToolRegistry()
        # Register built-in tool instances (they're registered as classes for lazy init)
        r.register(FileReader())
        r.register(FileWriter())
        r.register(CodeExecutor())
        r.register(ShellExecutor())
        from harness.tools.builtin.web_fetcher import WebFetcher
        r.register(WebFetcher())
        return r

    def test_register_builtins(self, registry):
        names = registry.list_tools()
        assert "file_reader" in names
        assert "file_writer" in names
        assert "code_executor" in names
        assert "shell_executor" in names
        assert "web_fetcher" in names

    def test_register_instance(self, registry):
        tool = FileReader()
        registry.register(tool)
        retrieved = registry.get_tool("file_reader")
        assert retrieved is tool

    def test_get_nonexistent_raises(self, registry):
        with pytest.raises(KeyError, match="Tool 'nonexistent' not found"):
            registry.get_tool("nonexistent")

    def test_get_all_definitions(self, registry):
        defs = registry.get_all_definitions()
        assert len(defs) >= 5
        for d in defs:
            assert "name" in d
            assert "description" in d
            assert "input_schema" in d

    def test_register_from_config(self, registry):
        config = {"name": "file_reader", "config": {"allowed_extensions": [".log"], "max_file_size_mb": 5}}
        registry.register_from_config(config)
        tool = registry.get_tool("file_reader")
        assert tool.allowed_extensions == [".log"]
        assert tool.max_file_size_mb == 5

    def test_register_from_config_unknown_tool(self, registry):
        with pytest.raises(ValueError, match="Unknown tool"):
            registry.register_from_config({"name": "nonexistent_tool"})


# ─── ToolExecutor ─────────────────────────────────────────────

class TestToolExecutor:
    @pytest.fixture
    def executor(self):
        registry = ToolRegistry()
        return ToolExecutor(registry)

    async def test_execute_unknown_tool(self, executor):
        result = await executor.execute("nonexistent", {})
        assert result.success is False
        assert "not found" in result.error

    async def test_execute_shell_with_empty_allowlist(self, executor):
        result = await executor.execute("shell_executor", {"command": "echo hello", "timeout_seconds": 60})
        assert result.success is False


# ─── FileReader ───────────────────────────────────────────────

class TestFileReader:
    @pytest.fixture
    def reader(self):
        return FileReader()

    async def test_read_existing_file(self, reader, tmp_path):
        test_file = tmp_path / "test.txt"
        test_file.write_text("hello world", encoding="utf-8")
        result = await reader.execute(path=str(test_file))
        assert result.success is True
        assert result.data["content"] == "hello world"

    async def test_read_nonexistent_file(self, reader):
        result = await reader.execute(path="/nonexistent/file.txt")
        assert result.success is False
        assert "not found" in result.error

    async def test_disallowed_extension(self, reader, tmp_path):
        test_file = tmp_path / "test.exe"
        test_file.write_text("binary")
        result = await reader.execute(path=str(test_file))
        assert result.success is False
        assert "not allowed" in result.error

    async def test_file_too_large(self, reader, tmp_path):
        reader.max_file_size_mb = 0
        test_file = tmp_path / "test.txt"
        test_file.write_text("hello")
        result = await reader.execute(path=str(test_file))
        assert result.success is False
        assert "exceeds" in result.error


# ─── FileWriter ───────────────────────────────────────────────

class TestFileWriter:
    @pytest.fixture
    def writer(self):
        return FileWriter()

    async def test_write_file(self, writer, tmp_path):
        test_file = tmp_path / "output" / "test.txt"
        result = await writer.execute(path=str(test_file), content="test content")
        assert result.success is True
        assert test_file.read_text() == "test content"

    async def test_creates_parent_directories(self, writer, tmp_path):
        test_file = tmp_path / "deep" / "nested" / "dir" / "test.txt"
        result = await writer.execute(path=str(test_file), content="nested")
        assert result.success is True
        assert test_file.exists()


# ─── ShellExecutor (security hardened) ────────────────────────

class TestShellExecutor:
    async def test_empty_allowlist_rejects_all(self):
        executor = ShellExecutor(allowed_commands=[])
        result = await executor.execute(command="echo hello")
        assert result.success is False
        assert "No allowed commands" in result.error

    async def test_allowlist_permits_allowed(self):
        executor = ShellExecutor(allowed_commands=["echo"])
        result = await executor.execute(command="echo hello")
        assert result.success is True
        assert "hello" in result.data["stdout"]

    async def test_allowlist_rejects_unknown(self):
        executor = ShellExecutor(allowed_commands=["echo"])
        result = await executor.execute(command="rm -rf /")
        assert result.success is False
        assert "not in allowlist" in result.error


# ─── CodeExecutor ─────────────────────────────────────────────

class TestCodeExecutor:
    @pytest.fixture
    def executor(self):
        return CodeExecutor()

    async def test_execute_python_code_success(self, executor):
        result = await executor.execute(
            code="print('hello world')",
            test_framework="plain",
            timeout_seconds=10,
        )
        assert result.success is True
        assert "hello world" in result.data["stdout"]

    async def test_execute_python_code_failure(self, executor):
        result = await executor.execute(
            code="raise ValueError('test error')",
            test_framework="plain",
            timeout_seconds=10,
        )
        assert result.success is False
        assert "ValueError" in result.data["stderr"]

    async def test_execute_timeout(self, executor):
        result = await executor.execute(
            code="import time; time.sleep(10)",
            test_framework="plain",
            timeout_seconds=1,
        )
        assert result.success is False
        assert "timed out" in result.error


# ─── BaseTool function definition ─────────────────────────────

class TestBaseToolFunctionDef:
    def test_get_function_definition(self):
        tool = FileReader()
        fdef = tool.get_function_definition()
        assert fdef["name"] == "file_reader"
        assert "description" in fdef
        assert "input_schema" in fdef
        assert fdef["input_schema"]["type"] == "object"
        assert "properties" in fdef["input_schema"]
