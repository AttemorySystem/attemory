from __future__ import annotations

import argparse
import os
import sys
import threading
from pathlib import Path
from typing import Any

from .code import cli as code_cli
from .exceptions import AttemoryHTTPError


CODE_MCP_INSTRUCTIONS = (
    "Use Attemory for read-only natural-language semantic code search in repositories already indexed with "
    "`attemory code` or its short alias `atcode`. This is not keyword search. Call `search` for any "
    "repository understanding or code-location question, including features, behavior, architecture, "
    "implementation, entry points, definitions, call sites, configuration, tests, error paths, and "
    "cross-file flows. Detailed queries work best: include relevant symbols, behaviors, files, errors, "
    "or user-facing effects when available. This MCP server only exposes repository search; repository "
    "setup and indexing are user-managed. If search reports a missing or incomplete index, tell the user "
    "to run `atcode init`, `atcode index`, or `atcode index --resume`. After search, read the returned "
    "file ranges before answering or editing."
)


class AttemoryCodeMCPService:
    def __init__(self, *, default_repo_root: str | None = None) -> None:
        self.default_repo_root = str(Path(default_repo_root).expanduser()) if default_repo_root else None
        self._lock = threading.RLock()

    def search(
        self,
        query: str,
        *,
        repo_root: str | None,
        query_context: str | None,
        display_top_k: int | None,
        candidate_chunk_top_k: int | None,
        include_snippets: bool,
    ) -> dict[str, Any]:
        with self._lock:
            root = self._resolve_project_root(repo_root)
            config = code_cli.load_config(root)
            records = code_cli.require_chunks(root)
            search_result = code_cli.retrieve_hits(
                root,
                config,
                query,
                records,
                display_top_k=display_top_k or config.display_top_k,
                candidate_chunk_top_k=candidate_chunk_top_k or config.candidate_chunk_top_k,
                user_query_context=query_context,
            )
            fused = search_result["hits"]
            return {
                "repo_root": str(root),
                "session_id": config.session_id,
                "context": render_code_context(root, fused, include_snippets),
                "results": [code_cli.fused_to_json(root, item, include_snippets) for item in fused],
                "raw_result_count": search_result["raw_result_count"],
                "raw_segment_count": search_result["raw_segment_count"],
                "oneshot_passes": search_result["oneshot_passes"],
            }

    def _resolve_project_root(self, repo_root: str | None) -> Path:
        requested = repo_root or self.default_repo_root
        start = Path(requested).expanduser().resolve() if requested else Path.cwd().resolve()
        root = code_cli.find_project_root(start)
        if root is None:
            raise code_cli.CodeCliError(
                f"no Attemory code project found from {start}; run `atcode init` and `atcode index`"
            )
        return root


def build_mcp_app(
    code_service: AttemoryCodeMCPService,
) -> Any:
    try:
        from mcp.server.fastmcp import FastMCP
        from mcp.types import ToolAnnotations
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "MCP support requires the optional dependency: "
            "pip install 'attemory[mcp]' or pip install mcp"
        ) from exc

    mcp = FastMCP(
        "attemory",
        instructions=CODE_MCP_INSTRUCTIONS,
    )
    read_only_annotations = ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )

    @mcp.tool(name="search", annotations=read_only_annotations)
    def search(
        query: str,
        repo_root: str | None = None,
        query_context: str | None = None,
        display_top_k: int | None = None,
        candidate_chunk_top_k: int | None = None,
        include_snippets: bool = False,
    ) -> dict[str, Any]:
        """Search an indexed repository and return semantic file/range context for coding agents.

        repo_root may be the repository root or any path inside the repository. If it
        is omitted, the MCP server falls back to --repo-root, ATTEMORY_CODE_REPO_ROOT,
        and finally the MCP process working directory.
        """
        return _run_tool(
            code_service.search,
            query,
            repo_root=repo_root,
            query_context=query_context,
            display_top_k=display_top_k,
            candidate_chunk_top_k=candidate_chunk_top_k,
            include_snippets=include_snippets,
        )

    return mcp


