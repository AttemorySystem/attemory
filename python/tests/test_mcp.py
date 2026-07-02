from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any

from attemory import mcp as mcp_module
from attemory.code.project import ChunkRecord, chunks_path, default_config, index_path, write_chunks, write_config
from attemory.mcp import AttemoryCodeMCPService


def write_indexed_project(root: Path, *, session_id: str = "demo") -> None:
    write_config(root, default_config(root, session_id))
    (root / "a.py").write_text("print('a')\n", encoding="utf-8")
    write_chunks(chunks_path(root), [ChunkRecord("0", "a.py", 1, 1, "python", "")])
    index_path(root).write_text("{}", encoding="utf-8")


def test_code_mcp_search_uses_cwd_fallback(tmp_path: Path, monkeypatch: Any) -> None:
    write_indexed_project(tmp_path)

    def fake_retrieve_hits(*args: Any, **kwargs: Any) -> dict[str, Any]:
        assert args[0] == tmp_path
        return {
            "hits": [],
            "raw_result_count": 0,
            "raw_segment_count": 0,
            "oneshot_passes": 0,
        }

    monkeypatch.setattr(mcp_module.code_cli, "retrieve_hits", fake_retrieve_hits)
    monkeypatch.chdir(tmp_path)
    service = AttemoryCodeMCPService()

    result = service.search(
        "where is parser",
        repo_root=None,
        query_context=None,
        display_top_k=None,
        candidate_chunk_top_k=None,
        include_snippets=False,
    )

    assert result["repo_root"] == str(tmp_path)


def test_code_mcp_search_returns_context(tmp_path: Path, monkeypatch: Any) -> None:
    write_indexed_project(tmp_path)

    def fake_retrieve_hits(*args: Any, **kwargs: Any) -> dict[str, Any]:
        assert args[2] == "where is parser"
        assert kwargs["display_top_k"] == 3
        return {
            "hits": [
                {
                    "rank": 1,
                    "path": "a.py",
                    "language": "python",
                    "ranges": [{"start_line": 1, "end_line": 1}],
                }
            ],
            "raw_result_count": 1,
            "raw_segment_count": 1,
            "oneshot_passes": 0,
        }

    monkeypatch.setattr(mcp_module.code_cli, "retrieve_hits", fake_retrieve_hits)
    service = AttemoryCodeMCPService()

    result = service.search(
        "where is parser",
        repo_root=str(tmp_path),
        query_context="Prefer public APIs.",
        display_top_k=3,
        candidate_chunk_top_k=None,
        include_snippets=True,
    )

    assert result["repo_root"] == str(tmp_path)
    assert result["session_id"] == "demo"
    assert result["results"][0]["path"] == "a.py"
    assert "1. a.py:1" in result["context"]
    assert "print('a')" in result["context"]


def test_mcp_app_exposes_only_search_by_default(monkeypatch: Any) -> None:
    install_fake_mcp_modules(monkeypatch)
    code_service = AttemoryCodeMCPService()

    app = mcp_module.build_mcp_app(code_service)

    assert set(app.tools) == {"search"}
    assert "natural-language semantic code search" in app.instructions
    assert "not keyword search" in app.instructions
    assert "only exposes repository search" in app.instructions
    annotations = app.tools["search"]["annotations"]
    assert annotations.kwargs["readOnlyHint"] is True
    assert annotations.kwargs["destructiveHint"] is False


def test_code_cli_error_payload_suggests_index() -> None:
    payload = mcp_module._error_payload(
        mcp_module.code_cli.CodeCliError("no Attemory code index found for this repository; run `atcode index`")
    )

    assert payload["code"] == "REPO_NOT_INDEXED"
    assert payload["details"]["recoverable"] is True
    assert "atcode index --resume" in payload["details"]["suggested_commands"]


def test_mcp_parser_has_no_server_lifecycle_options() -> None:
    option_dests = {action.dest for action in mcp_module.build_parser()._actions}

    assert "repo_root" in option_dests
    assert "transport" in option_dests
    assert "host" not in option_dests
    assert "port" not in option_dests
    assert "manage_server" not in option_dests


def install_fake_mcp_modules(monkeypatch: Any) -> None:
    class FakeFastMCP:
        def __init__(self, name: str, *, instructions: str) -> None:
            self.name = name
            self.instructions = instructions
            self.tools: dict[str, dict[str, Any]] = {}

        def tool(self, *, name: str, annotations: Any = None) -> Any:
            def decorator(func: Any) -> Any:
                self.tools[name] = {"func": func, "annotations": annotations}
                return func

            return decorator

    class FakeToolAnnotations:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

    mcp_pkg = types.ModuleType("mcp")
    server_pkg = types.ModuleType("mcp.server")
    fastmcp_mod = types.ModuleType("mcp.server.fastmcp")
    types_mod = types.ModuleType("mcp.types")
    fastmcp_mod.FastMCP = FakeFastMCP
    types_mod.ToolAnnotations = FakeToolAnnotations
    server_pkg.fastmcp = fastmcp_mod
    mcp_pkg.server = server_pkg
    monkeypatch.setitem(sys.modules, "mcp", mcp_pkg)
    monkeypatch.setitem(sys.modules, "mcp.server", server_pkg)
    monkeypatch.setitem(sys.modules, "mcp.server.fastmcp", fastmcp_mod)
    monkeypatch.setitem(sys.modules, "mcp.types", types_mod)
