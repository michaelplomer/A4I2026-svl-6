"""Private stdio MCP server for the HeatRx agent."""

from mcp.server.fastmcp import FastMCP

from .heat_agent import (
    explain_spot,
    local_heat_history,
    model_benchmark,
    outreach_equity,
    outreach_plan,
    shortlist_blocks,
    station_heat_history,
    tract_evidence,
)
from .weather import brooklyn_hourly_weather, lookup_brooklyn_address

server = FastMCP("HeatRx Brooklyn evidence")

for tool in (
    explain_spot, tract_evidence, outreach_plan, station_heat_history, model_benchmark,
    outreach_equity, shortlist_blocks, local_heat_history,
    lookup_brooklyn_address, brooklyn_hourly_weather,
):
    server.add_tool(tool)

if __name__ == "__main__":
    server.run(transport="stdio")
