#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
PYTHON_ROOT = ROOT / "python"
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from attemory import AttemoryClient, AttemoryHTTPError, MemoryInput, SearchResult


SYSTEM_PROMPT = """Read the following conversation and answer the query at the end.
"""

RERANK_SYSTEM_PROMPT = (
    "Read the following selected conversation snippets and find the most relevant evidence "
    "to the query at the end."
)

DEFAULT_TOP_K = 50
MAX_RERANK_ROUNDS = 8


@dataclass
class RecallData:
    top5: int = 0
    top10: int = 0
    top25: int = 0
    top50: int = 0
    n_evd: int = 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run LoCoMo retrieval benchmark with Attemory.")
    parser.add_argument("input", type=Path, help="path to LoCoMo locomo10.json input data")
    parser.add_argument("--host", default="127.0.0.1", help="Attemory server host")
    parser.add_argument("--port", type=int, default=9006, help="Attemory server port")
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K, help="number of search results")
    parser.add_argument(
        "--reuse-index",
        action="store_true",
        help="restore an existing session if present instead of rebuilding it",
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="delete an existing benchmark session before rebuilding it",
    )
    parser.add_argument(
        "--no-rerank",
        action="store_true",
        help="skip recursive one-shot reranking when search hits multiple segments",
    )
    return parser.parse_args()


def load_json(path: Path) -> list[Mapping[str, Any]]:
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("LoCoMo input must be a JSON array")
    return [item for item in data if isinstance(item, Mapping)]


def session_id(sample: Mapping[str, Any]) -> str:
    return "locomo-" + str(sample.get("sample_id", "unknown"))


def session_keys(conversation: Mapping[str, Any]) -> list[str]:
    keys: list[str] = []
    for idx in range(1, 100):
        key = f"session_{idx}"
        if not conversation.get(key):
            break
        keys.append(key)
    return keys


def expected_memory_count(conversation: Mapping[str, Any]) -> int:
    count = 0
    for key in session_keys(conversation):
        session = conversation.get(key, [])
        count += 1
        if isinstance(session, list):
            count += sum(isinstance(dialogue, Mapping) for dialogue in session)
    return count


def dialogue_text(dialogue: Mapping[str, Any]) -> str:
    text = f"{dialogue.get('speaker')}: {dialogue.get('text')}"
    image_url = dialogue.get("img_url")
    if image_url:
        text += f" {image_url}({dialogue.get('blip_caption')})"
    return text


def build_memory_map_only(conversation: Mapping[str, Any]) -> dict[str, str]:
    memory_to_dialogue: dict[str, str] = {}
    memory_idx = 0
    for key in session_keys(conversation):
        memory_to_dialogue[str(memory_idx)] = key + "_header"
        memory_idx += 1

        session = conversation.get(key, [])
        if not isinstance(session, list):
            continue
        for dialogue in session:
            if not isinstance(dialogue, Mapping):
                continue
            memory_to_dialogue[str(memory_idx)] = str(dialogue.get("dia_id"))
            memory_idx += 1
    return memory_to_dialogue


def build_index(
    client: AttemoryClient,
    sid: str,
    conversation: Mapping[str, Any],
) -> dict[str, str]:
    memory_to_dialogue: dict[str, str] = {}

    client.create_session()
    client.add_system(SYSTEM_PROMPT)
    memory_count = 0

    for key in session_keys(conversation):
        session_time = conversation.get(key + "_date_time")
        session_header = f"The following conversation take place at {session_time}"
        memory_id = str(memory_count)
        client.add_memory(session_header, id=memory_id)
        memory_to_dialogue[memory_id] = key + "_header"
        memory_count += 1

        session = conversation.get(key, [])
        if not isinstance(session, list):
            continue
        for dialogue in session:
            if not isinstance(dialogue, Mapping):
                continue

            memory_id = str(memory_count)
            client.add_memory(dialogue_text(dialogue), id=memory_id)
            memory_to_dialogue[memory_id] = str(dialogue.get("dia_id"))
            memory_count += 1

    start_time = time.perf_counter()
    client.index_session()
    elapsed = time.perf_counter() - start_time
    print(f"{sid} index {memory_count} memories used: {elapsed} seconds")
    print(client.save_session())
    return memory_to_dialogue


def delete_session_if_exists(client: AttemoryClient) -> None:
    try:
        client.delete_session()
    except AttemoryHTTPError as exc:
        if exc.status_code != 404:
            raise


def restore_session_if_exists(client: AttemoryClient) -> dict[str, Any] | None:
    try:
        return client.restore_session()
    except AttemoryHTTPError as exc:
        if exc.status_code == 404:
            return None
        raise


def prepare_session(
    client: AttemoryClient,
    sid: str,
    conversation: Mapping[str, Any],
    args: argparse.Namespace,
) -> dict[str, str]:
    if args.rebuild or not args.reuse_index:
        delete_session_if_exists(client)

    if args.reuse_index and not args.rebuild:
        restored = restore_session_if_exists(client)
        if restored is not None:
            expected = expected_memory_count(conversation)
            actual = int(restored.get("memory_count", -1))
            if actual != expected:
                raise RuntimeError(
                    f"{sid} restored memory_count={actual}, expected={expected}; "
                    "rerun with --rebuild to recreate this benchmark session"
                )

            segment_count = int(restored.get("segment_count", 0))
            indexed_segments = int(restored.get("indexed_segments", 0))
            if indexed_segments < segment_count:
                start_time = time.perf_counter()
                client.index_session()
                elapsed = time.perf_counter() - start_time
                print(f"{sid} restored index used: {elapsed} seconds")
            return build_memory_map_only(conversation)

    return build_index(client, sid, conversation)