def _run_tool(func: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
    try:
        return {"data": func(*args, **kwargs)}
    except Exception as exc:  # noqa: BLE001 - MCP tools should report structured failures.
        return {"error": _error_payload(exc)}


def _error_payload(exc: BaseException) -> dict[str, Any]:
    code = "ATTEMORY_ERROR"
    message = str(exc)
    details: dict[str, Any] = {"error_type": type(exc).__name__}

    if isinstance(exc, AttemoryHTTPError):
        if exc.error_code:
            code = exc.error_code
        if exc.error_message:
            message = exc.error_message
        details["status_code"] = exc.status_code
        if exc.details is not None:
            details["server"] = exc.details
    elif isinstance(exc, code_cli.CodeCliError):
        code, suggestions = _code_cli_error_details(message)
        details.update(suggestions)
    elif isinstance(exc, (ValueError, TypeError)):
        code = "INVALID_REQUEST"
    elif isinstance(exc, RuntimeError):
        code = "RUNTIME_ERROR"

    return {
        "code": code,
        "message": message,
        "details": details,
    }


def _code_cli_error_details(message: str) -> tuple[str, dict[str, Any]]:
    lower = message.lower()
    details: dict[str, Any] = {"recoverable": True}

    if "not in an attemory code project" in lower or "no attemory code project found" in lower:
        return (
            "REPO_NOT_INITIALIZED",
            details
            | {
                "suggested_action": "Run atcode init and atcode index in the target repository.",
                "suggested_commands": ["atcode init", "atcode index"],
            },
        )

    if "no attemory code index found" in lower or "session is not indexed" in lower:
        return (
            "REPO_NOT_INDEXED",
            details
            | {
                "suggested_action": (
                    "Run atcode index in the target repository. If indexing was interrupted "
                    "after memories were added, run atcode index --resume."
                ),
                "suggested_commands": ["atcode index", "atcode index --resume"],
            },
        )

    if "cannot reach attemory server" in lower or "is not healthy" in lower:
        return (
            "ATTEMORY_SERVER_UNAVAILABLE",
            details
            | {
                "suggested_action": "Start attemory-server with the port configured for this repository.",
                "suggested_commands": ["attemory-server --small --backend gpu --port 9006"],
            },
        )

    if "session is not loaded" in lower:
        return (
            "SESSION_NOT_LOADED",
            details
            | {
                "suggested_action": "Restore or rebuild the repository index with atcode index.",
                "suggested_commands": ["atcode index", "atcode index --resume"],
            },
        )

    return (
        "CODE_PROJECT_ERROR",
        details
        | {
            "suggested_action": "Check the repository setup with atcode status or atcode doctor.",
            "suggested_commands": ["atcode status", "atcode doctor"],
        },
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    code_service = AttemoryCodeMCPService(default_repo_root=args.repo_root)

    try:
        app = build_mcp_app(code_service)
        app.run(transport=args.transport)
    except Exception as exc:  # noqa: BLE001
        print(f"attemory-mcp: error: {exc}", file=sys.stderr)
        return 1
    return 0


def render_code_context(root: Path, fused: list[dict[str, Any]], include_snippets: bool) -> str:
    lines = [
        "<semantic_search_results>",
        "The following files and line ranges are semantic-search candidate evidence from the repository.",
        "",
    ]
    for item in fused:
        ranges = code_cli.format_ranges(item["ranges"])
        lines.append(f"{item['rank']}. {item['path']}:{ranges}")
        if include_snippets:
            for range_item in item["ranges"]:
                snippet = code_cli.read_snippet(
                    root,
                    code_cli.ChunkRecord(
                        "",
                        item["path"],
                        range_item["start_line"],
                        range_item["end_line"],
                        item["language"],
                        "",
                    ),
                )
                if snippet:
                    lines.extend(["```", snippet, "```"])
    lines.append("</semantic_search_results>")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="attemory-mcp", description="MCP adapter for attemory")
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http"],
        default=os.environ.get("ATTEMORY_MCP_TRANSPORT", "stdio"),
    )
    parser.add_argument("--repo-root", default=os.environ.get("ATTEMORY_CODE_REPO_ROOT"))
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
