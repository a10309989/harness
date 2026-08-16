"""Manual end-to-end smoke test for the log-analysis pipeline."""

import json

import httpx

BASE = "http://localhost:8001/api/v1"


def main() -> None:
    response = httpx.post(f"{BASE}/sessions", json={})
    response.raise_for_status()
    session = response.json()
    print("=== Session ===")
    print(f"ID: {session['session_id']}")

    message = (
        "My regression test failed with NullPointerException and timeout errors. "
        "Analyze the logs and identify the root cause."
    )
    result_response = httpx.post(
        f"{BASE}/sessions/{session['session_id']}/message",
        json={"message": message},
    )
    result_response.raise_for_status()
    print(json.dumps(result_response.json(), indent=2, ensure_ascii=False))

    conversation_response = httpx.get(f"{BASE}/sessions/{session['session_id']}")
    conversation_response.raise_for_status()
    for turn in conversation_response.json()["conversation"]:
        content = turn["content"]
        preview = content[:150] + "..." if len(content) > 150 else content
        print(f"  [{turn['role']}] {preview}")


if __name__ == "__main__":
    main()
