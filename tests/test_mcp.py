"""Checks the MCP server's tools in-process."""
import asyncio
import json

from changeguard.mcp_server import server

ZD = {"kind": "new_addon", "summary": "Zero Dep", "addon_code": "ZD", "max_car_age_years": 5,
      "price_pct_of_od": 0.15, "claim_limit_per_year": 2, "allow_midterm": True}


def _call(name, args):
    return asyncio.run(server.call_tool(name, args))


def test_tools_listed():
    names = {t.name for t in asyncio.run(server.list_tools())}
    assert {"list_rules", "impact_of_change", "generate_tests", "run_change", "analyze_request"} <= names


def test_run_change_via_mcp():
    out = _call("run_change", {"change": ZD})
    data = json.loads(out.content[0].text)
    assert data["passed"] == data["total"] and data["total"] > 30
