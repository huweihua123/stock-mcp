# src/server.utils/mcp_logger.py
"""MCP 工具调用日志装饰器

为所有 MCP 工具统一添加调用日志，记录：
- 工具名称
- 输入参数
- 执行结果摘要
- 错误信息

使用方法：
    from src.server.utils.mcp_logger import log_mcp_tool_call

    @mcp.tool()
    @log_mcp_tool_call
    async def my_tool(param1: str, param2: int, ctx: Context) -> dict:
        ...

注意：
- 如果工具函数有 ctx: Context 参数，装饰器会自动使用 ctx 向客户端发送日志
- 否则只记录到服务器本地日志
"""

from functools import wraps
from typing import Any, Callable

try:
    from fastmcp import Context

    CONTEXT_AVAILABLE = True
except ImportError:
    CONTEXT_AVAILABLE = False
    Context = None

from src.server.utils.logger import logger


def log_mcp_tool_call(func: Callable) -> Callable:
    """
    MCP 工具调用日志装饰器

    记录工具调用的入口、参数、结果摘要和错误信息

    如果工具函数有 ctx: Context 参数，会向客户端发送日志
    """

    @wraps(func)
    async def wrapper(*args, **kwargs) -> Any:
        tool_name = func.__name__

        # 提取 Context（如果存在）
        ctx = kwargs.get("ctx") if CONTEXT_AVAILABLE else None

        # 构建参数字典（排除 ctx）
        params = {k: v for k, v in kwargs.items() if k != "ctx"}

        # 🔧 记录工具调用入口
        logger.info(
            f"🔧 [MCP TOOL CALL] {tool_name}",
            tool=tool_name,
            params=params,
        )

        # 向客户端发送 info 日志
        if ctx:
            await ctx.info(
                f"调用工具 {tool_name}", extra={"tool": tool_name, "params": params}
            )

        try:
            # 执行实际工具函数
            result = await func(*args, **kwargs)

            # ✅ 记录成功结果摘要
            result_summary = _get_result_summary(result)
            logger.info(
                f"✅ [MCP TOOL RESULT] {tool_name} success",
                tool=tool_name,
                result_summary=result_summary,
            )

            # 向客户端发送成功日志
            if ctx:
                await ctx.info(
                    f"工具 {tool_name} 执行成功",
                    extra={"tool": tool_name, "result_summary": result_summary},
                )

            return result

        except Exception as e:
            # ❌ 记录错误信息
            logger.error(
                f"❌ [MCP TOOL ERROR] {tool_name} failed",
                tool=tool_name,
                error=str(e),
                error_type=type(e).__name__,
                params=params,
                exc_info=True,
            )

            # 向客户端发送错误日志
            if ctx:
                await ctx.error(
                    f"工具 {tool_name} 执行失败: {str(e)}",
                    extra={
                        "tool": tool_name,
                        "error": str(e),
                        "params": params,
                    },
                )

            raise

    return wrapper


def _get_result_summary(result: Any) -> str:
    """
    生成结果摘要，避免日志过大

    Args:
        result: 工具返回结果

    Returns:
        结果摘要字符串
    """
    if result is None:
        return "None"

    if isinstance(result, dict):
        keys = list(result.keys())
        structured = result.get("structuredContent")
        if isinstance(structured, dict):
            resources = structured.get("resources")
            if isinstance(resources, list) and resources:
                first = resources[0] if isinstance(resources[0], dict) else {}
                return (
                    f"MCPResult(resources={len(resources)}, "
                    f"uri={first.get('uri')}, mimeType={first.get('mimeType')})"
                )
            if result.get("isError"):
                error = structured.get("error") if isinstance(structured.get("error"), dict) else {}
                return f"MCPError(code={error.get('code')}, message={error.get('message')})"

        return f"dict with keys: {keys}"

    if isinstance(result, list):
        # 列表类型：返回长度和第一个元素类型
        if len(result) == 0:
            return "empty list"
        first_item_type = type(result[0]).__name__
        return f"list[{first_item_type}] with {len(result)} items"

    # 其他类型：返回类型名
    return f"{type(result).__name__}"
