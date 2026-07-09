from __future__ import annotations

"""Incident timeline retrieval example.

Run an Attemory server first, for example:

    attemory-server --large --backend gpu --port 9006

Then run:

    python examples/incident_timeline.py
"""

import argparse
from pathlib import Path

from attemory import AttemoryClient, AttemoryHTTPError, MemoryInput


SYSTEM_PROMPT = (
    "Read the incident timeline carefully. Use only facts stated in the timeline."
)


def load_timeline(path: Path) -> list[tuple[str, MemoryInput]]:
    memories: list[tuple[str, MemoryInput]] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        memory_id = line.split(" ", 1)[0]
        memories.append((memory_id, MemoryInput(id=memory_id, text=line)))
    return memories


def print_timeline(memories: list[tuple[str, MemoryInput]]) -> None:
    print("== Incident timeline ==")
    for memory_id, memory in memories:
        print(f"{memory_id}: {memory.text}")


def print_results(
    title: str,
    query: str,
    query_context: str | None,
    label: str,
    results,
) -> None:
    print(f"\n== {title} ==")
    print(f"query: {query}")
    if query_context is not None:
        print(f"query_context: {query_context}")
    print("--- Results ---")
    for result in results:
        if result.id is None:
            continue
        preview = result.text.replace("\n", " ")
        marker = " ✓" if result.id == label else ""
        print(f"rank={result.rank} id={result.id} text={preview}{marker}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Attemory incident timeline example")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9006)
    parser.add_argument("--session-id", default="incident-timeline")
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()

    timeline_path = Path(__file__).with_name("incident_timeline.md")
    memories = load_timeline(timeline_path)
    print_timeline(memories)

    client = AttemoryClient(host=args.host, port=args.port, session_id=args.session_id)
    try:
        client.delete_session()
    except AttemoryHTTPError:
        pass
    client.create_session()
    client.add_system(SYSTEM_PROMPT)

    for _, memory in memories:
        client.add_memory(memory)

    client.index_session()

    blank_dashboard_query = "What physical infrastructure issue caused the dashboard outage?"
    shared_root_cause_context = (
        "The user's query: {query}\n"
        "Focus on the earliest physical facility event behind the outage rather than component failure or recovery."
    )
    blank_dashboard_context = shared_root_cause_context.format(query=blank_dashboard_query)
    blank_dashboard_label = "10:02"
    blank_dashboard_results = client.search(
        blank_dashboard_query,
        query_context=blank_dashboard_context,
        top_k=args.top_k,
    )
    print_results(
        "Root cause: dashboard outage",
        blank_dashboard_query,
        blank_dashboard_context,
        blank_dashboard_label,
        blank_dashboard_results,
    )

    reset_link_query = "What physical infrastructure issue caused the password reset outage?"
    reset_link_context = shared_root_cause_context.format(query=reset_link_query)
    reset_link_label = "10:05"
    reset_link_results = client.search(
        reset_link_query,
        query_context=reset_link_context,
        top_k=args.top_k,
    )
    print_results(
        "Root cause: password reset outage",
        reset_link_query,
        reset_link_context,
        reset_link_label,
        reset_link_results,
    )


if __name__ == "__main__":
    main()
