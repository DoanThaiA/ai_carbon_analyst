#!/usr/bin/env bash
# Build gói MCP client (mcp_client/) thành wheel và đặt vào frontend/public/downloads/ để web
# phục vụ tại /downloads/<tên>.whl — URL này được dán vào config Claude Desktop của user
# (xem frontend/src/lib/claudeConnect.ts). Tăng `version` trong mcp_client/pyproject.toml VÀ
# MCP_CLIENT_VERSION trong claudeConnect.ts khi phát hành bản mới (uv suy ra phiên bản từ
# tên file wheel và cache theo URL, nên URL mới = buộc máy user tải bản mới).
set -euo pipefail
cd "$(dirname "$0")/.."

rm -rf mcp_client/dist
(cd mcp_client && uv build --wheel)

mkdir -p frontend/public/downloads
cp mcp_client/dist/*.whl frontend/public/downloads/
ls -l frontend/public/downloads/*.whl
