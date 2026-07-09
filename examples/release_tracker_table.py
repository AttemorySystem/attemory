from __future__ import annotations

"""Release tracker relationship + table-cell retrieval example.

Run an Attemory server first, for example:

    attemory-server --large --backend gpu --port 9006

Then run:

    python examples/release_tracker_table.py
"""

import argparse
import re
from pathlib import Path

from attemory import AttemoryClient, AttemoryHTTPError, MemoryInput


SYSTEM_PROMPT = (
    "Read the following notes and table carefully and answer the query at the end."
)

QUERY_CONTEXT = (
    "The user's query: {query}\n"
    "Use notes as context for table cells."
)


def split_markdown_row(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def is_separator_row(cells: list[str]) -> bool:
    return all(re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in cells)


def load_release_tracker(path: Path) -> tuple[list[str], list[list[str]], list[tuple[str, MemoryInput]]]:
    notes: list[str] = []
    rows: list[list[str]] = []
    section: str | None = None

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("## "):
            section = line[3:].strip().lower()
            continue
        if section == "notes" and not line.startswith("|") and not line.startswith("#"):
            notes.append(line)
            continue
        if not line.startswith("|") or not line.endswith("|"):
            continue
        cells = split_markdown_row(line)
        if not cells or is_separator_row(cells):
            continue
        rows.append(cells)

    if not rows:
        raise ValueError(f"no markdown table found in {path}")

    width = len(rows[0])
    for row_number, row in enumerate(rows, start=1):
        if len(row) != width:
            raise ValueError(
                f"table row {row_number} has {len(row)} cells; expected {width}"
            )

    memories: list[tuple[str, MemoryInput]] = [
        (f"n{index}", MemoryInput(id=f"n{index}", text=note))
        for index, note in enumerate(notes)
    ]

    for row_index, row in enumerate(rows):
        for column_index, cell in enumerate(row):
            # Each table cell is intentionally stored as its own memory. The
            # cell text does not repeat the row or column label; Attemory can
            # still use the surrounding header cells, row cells, and notes from
            # the indexed sequence to recover table structure during search.
            memory_id = (
                f"h{column_index}"
                if row_index == 0
                else f"r{row_index - 1}c{column_index}"
            )
            memories.append(
                (
                    memory_id,
                    MemoryInput(
                        id=memory_id,
                        text=f"| {cell}",
                    ),
                )
            )

    return notes, rows, memories


def print_raw_table(path: Path) -> None:
    print("== Release tracker ==")
    print(path.read_text(encoding="utf-8").strip())


def print_memories(memories: list[tuple[str, MemoryInput]]) -> None:
    print("\n== Memories passed to Attemory ==")
    for index, (memory_id, memory) in enumerate(memories):
        print(f"{index:02d} {memory_id:>4} {memory.text}")


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
    parser = argparse.ArgumentParser(description="Attemory release tracker table example")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9006)
    parser.add_argument("--session-id", default="release-tracker-table")
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()

    table_path = Path(__file__).with_name("release_tracker_table.md")
    _, _, memories = load_release_tracker(table_path)
    print_raw_table(table_path)
    print_memories(memories)

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

    date_query = "What is the target date for Echo Mobile?"
    date_label = "r4c5"
    date_results = client.search(date_query, top_k=args.top_k)
    print_results("Direct table lookup", date_query, None, date_label, date_results)

    mentor_status_query = "What is the Current Status cell for the row whose owner mentors Omar?"
    mentor_status_label = "r2c2"
    QC = QUERY_CONTEXT.format(query=mentor_status_query)
    mentor_status_results = client.search(
        mentor_status_query,
        query_context=QC,
        top_k=args.top_k,
    )
    print_results(
        "Relationship lookup: mentor current status",
        mentor_status_query,
        QC,
        mentor_status_label,
        mentor_status_results,
    )

    security_query = "What target date is tied to the security review?"
    security_label = "r2c5"
    QC = QUERY_CONTEXT.format(query=security_query)
    security_results = client.search(
        security_query,
        query_context=QC,
        top_k=args.top_k,
    )
    print_results(
        "Relationship lookup: security target date",
        security_query,
        QC,
        security_label,
        security_results,
    )

    finance_status_query = "What current status is tied to the finance review?"
    finance_status_label = "r3c2"
    QC = QUERY_CONTEXT.format(query=finance_status_query)
    finance_status_results = client.search(
        finance_status_query,
        query_context=QC,
        top_k=args.top_k,
    )
    print_results(
        "Relationship lookup: finance current status",
        finance_status_query,
        QC,
        finance_status_label,
        finance_status_results,
    )


if __name__ == "__main__":
    main()
