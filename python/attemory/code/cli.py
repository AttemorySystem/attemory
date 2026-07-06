from __future__ import annotations

import argparse
import json
import math
import shutil
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterable

from attemory.client import AttemoryClient
from attemory.exceptions import AttemoryError, AttemoryHTTPError
from attemory.models import SearchResult

from .project import (
    CONTEXT_STRUCTURE,
    FILE_HEADER_PREFIX,
    SYSTEM_PROMPT,
    ChunkRecord,
    add_to_gitignore,
    chunk_record,
    chunks_path,
    config_path,
    default_config,
    file_header,
    find_project_root,
    git_commit,
    index_path,
    IndexedFile,
    indexed_file_from_path,
    iter_indexed_files,
    load_chunks,
    load_config,
    load_index,
    read_snippet,
    remove_from_gitignore,
    scan_summary,
    write_chunks,
    write_config,
)


RERANK_SYSTEM_PROMPT = (
    "Rank the selected code snippets by how directly they answer the repository question. Prefer production "
    "source and concrete definitions or implementations. Use tests, examples, and docs when they are the "
    "best evidence for the requested behavior."
)
QUERY_CONTEXT_INTRO_PROMPT = (
    "Read the above code snippets carefully and answer the user's repository question: {question}."
)
QUERY_CONTEXT_DEFAULT_GUIDANCE = (
    "Treat the query as code navigation. Rank snippets by how directly they define, declare, implement, "
    "export, configure, or explain the requested symbol, API, behavior, or feature. Prefer exact identifier "
    "matches and concrete implementation code over snippets that only mention, call, test, or document it."
)
QUERY_CONTEXT_PROMPT = QUERY_CONTEXT_INTRO_PROMPT + " " + QUERY_CONTEXT_DEFAULT_GUIDANCE


class CodeCliError(RuntimeError):
    pass


def main(argv: list[str] | None = None, *, prog: str = "atcode") -> int:
    parser = build_parser(prog=prog)
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args) or 0)
    except (CodeCliError, AttemoryError, OSError, ValueError, TypeError) as exc:
        if getattr(args, "json", False):
            print(json.dumps({"error": {"message": str(exc), "type": type(exc).__name__}}))
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 1


def build_parser(*, prog: str = "atcode") -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=prog, description="Repo-aware Attemory code search")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init", help="initialize this repo for Attemory code search")
    init.add_argument("--session-id", default=None)
    init.set_defaults(handler=cmd_init)

    index = subparsers.add_parser("index", help="build the repo code index if missing")
    index.add_argument("--dry-run", action="store_true")
    index.add_argument("--resume", action="store_true", help="resume indexing the configured existing session")
    index.add_argument(
        "--file",
        dest="files",
        action="append",
        default=[],
        metavar="PATH",
        help="incrementally add one file to an existing index; repeatable",
    )
    index.set_defaults(handler=cmd_index)

    reindex = subparsers.add_parser("reindex", help="explicitly rebuild the repo code index")
    reindex.add_argument("-f", "--force", action="store_true")
    reindex.add_argument("--dry-run", action="store_true")
    reindex.set_defaults(handler=cmd_reindex)

    search = subparsers.add_parser("search", help="search the existing repo code index")
    search.add_argument("query", nargs="+")
    search.add_argument("--display-top-k", type=int, default=None)
    search.add_argument("--candidate-chunk-top-k", type=int, default=None)
    search.add_argument("--query-context", default=None)
    search.add_argument("--top-k", dest="display_top_k", type=int, default=None, help=argparse.SUPPRESS)
    search.add_argument("--chunk-top-k", dest="candidate_chunk_top_k", type=int, default=None, help=argparse.SUPPRESS)
    search.add_argument("--raw", action="store_true", help="print ranked raw chunks with snippets")
    search.add_argument("--include-snippets", action="store_true")
    search.add_argument("--format", choices=["text", "markdown"], default="text")
    search.add_argument("--json", action="store_true")
    search.set_defaults(handler=cmd_search)

    status = subparsers.add_parser("status", help="show repo code index status")
    status.set_defaults(handler=cmd_status)

    doctor = subparsers.add_parser("doctor", help="diagnose repo code search setup")
    doctor.set_defaults(handler=cmd_doctor)

    reset = subparsers.add_parser("reset", help="remove local metadata and the configured Attemory session")
    reset.add_argument("--all", action="store_true", help="also remove config and .gitignore entry")
    reset.add_argument("-f", "--force", action="store_true")
    reset.set_defaults(handler=cmd_reset)
    return parser


