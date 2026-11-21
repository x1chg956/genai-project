"""Tool kind labels (docs/05, docs/11 §8): every tool is read, compute or write.

The kind is a FastMCP tag; read and compute tools also set the MCP `readOnlyHint`. The tool
catalogue generator checks each tag against the R/C/W column in docs/05.
"""

from __future__ import annotations

from mcp.types import ToolAnnotations

READ_ONLY = ToolAnnotations(readOnlyHint=True)
WRITES = ToolAnnotations(readOnlyHint=False)

KINDS = {"read": "R", "compute": "C", "write": "W"}
