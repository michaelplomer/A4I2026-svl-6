"""Optional multi-agent mode (HEAT_AGENT_MODE=team): a router with two track specialists and a
council reviewer. The default single agent in agent.py is unchanged; set the env var to switch.

heat_planner (router: simple block questions + BigQuery MCP)
├── dispatch_agent   ⚡ Today: forecast, dispatch_plan, shortlist, equity, recommendation
└── planning_agent   🌳 Decade: replacement, planting, Street View, equity, recommendation
      └── council_skeptic (AgentTool): stress-tests a council memo with model_card

Every agent keeps the map (UI) tools, so the portal still moves the map whichever agent answers.
"""
from __future__ import annotations

from google.adk.agents import Agent
from google.adk.tools.agent_tool import AgentTool

from .bigquery_mcp import bigquery_mcp
from .data_tools import (equity_audit, get_block, local_heat_history, model_card, shortlist_blocks,
                         station_heat_history, tract_evidence, demo_cooling_access)
from .hydration_tools import HYDRATION_TOOLS
from .live_tools import brooklyn_hourly_weather, lookup_brooklyn_address, now_in_new_york
from .model_tools import compare_model_predictions, model_registry
from .ops_tools import (dispatch_plan, get_forecast, inspect_site, placement_recommendation,
                        rank_planting_sites, replacement_priority)
from .toolbox_mcp import make_toolbox_mcp
from .ui_tools import UI_TOOLS

DISPATCH_ROLE = """
## Your role: Dispatch agent (Today track, next 24-72 hours)
- First collect: forecast high (get_forecast, or the planner's number), trucks/teams, stops or
  hours, area. Then dispatch_plan. On "truck 2 broke down" call dispatch_plan again with fewer
  trucks and report what_changed.
- Present zones -> numbered stops (near-address, neighbourhood, district, est. block high,
  65+ and no-car counts, confidence) with each zone's route link; highlight the cells on the map.
- Always run equity_audit on the plan's cells and report it in one or two lines.
- Formal deliverable: placement_recommendation(track='today').
- If a block keeps topping the list, give its est. days >=95F per year and offer the Decade track
  (transfer to planning_agent).
- Never say a cooling center is open now: point to NYC's Cooling Center Finder / 311.
"""

PLANNING_ROLE = """
## Your role: Planning agent (Decade track: trees, cool roofs/pavement, council)
- Replacement first: replacement_priority lists dead trees and stumps (existing pits) with exact
  coordinates on hot, exposed blocks. New planting: rank_planting_sites.
- For each recommended site, use get_block or placement_recommendation: physical drivers vs.
  location context ('nbr_offset_1km' = nearby measured streets). Recommend actions only from
  physical drivers; if context dominates, say the model cannot isolate a physical cause.
- inspect_site on the top 3 when Street View is configured; report what it saw.
- Always run equity_audit on recommended cells; if the plan reaches less-exposed residents than
  Brooklyn overall, say so and offer an exposure-weighted re-rank.
- Council memo: placement_recommendation(track='decade'), then a one-page memo (ask, sites, why,
  who benefits, cost only if sourced, equity, limitations, next steps). Before finalising, ask
  council_skeptic to review the draft and answer its questions.
- Never quantify future cooling ("500 trees -> X F by 2035").
"""

ROUTER_ROLE = """
## Your role: router
- Today questions (trucks, wellness checks, outreach, alerts, extended hours) -> transfer to
  dispatch_agent. Decade questions (planting, replacement, cool roofs/pavement, budgets, council
  memos) -> transfer to planning_agent.
- Answer single-block, history and model questions yourself (get_block, local_heat_history,
  model_card) and use the BigQuery MCP tools for anything else (read-only).
"""

SKEPTIC = """You are a sceptical NYC Council member reviewing a Brooklyn heat-intervention proposal.
Ask the 3 hardest fair questions (evidence, uncertainty, equity, cost, 'why not my district').
Use model_card for the model's real accuracy and limits. Numbered, one line each on what a good
answer must show."""


def _toolbox() -> list:
    t = make_toolbox_mcp()
    return [t] if t is not None else []


def build_team(model: str, base_instruction: str) -> Agent:
    council_skeptic = Agent(name="council_skeptic", model=model, instruction=SKEPTIC,
                            description="Stress-tests a draft council proposal.",
                            tools=[model_card, model_registry])
    dispatch_agent = Agent(
        name="dispatch_agent", model=model,
        description="Today track: hydration trucks, wellness checks, outreach and alerts for a hot day.",
        instruction=base_instruction + DISPATCH_ROLE,
        tools=[get_block, shortlist_blocks, get_forecast, dispatch_plan, equity_audit,
               now_in_new_york, lookup_brooklyn_address, brooklyn_hourly_weather, *HYDRATION_TOOLS,
               placement_recommendation, tract_evidence, demo_cooling_access, model_card,
               *_toolbox(), *UI_TOOLS],
    )
    planning_agent = Agent(
        name="planning_agent", model=model,
        description="Decade track: tree replacement and planting, cool roof/pavement candidates, council memos.",
        instruction=base_instruction + PLANNING_ROLE,
        tools=[get_block, shortlist_blocks, replacement_priority, rank_planting_sites, inspect_site,
               lookup_brooklyn_address, compare_model_predictions,
               local_heat_history, equity_audit, placement_recommendation, model_card,
               AgentTool(agent=council_skeptic), *_toolbox(), *UI_TOOLS],
    )
    return Agent(
        name="heat_planner", model=model,
        description="HeatRx: answers city planners' questions about street-level heat in Brooklyn.",
        instruction=base_instruction + ROUTER_ROLE,
        tools=[get_block, local_heat_history, station_heat_history, model_card, model_registry,
               compare_model_predictions, now_in_new_york, lookup_brooklyn_address,
               brooklyn_hourly_weather, bigquery_mcp, *_toolbox(), *UI_TOOLS],
        sub_agents=[dispatch_agent, planning_agent],
    )