def cmd_init(args: argparse.Namespace) -> int:
    root = Path.cwd().resolve()
    path = config_path(root)
    if path.exists():
        raise CodeCliError(f"project already initialized: {path}")
    config = default_config(root, args.session_id)
    write_config(root, config)
    add_to_gitignore(root)
    print(f"created {path.relative_to(root)}")
    print(f"session: {config.session_id}")
    return 0


def cmd_index(args: argparse.Namespace) -> int:
    root, config = require_project()
    if args.files:
        if args.dry_run or args.resume:
            raise CodeCliError("`atcode index --file` cannot be combined with `--dry-run` or `--resume`")
        add_index_files(root, config, args.files)
        return 0

    if args.dry_run:
        summary = scan_summary(root, config)
        print(f"files={summary.files} chunks={summary.chunks} memories={summary.files + summary.chunks}")
        return 0

    if args.resume:
        resume_index(root, config)
        return 0

    has_index = index_path(root).exists()
    has_chunks = chunks_path(root).exists()
    if has_index and has_chunks:
        print("repo is already indexed; run `atcode reindex` to rebuild")
        return 0
    if has_index or has_chunks:
        raise CodeCliError("partial local metadata found; run `atcode reset` or `atcode reindex`")

    build_index(root, config)
    return 0


def cmd_reindex(args: argparse.Namespace) -> int:
    root, config = require_project()
    if args.dry_run:
        summary = scan_summary(root, config)
        print(f"files={summary.files} chunks={summary.chunks}")
        return 0

    if not args.force:
        print(f"will replace session: {config.session_id}")
        print(f"will rewrite: {index_path(root).relative_to(root)}")
        print(f"will rewrite: {chunks_path(root).relative_to(root)}")
        if not confirm("proceed?"):
            print("aborted")
            return 1

    client = client_for(config)
    require_server(client, config)
    try:
        client.delete_session(config.session_id)
    except AttemoryHTTPError as exc:
        if exc.status_code != 404:
            raise
    index_path(root).unlink(missing_ok=True)
    chunks_path(root).unlink(missing_ok=True)
    build_index(root, config, client=client)
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    root, config = require_project()
    records = require_chunks(root)
    search_result = retrieve_hits(
        root,
        config,
        " ".join(args.query),
        records,
        display_top_k=args.display_top_k or config.display_top_k,
        candidate_chunk_top_k=args.candidate_chunk_top_k or config.candidate_chunk_top_k,
        user_query_context=args.query_context,
    )
    if args.raw:
        hits = search_result["chunk_hits"]
        limit = args.display_top_k or config.display_top_k
        hits = hits[:limit]
        if args.json:
            print(
                json.dumps(
                    {
                        "results": [chunk_hit_to_json(root, hit) for hit in hits],
                        "raw_result_count": search_result["raw_result_count"],
                        "raw_segment_count": search_result["raw_segment_count"],
                        "oneshot_passes": search_result["oneshot_passes"],
                    },
                    ensure_ascii=False,
                )
            )
            return 0
        if not hits:
            print("No results found.")
            return 0
        for hit in hits:
            record = hit["record"]
            print(f"\n--- Result {hit['chunk_rank']} ---")
            print(f"File: {record.file}:{record.start_line}-{record.end_line} [{record.language}]")
            snippet = read_snippet(root, record) or hit.get("text", "")
            if snippet:
                print(snippet)
        return 0

    fused = search_result["hits"]
    if args.json:
        print(
            json.dumps(
                {
                    "results": [fused_to_json(root, item, args.include_snippets) for item in fused],
                    "raw_result_count": search_result["raw_result_count"],
                    "raw_segment_count": search_result["raw_segment_count"],
                    "oneshot_passes": search_result["oneshot_passes"],
                },
                ensure_ascii=False,
            )
        )
        return 0
    if args.format == "markdown":
        print_context_markdown(root, fused, args.include_snippets)
    else:
        print_context_text(root, fused, args.include_snippets)
    return 0


