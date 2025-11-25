# filepath: /Users/huweihua/java/stock-mcp/start_nacos_mcp.sh
# 启动 Nacos MCP 服务器

echo "🚀 Starting Stock MCP Server with Nacos Auto-Registration..."
echo ""

# Activate conda environment (use source instead of conda activate)
source ~/anaconda3/etc/profile.d/conda.sh
conda activate valuecell

# 设置环境变量（可选，也可以在 .env 文件中配置）
# export NACOS_SERVER_ADDR="127.0.0.1:8848"
# export NACOS_NAMESPACE="public"
# export NACOS_USERNAME="nacos"
# export NACOS_PASSWORD="nacos"
# export NACOS_MCP_TRANSPORT="sse"  # 可选: stdio, sse, streamable-http

# 启动 Nacos MCP 服务器
python -m src.server.app
