#!/usr/bin/env bash
# Build dist/indexscout.mcpb for Claude Desktop. Requires Node (npx) and uv at runtime.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
stage="$(mktemp -d)"
trap 'rm -rf "$stage"' EXIT
cp -r "$root/src" "$root/pyproject.toml" "$root/README.md" "$root/LICENSE" "$stage/"
cp "$root/mcpb/manifest.json" "$stage/manifest.json"
find "$stage" -name "__pycache__" -prune -exec rm -rf {} +
mkdir -p "$root/dist"
npx -y @anthropic-ai/mcpb@2 validate "$stage/manifest.json"
npx -y @anthropic-ai/mcpb@2 pack "$stage" "$root/dist/indexscout.mcpb"
