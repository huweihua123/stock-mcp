# Stock MCP Capability Contract

## 目标

将对外契约从“工具名”升级为“能力 ID（capability_id）”，避免 agent 与内部工具实现强耦合。

## 文件与入口

- 契约文件：`src/server/mcp/capabilities.json`
- 安装逻辑：`src/server/mcp/capability_contract.py`
- 服务接入：`src/server/mcp/server.py`（启动时安装）

## 契约结构

`capabilities.json` 维护以下字段：

- `contract_version`：契约版本（semver）
- `capability_stability`：稳定级别（如 `stable`）
- `capability_tag_prefix`：能力标签前缀（默认 `cap:`）
- `bindings`：`tool_name -> binding`

每个 `binding` 包含：

- `capability_id`
- `capability_version`
- `input_schema`
- `output_schema`

## 运行时行为

启动时会执行：

1. 读取并校验契约文件（版本格式、字段完整性、一致性）
2. 检查启用工具是否全部有 binding（strict 模式下缺失即失败）
3. 通过 FastMCP tool transformation 注入：
   - `cap:<capability_id>` 标签
   - `meta.capability_id/capability_version/...`
4. 注册 `list_capabilities` 工具作为能力目录

## 变更流程（必须遵守）

新增或修改工具时：

1. 先改工具实现
2. 再更新 `capabilities.json`
3. 运行 `pytest tests/test_capability_contract.py`
4. 通过后才允许合入

## 版本策略

- 工具重命名但能力不变：只改工具代码，`capability_id` 不变
- 能力语义变更（输入/输出语义变化）：提升 `capability_version`，并升级 schema id
- 破坏性变更：必须升级 `contract_version` 并同步下游消费方
