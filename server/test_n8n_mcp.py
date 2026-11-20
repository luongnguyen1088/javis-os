import pytest
import sys
from pathlib import Path

# Add server to path
SERVER_DIR = Path(__file__).parent
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

import mcp_catalog
import mcp_client
import n8n_mcp


def test_n8n_catalog_entry():
    cat = mcp_catalog.load()
    assert "n8n" in cat
    connector = cat["n8n"]
    assert connector["name"] == "n8n Automation"
    assert connector["transport"] == "internal"
    assert connector["internal"] == "n8n"
    assert "instance_url" in [f["key"] for f in connector["auth"]["fields"]]
    assert "api_key" in [f["key"] for f in connector["auth"]["fields"]]


@pytest.mark.anyio
async def test_n8n_list_tools():
    tools = await n8n_mcp.list_tools()
    assert len(tools) == 6
    names = [t["name"] for t in tools]
    assert "n8n_list_workflows" in names
    assert "n8n_get_workflow" in names
    assert "n8n_activate_workflow" in names
    assert "n8n_trigger_webhook" in names
    assert "n8n_list_executions" in names
    assert "n8n_get_execution" in names


@pytest.mark.anyio
async def test_n8n_missing_secrets():
    res = await n8n_mcp.call("n8n_list_workflows", {}, {"secrets": {}})
    assert res.get("isError") is True
    assert "requires instance_url and api_key" in res["content"][0]["text"]


@pytest.mark.anyio
async def test_n8n_unknown_tool():
    spec = {"secrets": {"instance_url": "https://auto.claro.vn", "api_key": "test"}}
    res = await n8n_mcp.call("n8n_unknown", {}, spec)
    assert res.get("isError") is True
    assert "Unknown n8n tool" in res["content"][0]["text"]


@pytest.mark.anyio
async def test_n8n_internal_session_bridge():
    spec = {"secrets": {"instance_url": "https://auto.claro.vn", "api_key": "test"}}
    sess = mcp_client.McpInternalSession("n8n", spec)
    tools = await sess.list_tools()
    assert len(tools) == 6
