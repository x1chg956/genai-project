from mcp_servers.common.runner import run
from mcp_servers.filing_portal.server import build_from_settings

run("filing_portal", build_from_settings)