def cmd_status(_: argparse.Namespace) -> int:
    root, config = require_project()
    print(f"Project: {root}")
    print(f"Config: {config_path(root).relative_to(root)}")
    print(f"Session: {config.session_id}")
    if index_path(root).exists() and chunks_path(root).exists():
        data = load_index(root)
        print("Metadata: present")
        print(f"Indexed files: {data.get('file_count', '-')}")
        print(f"Indexed chunks: {data.get('chunk_count', '-')}")
        print(f"Indexed tokens: {data.get('token_count', '-')}")
        print(f"Segments: {data.get('segment_count', '-')}")
    else:
        print("Metadata: missing")

    client = client_for(config)
    try:
        healthy = client.health()
    except Exception:
        healthy = False
    print(f"Server: {config.host}:{config.port} {'healthy' if healthy else 'unreachable'}")
    if healthy:
        status = find_session_status(client, config.session_id)
        restore_failed = False
        if status is None:
            try:
                client.restore_session(config.session_id)
                status = find_session_status(client, config.session_id)
            except Exception as exc:
                print(f"Session status: not restorable ({exc})")
                restore_failed = True
        if status is not None:
            print(f"Session status: indexed={status.indexed} kv_persist={status.kv_persist}")
            print(f"Saved segments: {status.saved_segments}")
        elif not restore_failed:
            print("Session status: not loaded")
    return 0


def cmd_doctor(_: argparse.Namespace) -> int:
    checks: list[tuple[str, bool, str]] = []
    root = find_project_root(Path.cwd())
    checks.append(("project config", root is not None, "run `atcode init`"))
    if root is None:
        return print_checks(checks)
    config = load_config(root)
    checks.append(
        ("local index metadata", index_path(root).exists() and chunks_path(root).exists(), "run `atcode index`")
    )
    client = client_for(config)
    try:
        server_ok = client.health()
    except Exception as exc:
        server_ok = False
        server_detail = str(exc)
    else:
        server_detail = "start attemory-server" if not server_ok else ""
    checks.append(("server health", server_ok, server_detail))
    if server_ok:
        try:
            client.restore_session(config.session_id)
            restored = True
            restore_detail = ""
        except Exception as exc:
            restored = False
            restore_detail = str(exc)
        checks.append(("restore session", restored, restore_detail))
        status = find_session_status(client, config.session_id) if restored else None
        checks.append(("session indexed", bool(status and status.indexed), "run `atcode index`"))
        checks.append(("session kv_persist", bool(status and status.kv_persist), "repo sessions should use kv_persist=true"))
    return print_checks(checks)


def cmd_reset(args: argparse.Namespace) -> int:
    root, config = require_project()
    targets = [index_path(root), chunks_path(root)]
    if args.all:
        targets.append(config_path(root))
    existing = [path for path in targets if path.exists()]
    existing.append(Path(f"session:{config.session_id}"))
    if not existing:
        print("nothing to reset")
        return 0
    if not args.force:
        print("will remove:")
        for path in existing:
            print(f"  {path}")
        if not confirm("proceed?"):
            print("aborted")
            return 1
    client = client_for(config)
    require_server(client, config)
    try:
        client.delete_session(config.session_id)
    except AttemoryHTTPError as exc:
        if exc.status_code != 404:
            raise
    for path in targets:
        path.unlink(missing_ok=True)
    if args.all:
        remove_from_gitignore(root)
        try:
            (root / ".attemory").rmdir()
        except OSError:
            pass
    print("reset complete")
    return 0


