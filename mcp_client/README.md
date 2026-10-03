# carbon-analyst-mcp

MCP server cục bộ cho Claude Desktop: lấy ngữ cảnh báo cáo Carbon Analyst (đoạn
trích bôi đen, giá, EUA, mục báo cáo, tin tức...) qua `/api/mcp-gateway/*` bằng
API token cá nhân. Chỉ đọc; kết quả Claude sinh ra không được lưu ngược lại hệ thống.

Kiểm tra token: `CARBON_API_URL=... CARBON_API_TOKEN=... carbon-analyst-mcp --check`

Config Claude Desktop (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "carbon-analyst": {
      "command": "uvx",
      "args": ["--from", "<URL file .whl>", "carbon-analyst-mcp"],
      "env": { "CARBON_API_URL": "https://sim.mcv.network", "CARBON_API_TOKEN": "cat_..." }
    }
  }
}
```

Trên macOS dùng đường dẫn tuyệt đối cho `command` (`which uvx`) — app desktop không thừa hưởng PATH của shell.
