from harness.api.config_store import ConfigStore


async def test_set_agent_mcp_bindings_round_trips(db):
    store = ConfigStore(db)

    await store.set_agent_mcp_bindings(
        "agent-1",
        {
            "server-a": {"tool_filter": ["tool_a", "tool_b"]},
            "server-b": {"tool_filter": ["tool_c"]},
        },
    )

    bindings = await store.get_agent_mcp_bindings("agent-1")
    assert bindings["server-a"]["tool_filter"] == ["tool_a", "tool_b"]
    assert bindings["server-b"]["tool_filter"] == ["tool_c"]


async def test_set_agent_mcp_bindings_atomically_replaces(db):
    store = ConfigStore(db)

    await store.set_agent_mcp_bindings("agent-1", {"server-a": {"tool_filter": ["x"]}})
    await store.set_agent_mcp_bindings("agent-1", {"server-b": {"tool_filter": ["y"]}})

    # Old binding must be gone after the atomic replace, not merged.
    bindings = await store.get_agent_mcp_bindings("agent-1")
    assert set(bindings) == {"server-b"}
    assert bindings["server-b"]["tool_filter"] == ["y"]


async def test_set_agent_mcp_bindings_empty_clears_all(db):
    store = ConfigStore(db)

    await store.set_agent_mcp_bindings("agent-1", {"server-a": {"tool_filter": ["x"]}})
    await store.set_agent_mcp_bindings("agent-1", {})

    assert await store.get_agent_mcp_bindings("agent-1") == {}