def build_index(root: Path, config: Any, *, client: AttemoryClient | None = None) -> None:
    client = client or client_for(config)
    require_server(client, config)
    temp_dir = Path(tempfile.mkdtemp(prefix="attemory-code-", dir=str(root / ".attemory")))
    records: list[ChunkRecord] = []
    file_count = 0
    chunk_count = 0
    last_usage: Any | None = None

    try:
        client.create_session(config.session_id, kv_persist=config.kv_persist)
        last_usage = client.add_system(SYSTEM_PROMPT, session_id=config.session_id)
        for indexed_file in iter_indexed_files(root, config):
            if (
                last_usage is not None
                and last_usage.remaining_tokens < config.min_remaining_tokens_before_file
            ):
                client.next_segment(config.session_id)
            last_usage = client.add_memory(
                file_header(indexed_file.rel_path),
                session_id=config.session_id,
            )
            file_count += 1
            for chunk in indexed_file.chunks:
                record = chunk_record(chunk)
                last_usage = client.add_memory(
                    {"id": record.id, "text": chunk.memory_text},
                    session_id=config.session_id,
                )
                records.append(record)
                chunk_count += 1
            if file_count % 100 == 0:
                print(f"indexed files={file_count} chunks={chunk_count}", file=sys.stderr)

        if chunk_count == 0:
            raise CodeCliError("no source chunks matched the project config")

        client.index_session(config.session_id)
        try:
            client.save_session(config.session_id)
        except AttemoryHTTPError:
            raise

        status = find_session_status(client, config.session_id)
        if status is None or not status.indexed:
            raise CodeCliError("session was created but is not indexed")

        write_local_index_metadata(
            root,
            config,
            records=records,
            file_count=file_count,
            chunk_count=chunk_count,
            status=status,
            temp_dir=temp_dir,
        )
        print(f"indexed files={file_count} chunks={chunk_count} segments={status.segment_count}")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def resume_index(root: Path, config: Any) -> None:
    client = client_for(config)
    status = restore_configured_session(client, config)
    records, file_count, chunk_count = scan_index_records(root, config, preserve_existing=True)
    validate_session_matches_repo(status, file_count=file_count, chunk_count=chunk_count)

    if not status.indexed:
        client.index_session(config.session_id)
        status = refreshed_session_status(client, config.session_id)
        if status is None:
            raise CodeCliError(f"session is not loaded after resume: {config.session_id}")

    if not status.disk_cached or not status.kv_persist:
        client.save_session(config.session_id)
        status = refreshed_session_status(client, config.session_id)

    if status is None or not status.indexed:
        raise CodeCliError(f"session is not fully indexed after resume: {config.session_id}")
    write_local_index_metadata(
        root,
        config,
        records=records,
        file_count=file_count,
        chunk_count=chunk_count,
        status=status,
    )
    print(f"resumed index files={file_count} chunks={chunk_count} segments={status.segment_count}")


def restore_configured_session(client: AttemoryClient, config: Any) -> Any:
    require_server(client, config)
    client.restore_session(config.session_id)
    status = refreshed_session_status(client, config.session_id)
    if status is None:
        raise CodeCliError(f"session is not loaded: {config.session_id}")
    return status


def refreshed_session_status(client: AttemoryClient, session_id: str) -> Any | None:
    return find_session_status(client, session_id)


def add_index_files(root: Path, config: Any, file_values: list[str]) -> None:
    if not index_path(root).exists() or not chunks_path(root).exists():
        raise CodeCliError("no Attemory code index found for this repository; run `atcode index` first")

    existing_records = list(load_chunks(chunks_path(root)).values())
    existing_files = {record.file for record in existing_records}
    existing_ids = {record.id for record in existing_records}
    new_files: list[IndexedFile] = []
    new_records: list[ChunkRecord] = []
    new_file_paths: set[str] = set()
    new_ids: set[str] = set()

    for file_value in file_values:
        indexed_file = explicit_indexed_file(root, config, file_value)
        if indexed_file.rel_path in existing_files or indexed_file.rel_path in new_file_paths:
            raise CodeCliError(f"file is already indexed: {indexed_file.rel_path}; run `atcode reindex` to replace it")
        records = [chunk_record(chunk) for chunk in indexed_file.chunks]
        duplicate_id = next((record.id for record in records if record.id in existing_ids or record.id in new_ids), None)
        if duplicate_id is not None:
            raise CodeCliError(f"file would duplicate an indexed chunk id: {duplicate_id}")
        new_files.append(indexed_file)
        new_records.extend(records)
        new_file_paths.add(indexed_file.rel_path)
        new_ids.update(record.id for record in records)

    metadata = load_index(root)
    old_file_count = int(metadata.get("file_count", len(existing_files)))
    old_chunk_count = int(metadata.get("chunk_count", len(existing_records)))

    client = client_for(config)
    status = restore_configured_session(client, config)
    validate_session_matches_repo(status, file_count=old_file_count, chunk_count=old_chunk_count)
    if not status.indexed:
        raise CodeCliError(f"session is not indexed: {config.session_id}; run `atcode index --resume` first")

    last_usage: Any | None = None
    if existing_records:
        client.next_segment(config.session_id)
    for indexed_file in new_files:
        if last_usage is not None and last_usage.remaining_tokens < config.min_remaining_tokens_before_file:
            client.next_segment(config.session_id)
        last_usage = client.add_memory(file_header(indexed_file.rel_path), session_id=config.session_id)
        for chunk in indexed_file.chunks:
            record = chunk_record(chunk)
            last_usage = client.add_memory(
                {"id": record.id, "text": chunk.memory_text},
                session_id=config.session_id,
            )

    client.index_session(config.session_id)
    status = refreshed_session_status(client, config.session_id)
    if status is None or not status.indexed:
        raise CodeCliError(f"session is not indexed after adding files: {config.session_id}")

    client.save_session(config.session_id)
    status = refreshed_session_status(client, config.session_id)
    if status is None or not status.indexed:
        raise CodeCliError(f"session is not loaded after saving: {config.session_id}")

    write_local_index_metadata(
        root,
        config,
        records=existing_records + new_records,
        file_count=old_file_count + len(new_files),
        chunk_count=old_chunk_count + len(new_records),
        status=status,
    )
    updated_config = config_with_include_files(config, [indexed_file.rel_path for indexed_file in new_files])
    if updated_config.include_files != config.include_files:
        write_config(root, updated_config)
    print(f"added files={len(new_files)} chunks={len(new_records)} segments={status.segment_count}")