def result_segment_ids(results: list[SearchResult]) -> set[int]:
    return {result.segment_id for result in results if result.segment_id is not None}


def result_memories(results: list[SearchResult]) -> list[MemoryInput]:
    return [
        MemoryInput(id=result.id, text=result.text)
        for result in results
        if result.id is not None
    ]


def recursive_oneshot_search(
    client: AttemoryClient,
    query: str,
    memories: list[MemoryInput],
    *,
    top_k: int,
    max_rounds: int = MAX_RERANK_ROUNDS,
) -> list[SearchResult]:
    candidates = memories
    latest_results: list[SearchResult] = []

    for _ in range(max_rounds):
        latest_results = client.oneshot_search(
            query,
            candidates,
            system=RERANK_SYSTEM_PROMPT,
            top_k=top_k,
        )
        if len(result_segment_ids(latest_results)) <= 1:
            break

        candidates = result_memories(latest_results)
        if not candidates:
            break

    return latest_results[:top_k]


def ranked_memory_ids(
    client: AttemoryClient,
    results: list[SearchResult],
    query: str,
    *,
    top_k: int,
    rerank: bool,
) -> list[str]:
    if rerank and len(result_segment_ids(results)) > 1:
        memories = result_memories(results)
        if memories:
            results = recursive_oneshot_search(
                client,
                query,
                memories,
                top_k=top_k,
            )

    return [result.id for result in results if result.id is not None]


def evidence_ids(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def should_skip_question(question: Mapping[str, Any]) -> bool:
    category = question.get("category")
    try:
        return int(category) == 5
    except (TypeError, ValueError):
        return False


def apply_recall(recall: RecallData, evidence: list[str], ranked_dialogues: list[str]) -> None:
    recall.n_evd += len(evidence)
    for item in evidence:
        if item in ranked_dialogues[:5]:
            recall.top5 += 1
        if item in ranked_dialogues[:10]:
            recall.top10 += 1
        if item in ranked_dialogues[:25]:
            recall.top25 += 1
        if item in ranked_dialogues[:50]:
            recall.top50 += 1


def ratio(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return numerator / denominator


def run_sample(
    client: AttemoryClient,
    sample: Mapping[str, Any],
    args: argparse.Namespace,
    recall_by_session: defaultdict[str, RecallData],
) -> None:
    sid = session_id(sample)
    conversation = sample.get("conversation")
    if not isinstance(conversation, Mapping):
        raise ValueError(f"{sid} conversation must be a JSON object")

    session_client = client.with_session(sid)
    memory_to_dialogue = prepare_session(session_client, sid, conversation, args)

    qa = sample.get("qa", [])
    if not isinstance(qa, list):
        return

    for question in qa:
        if not isinstance(question, Mapping) or should_skip_question(question):
            continue

        evidence = evidence_ids(question.get("evidence"))
        query = f"\nQuery: {question.get('question')}"
        results = session_client.search(query, top_k=args.top_k)
        ranked_ids = ranked_memory_ids(
            session_client,
            results,
            query,
            top_k=args.top_k,
            rerank=not args.no_rerank,
        )
        ranked_dialogues = [memory_to_dialogue.get(memory_id, "") for memory_id in ranked_ids]
        apply_recall(recall_by_session[sid], evidence, ranked_dialogues)
        print(f"{sid} {recall_by_session[sid]}")


def print_summary(recall_by_session: Mapping[str, RecallData]) -> None:
    acc_top5 = 0
    acc_top10 = 0
    acc_top25 = 0
    acc_top50 = 0
    acc_all = 0

    print(
        "docid, n_top5, n_top10, n_top25, n_top50, all_evidence, "
        "top5%, top10%, top25%, top50%"
    )
    for sid, recall in recall_by_session.items():
        acc_top5 += recall.top5
        acc_top10 += recall.top10
        acc_top25 += recall.top25
        acc_top50 += recall.top50
        acc_all += recall.n_evd

        print(
            f"{sid}, {recall.top5}, {recall.top10}, {recall.top25}, "
            f"{recall.top50}, {recall.n_evd}, "
            f"{ratio(recall.top5, recall.n_evd)}, "
            f"{ratio(recall.top10, recall.n_evd)}, "
            f"{ratio(recall.top25, recall.n_evd)}, "
            f"{ratio(recall.top50, recall.n_evd)}"
        )

    print(
        "all",
        acc_top5,
        acc_top10,
        acc_top25,
        acc_top50,
        acc_all,
        ratio(acc_top5, acc_all),
        ratio(acc_top10, acc_all),
        ratio(acc_top25, acc_all),
        ratio(acc_top50, acc_all),
    )


def main() -> None:
    args = parse_args()
    data = load_json(args.input)
    client = AttemoryClient(host=args.host, port=args.port)

    start_time = time.perf_counter()
    recall_by_session: defaultdict[str, RecallData] = defaultdict(RecallData)
    for sample in data:
        run_sample(client, sample, args, recall_by_session)
    elapsed = time.perf_counter() - start_time

    print_summary(recall_by_session)
    print(f"Time used: {elapsed} seconds")


if __name__ == "__main__":
    main()
