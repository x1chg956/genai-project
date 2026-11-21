from mcp_servers.common.runner import run
from mcp_servers.regulatory_kb.server import build_from_settings

run("regulatory_kb", build_from_settings)