def explicit_indexed_file(root: Path, config: Any, file_value: str) -> IndexedFile:
    path = Path(file_value).expanduser()
    if not path.is_absolute():
        path = root / path
    path = path.resolve()
    try:
        rel = path.relative_to(root)
    except ValueError as exc:
        raise CodeCliError(f"file is outside the repository: {file_value}") from exc
    if rel.parts and rel.parts[0] == ".attemory":
        raise CodeCliError("refusing to index .attemory metadata")
    if not path.is_file():
        raise CodeCliError(f"not a file: {rel.as_posix()}")
    indexed_file = indexed_file_from_path(root, config, path)
    if indexed_file is None:
        raise CodeCliError(f"file has no indexable content: {rel.as_posix()}")
    return indexed_file


def config_with_include_files(config: Any, rel_paths: Iterable[str]) -> Any:
    seen: set[str] = set()
    include_files: list[str] = []
    for rel_path in [*config.include_files, *rel_paths]:
        normalized = rel_path.strip().strip("/")
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        include_files.append(normalized)
    return replace(config, include_files=include_files)


def scan_index_records(root: Path, config: Any, *, preserve_existing: bool = False) -> tuple[list[ChunkRecord], int, int]:
    records: list[ChunkRecord] = []
    scanned_files: set[str] = set()
    for indexed_file in iter_indexed_files(root, config):
        scanned_files.add(indexed_file.rel_path)
        for chunk in indexed_file.chunks:
            records.append(chunk_record(chunk))
    preserved_files: set[str] = set()
    if preserve_existing and chunks_path(root).is_file():
        for record in load_chunks(chunks_path(root)).values():
            if record.file in scanned_files:
                continue
            records.append(record)
            preserved_files.add(record.file)
    file_count = len(scanned_files | preserved_files)
    chunk_count = len(records)
    if chunk_count == 0:
        raise CodeCliError("no source chunks matched the project config")
    return records, file_count, chunk_count


def validate_session_matches_repo(status: Any, *, file_count: int, chunk_count: int) -> None:
    expected_memory_count = file_count + chunk_count
    if status.memory_count != expected_memory_count:
        raise CodeCliError(
            "configured session does not match the current repo scan: "
            f"session_memories={status.memory_count} expected_memories={expected_memory_count} "
            f"(files={file_count} chunks={chunk_count}); the previous add-memory pass may not have completed"
        )


