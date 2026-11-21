from mcp_servers.common.runner import run
from mcp_servers.transactions.server import build_from_settings

run("transactions", build_from_settings)
