from harness.api.routes.settings import (
    DEFAULT_QA_ROUTING_CONFIG,
    get_qa_routing_config,
    save_qa_routing_config,
)
from harness.api.config_store import ConfigStore


async def test_qa_routing_config_defaults_and_save(db):
    store = ConfigStore(db)

    default_response = await get_qa_routing_config(store, None)
    assert default_response["config"]["threshold"] == DEFAULT_QA_ROUTING_CONFIG["threshold"]

    saved_response = await save_qa_routing_config(
        {
            "classifier_endpoint": "http://remote/a2a",
            "threshold": 0.8,
            "intent_mappings": {"general_qa": "remote.qa_conversation"},
        },
        store,
        None,
    )

    assert saved_response["config"]["threshold"] == 0.8
    loaded_response = await get_qa_routing_config(store, None)
    assert loaded_response["config"]["classifier_endpoint"] == "http://remote/a2a"