def write_local_index_metadata(
    root: Path,
    config: Any,
    *,
    records: list[ChunkRecord],
    file_count: int,
    chunk_count: int,
    status: Any,
    temp_dir: Path | None = None,
) -> None:
    owned_temp_dir = temp_dir is None
    if temp_dir is None:
        temp_dir = Path(tempfile.mkdtemp(prefix="attemory-code-", dir=str(root / ".attemory")))
    temp_chunks = temp_dir / "chunks.jsonl"
    temp_index = temp_dir / "index.json"
    try:
        write_chunks(temp_chunks, records)
        temp_index.write_text(
            json.dumps(
                {
                    "version": 1,
                    "session_id": config.session_id,
                    "repo_root": str(root),
                    "git_commit": git_commit(root),
                    "context_structure": CONTEXT_STRUCTURE,
                    "file_header_prefix": FILE_HEADER_PREFIX,
                    "kv_persist": config.kv_persist,
                    "system_prompt": SYSTEM_PROMPT,
                    "context_window": config.context_window,
                    "file_boundary_reserve_percent": config.file_boundary_reserve_percent,
                    "min_remaining_tokens_before_file": config.min_remaining_tokens_before_file,
                    "file_count": file_count,
                    "chunk_count": chunk_count,
                    "memory_count": file_count + chunk_count,
                    "token_count": status.total_tokens,
                    "segment_count": status.segment_count,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        temp_chunks.replace(chunks_path(root))
        temp_index.replace(index_path(root))
    finally:
        if owned_temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)


def require_project() -> tuple[Path, Any]:
    root = find_project_root(Path.cwd())
    if root is None:
        raise CodeCliError("not in an Attemory code project; run `atcode init`")
    return root, load_config(root)


def require_chunks(root: Path) -> dict[str, ChunkRecord]:
    if not index_path(root).exists() or not chunks_path(root).exists():
        raise CodeCliError("no Attemory code index found for this repository; run `atcode index`")
    return load_chunks(chunks_path(root))


def client_for(config: Any) -> AttemoryClient:
    return AttemoryClient(host=config.host, port=config.port, timeout=3600)


def require_server(client: AttemoryClient, config: Any) -> None:
    try:
        if client.health():
            return
    except Exception as exc:
        raise CodeCliError(
            f"cannot reach Attemory server at {config.host}:{config.port}; "
            f"start one with `attemory-server --small --backend gpu --port {config.port}`"
        ) from exc
    raise CodeCliError(f"Attemory server at {config.host}:{config.port} is not healthy")


def retrieve_hits(
    root: Path,
    config: Any,
    question: str,
    memory_map: dict[str, ChunkRecord],
    *,
    display_top_k: int,
    candidate_chunk_top_k: int,
    user_query_context: str | None = None,
) -> dict[str, Any]:
    client = client_for(config)
    require_server(client, config)
    client.restore_session(config.session_id)
    status = find_session_status(client, config.session_id)
    if status is None:
        raise CodeCliError(f"session is not loaded: {config.session_id}")
    if not status.indexed:
        raise CodeCliError(f"session is not indexed: {config.session_id}; run `atcode index`")

    query = f"Question: {question}"
    query_context = build_query_context(question, user_query_context)
    raw_results = client.search(
        query,
        session_id=config.session_id,
        query_context=query_context,
        top_k=candidate_chunk_top_k,
    )
    ordered_ids = ordered_chunk_ids(raw_results, memory_map)
    id_segments = result_segments_by_id(raw_results, memory_map)
    raw_segment_count = len(result_segments(raw_results))
    oneshot_passes = 0

    if raw_segment_count > 1:
        ordered_ids, id_segments, oneshot_passes = rerank_ordered_ids(
            client=client,
            query=query,
            query_context=query_context,
            results=raw_results,
            ordered_ids=ordered_ids,
            id_segments=id_segments,
            memory_map=memory_map,
            candidate_chunk_top_k=candidate_chunk_top_k,
            passes=8,
        )

    chunk_hits = chunk_hits_from_order(
        ordered_ids=ordered_ids,
        id_segments=id_segments,
        memory_map=memory_map,
        candidate_chunk_top_k=candidate_chunk_top_k,
    )
    return {
        "hits": fused_hits(chunk_hits=chunk_hits, display_top_k=display_top_k),
        "chunk_hits": chunk_hits,
        "raw_result_count": len(raw_results),
        "raw_segment_count": raw_segment_count,
        "oneshot_passes": oneshot_passes,
        "search_query": query,
        "query_context": query_context,
    }


def build_query_context(question: str, user_query_context: str | None = None) -> str:
    if user_query_context is None or not user_query_context.strip():
        return QUERY_CONTEXT_PROMPT.format(question=question)
    return f"{QUERY_CONTEXT_INTRO_PROMPT.format(question=question)}\n\n{user_query_context.strip()}"


def find_session_status(client: AttemoryClient, session_id: str) -> Any | None:
    for status in client.list_sessions():
        if status.session_id == session_id:
            return status
    return None


def rerank_ordered_ids(
    *,
    client: AttemoryClient,
    query: str,
    query_context: str,
    results: list[SearchResult],
    ordered_ids: list[str],
    id_segments: dict[str, int | None],
    memory_map: dict[str, ChunkRecord],
    candidate_chunk_top_k: int,
    passes: int,
) -> tuple[list[str], dict[str, int | None], int]:
    current = results
    pass_count = 0
    while len(result_segments(current)) > 1 and pass_count < passes:
        memories = memories_from_index_order(current, memory_map)
        if not memories:
            return ordered_ids, id_segments, pass_count
        non_id_count = sum(1 for memory in memories if "id" not in memory)
        reranked = client.oneshot_search(
            query,
            memories,
            system=RERANK_SYSTEM_PROMPT,
            query_context=query_context,
            top_k=min(len(memories), non_id_count + candidate_chunk_top_k),
        )
        pass_count += 1
        reranked_ids = ordered_chunk_ids(reranked, memory_map)
        if reranked_ids:
            seen_reranked = set(reranked_ids)
            ordered_ids = reranked_ids + [memory_id for memory_id in ordered_ids if memory_id not in seen_reranked]
        id_segments.update(result_segments_by_id(reranked, memory_map))
        current = reranked
    return ordered_ids, id_segments, pass_count


def memories_from_index_order(
    results: list[SearchResult],
    memory_map: dict[str, ChunkRecord],
) -> list[dict[str, str]]:
    text_by_id: dict[str, str] = {}
    for result in results:
        memory_id = getattr(result, "id", None)
        text = getattr(result, "text", "") or ""
        if isinstance(memory_id, str) and memory_id in memory_map and text and memory_id not in text_by_id:
            text_by_id[memory_id] = text

    memories: list[dict[str, str]] = []
    current_file: str | None = None
    for memory_id, location in memory_map.items():
        text = text_by_id.get(memory_id)
        if text is None:
            continue
        if location.file != current_file:
            memories.append({"text": file_header(location.file)})
            current_file = location.file
        memories.append({"id": memory_id, "text": text})
    return memories


def ordered_chunk_ids(results: list[SearchResult], memory_map: dict[str, ChunkRecord]) -> list[str]:
    ordered_ids: list[str] = []
    seen_ids: set[str] = set()
    for result in results:
        memory_id = getattr(result, "id", None)
        if not isinstance(memory_id, str) or memory_id not in memory_map or memory_id in seen_ids:
            continue
        seen_ids.add(memory_id)
        ordered_ids.append(memory_id)
    return ordered_ids


def result_segments(results: list[SearchResult]) -> set[int]:
    return {
        segment_id
        for result in results
        if isinstance((segment_id := getattr(result, "segment_id", None)), int)
    }


def result_segments_by_id(
    results: list[SearchResult],
    memory_map: dict[str, ChunkRecord],
) -> dict[str, int | None]:
    id_segments: dict[str, int | None] = {}
    for result in results:
        memory_id = getattr(result, "id", None)
        if not isinstance(memory_id, str) or memory_id not in memory_map or memory_id in id_segments:
            continue
        id_segments[memory_id] = getattr(result, "segment_id", None)
    return id_segments


def chunk_hits_from_order(
    *,
    ordered_ids: list[str],
    id_segments: dict[str, int | None],
    memory_map: dict[str, ChunkRecord],
    candidate_chunk_top_k: int,
) -> list[dict[str, Any]]:
    chunk_hits: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for memory_id in ordered_ids:
        if memory_id not in memory_map or memory_id in seen_ids:
            continue
        seen_ids.add(memory_id)
        record = memory_map[memory_id]
        chunk_hits.append(
            {
                "path": record.file,
                "start_line": record.start_line,
                "end_line": record.end_line,
                "chunk_rank": len(chunk_hits) + 1,
                "memory_id": memory_id,
                "segment_id": id_segments.get(memory_id),
                "record": record,
            }
        )
        if len(chunk_hits) >= candidate_chunk_top_k:
            break
    return chunk_hits


def chunk_hit_to_json(root: Path, hit: dict[str, Any]) -> dict[str, Any]:
    record = hit["record"]
    return {
        "rank": hit["chunk_rank"],
        "path": hit["path"],
        "start_line": hit["start_line"],
        "end_line": hit["end_line"],
        "memory_id": hit["memory_id"],
        "segment_id": hit["segment_id"],
        "text": read_snippet(root, record) or hit.get("text", ""),
    }


def fused_hits(*, chunk_hits: list[dict[str, Any]], display_top_k: int) -> list[dict[str, Any]]:
    file_scores: dict[str, float] = {}
    first_rank: dict[str, int] = {}
    chunks_by_file: dict[str, list[dict[str, Any]]] = {}
    for chunk in chunk_hits:
        path = chunk["path"]
        rank = chunk["chunk_rank"]
        file_scores[path] = file_scores.get(path, 0.0) + 1.0 / math.log2(rank + 1)
        first_rank.setdefault(path, rank)
        chunks_by_file.setdefault(path, []).append(chunk)

    ranked_paths = sorted(file_scores, key=lambda path: (-file_scores[path], first_rank[path], path))
    hits: list[dict[str, Any]] = []
    for file_rank, path in enumerate(ranked_paths[:display_top_k], 1):
        ranges = merge_ranges((chunk["start_line"], chunk["end_line"]) for chunk in chunks_by_file.get(path, []))
        language = chunks_by_file[path][0]["record"].language
        hits.append({"rank": file_rank, "path": path, "language": language, "ranges": ranges})
    return hits


def merge_ranges(ranges: Iterable[tuple[int, int]]) -> list[dict[str, int]]:
    ordered = sorted((int(start), int(end)) for start, end in ranges if start and end)
    merged: list[list[int]] = []
    for start, end in ordered:
        if not merged or start > merged[-1][1] + 1:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return [{"start_line": start, "end_line": end} for start, end in merged]


def fused_to_json(root: Path, item: dict[str, Any], include_snippets: bool) -> dict[str, Any]:
    data = {
        "path": item["path"],
        "language": item["language"],
        "rank": item["rank"],
        "ranges": item["ranges"],
    }
    if include_snippets:
        data["snippets"] = [
            read_snippet(
                root,
                ChunkRecord("", item["path"], range_item["start_line"], range_item["end_line"], item["language"], ""),
            )
            for range_item in item["ranges"]
        ]
    return data


def print_context_text(root: Path, fused: list[dict[str, Any]], include_snippets: bool) -> None:
    print("<semantic_search_results>")
    print("The following files and line ranges are semantic-search candidate evidence from the repository.")
    print()
    for item in fused:
        ranges = format_ranges(item["ranges"])
        print(f"{item['rank']}. {item['path']}:{ranges}")
        if include_snippets:
            for range_item in item["ranges"]:
                snippet = read_snippet(
                    root,
                    ChunkRecord("", item["path"], range_item["start_line"], range_item["end_line"], item["language"], ""),
                )
                if snippet:
                    print("```")
                    print(snippet)
                    print("```")
    print("</semantic_search_results>")


def print_context_markdown(root: Path, fused: list[dict[str, Any]], include_snippets: bool) -> None:
    print("### Semantic Search Results")
    for item in fused:
        ranges = format_ranges(item["ranges"])
        print(f"{item['rank']}. `{item['path']}:{ranges}`")
        if include_snippets:
            for range_item in item["ranges"]:
                snippet = read_snippet(
                    root,
                    ChunkRecord("", item["path"], range_item["start_line"], range_item["end_line"], item["language"], ""),
                )
                if snippet:
                    print("```")
                    print(snippet)
                    print("```")


def format_ranges(ranges: list[dict[str, int]]) -> str:
    parts: list[str] = []
    for item in ranges:
        start = item.get("start_line")
        end = item.get("end_line")
        if isinstance(start, int) and isinstance(end, int):
            parts.append(str(start) if start == end else f"{start}-{end}")
    return ",".join(parts)


def print_checks(checks: list[tuple[str, bool, str]]) -> int:
    ok = True
    for name, passed, detail in checks:
        print(f"{'OK' if passed else 'FAIL'} {name}")
        if not passed and detail:
            print(f"  {detail}")
        ok = ok and passed
    return 0 if ok else 1


def confirm(prompt: str) -> bool:
    try:
        answer = input(f"{prompt} [y/N] ")
    except EOFError:
        return False
    return answer.strip().lower() in {"y", "yes"}


if __name__ == "__main__":
    raise SystemExit(main())
