from __future__ import annotations

from pathlib import Path
from typing import Any

from attemory import cli as attemory_cli
from attemory.code import cli as code_cli
from attemory.code.project import (
    add_to_gitignore,
    chunks_path,
    config_path,
    default_config,
    iter_indexed_files,
    load_config,
    split_code,
    write_config,
)
from attemory.models import SearchResult, SessionStatus, TokenUsage


def test_code_init_creates_config_and_gitignore(tmp_path: Path, monkeypatch: Any) -> None:
    (tmp_path / ".git").mkdir()
    monkeypatch.chdir(tmp_path)

    code = code_cli.main(["init", "--session-id", "attemory-code-demo"])

    assert code == 0
    config = load_config(tmp_path)
    assert config.session_id == "attemory-code-demo"
    assert config.kv_persist is True
    assert "/.attemory/" in (tmp_path / ".gitignore").read_text(encoding="utf-8")


def test_default_file_boundary_threshold_is_derived_from_context_window(tmp_path: Path) -> None:
    config = default_config(tmp_path, "demo")

    assert config.context_window == 262144
    assert config.file_boundary_reserve_percent == 25
    assert config.min_remaining_tokens_before_file == 65536


def test_attemory_code_delegates_to_repo_cli(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.chdir(tmp_path)

    code = attemory_cli.main(["code", "init", "--session-id", "delegated"])

    assert code == 0
    assert load_config(tmp_path).session_id == "delegated"


def test_code_cli_default_prog_is_atcode() -> None:
    assert code_cli.build_parser().prog == "atcode"


def test_code_cli_has_no_context_command() -> None:
    help_text = code_cli.build_parser().format_help()

    assert "context" not in help_text


def test_split_code_matches_benchmark_forward_blank_split() -> None:
    text = "a\nb\nc\nd\n\ne\n"

    chunks = split_code("x.py", text, "python", chunk_lines=4, blank_line_window=2)

    assert [(chunk.start_line, chunk.end_line) for chunk in chunks] == [(1, 5), (6, 6)]
    assert chunks[0].memory_text.startswith("// x.py:1-5\n")
    assert chunks[0].id.endswith(":x.py:1-5")


def test_iter_indexed_files_applies_include_and_exclude(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "keep.py").write_text("print('x')\n", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "skip.py").write_text("print('bad')\n", encoding="utf-8")
    (tmp_path / "README.txt").write_text("not included\n", encoding="utf-8")
    config = default_config(tmp_path, "demo")

    files = list(iter_indexed_files(tmp_path, config))

    assert [item.rel_path for item in files] == ["src/keep.py"]
    assert files[0].chunks[0].text == "print('x')\n"


def test_default_index_includes_common_repo_files_without_all_json(tmp_path: Path) -> None:
    files = {
        "README.md": "# docs\n",
        "Dockerfile": "FROM python:3.12\n",
        "Makefile": "test:\n\tpytest\n",
        "schema.proto": "syntax = \"proto3\";\n",
        "query.sql": "select 1;\n",
        "pyproject.toml": "[project]\nname = \"demo\"\n",
        "config.yaml": "enabled: true\n",
        "package.json": "{\"scripts\":{\"test\":\"vitest\"}}\n",
        "data.json": "[1,2,3]\n",
        "analysis.R": "print('x')\n",
        "app.dart": "void main() {}\n",
        "kernel.cu": "__global__ void k() {}\n",
    }
    for name, text in files.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    config = default_config(tmp_path, "demo")

    indexed = {item.rel_path: item.language for item in iter_indexed_files(tmp_path, config)}

    assert indexed["README.md"] == "markdown"
    assert indexed["Dockerfile"] == "dockerfile"
    assert indexed["Makefile"] == "makefile"
    assert indexed["schema.proto"] == "protobuf"
    assert indexed["query.sql"] == "sql"
    assert indexed["pyproject.toml"] == "toml"
    assert indexed["config.yaml"] == "yaml"
    assert indexed["package.json"] == "json"
    assert indexed["analysis.R"] == "r"
    assert indexed["app.dart"] == "dart"
    assert indexed["kernel.cu"] == "cuda"
    assert "data.json" not in indexed


def test_index_file_adds_manual_file_and_marks_it_for_reindex(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    write_config(tmp_path, default_config(tmp_path, "demo"))
    (tmp_path / "a.py").write_text("print('a')\n", encoding="utf-8")
    (tmp_path / "data.json").write_text("{\"enabled\": true}\n", encoding="utf-8")
    config = load_config(tmp_path)
    existing_chunk = split_code("a.py", "print('a')\n", "python", config.chunk_lines, config.blank_line_window)[0]
    code_cli.write_local_index_metadata(
        tmp_path,
        config,
        records=[code_cli.chunk_record(existing_chunk)],
        file_count=1,
        chunk_count=1,
        status=session_status(memory_count=2, segment_count=1, total_tokens=10),
    )
    monkeypatch.chdir(tmp_path)

    class FakeClient:
        def __init__(self) -> None:
            self.calls: list[str] = []
            self.memory_count = 2
            self.segment_count = 1

        def health(self) -> bool:
            self.calls.append("health")
            return True

        def restore_session(self, session_id: str) -> dict[str, str]:
            self.calls.append(f"restore:{session_id}")
            return {}

        def list_sessions(self) -> list[SessionStatus]:
            return [session_status(memory_count=self.memory_count, segment_count=self.segment_count, total_tokens=42)]

        def next_segment(self, session_id: str) -> dict[str, str]:
            self.calls.append(f"next:{session_id}")
            self.segment_count += 1
            return {}

        def add_memory(self, memory: Any, *, session_id: str) -> TokenUsage:
            self.memory_count += 1
            if isinstance(memory, str):
                self.calls.append(memory)
            else:
                self.calls.append(f"chunk:{memory['id']}")
            return usage(100000)

        def index_session(self, session_id: str) -> dict[str, str]:
            self.calls.append(f"index:{session_id}")
            return {}

        def save_session(self, session_id: str) -> dict[str, str]:
            self.calls.append(f"save:{session_id}")
            return {}

    fake = FakeClient()
    monkeypatch.setattr(code_cli, "client_for", lambda _: fake)

    code = code_cli.main(["index", "--file", "data.json"])

    assert code == 0
    assert fake.calls[0:3] == ["health", "restore:demo", "next:demo"]
    assert fake.calls[3] == "// the following code come from data.json"
    assert fake.calls[4].startswith("chunk:")
    assert fake.calls[-2:] == ["index:demo", "save:demo"]
    metadata = code_cli.load_index(tmp_path)
    assert metadata["file_count"] == 2
    assert metadata["chunk_count"] == 2
    assert metadata["memory_count"] == 4
    records = code_cli.load_chunks(chunks_path(tmp_path))
    assert any(record.file == "data.json" and record.language == "json" for record in records.values())
    config = load_config(tmp_path)
    assert config.include_files == ["data.json"]
    indexed = {item.rel_path for item in iter_indexed_files(tmp_path, config)}
    assert "data.json" in indexed


def test_map_and_fuse_hits_use_chunk_metadata(tmp_path: Path) -> None:
    write_config(tmp_path, default_config(tmp_path, "demo"))
    records = {
        "0": code_cli.ChunkRecord("0", "a.py", 1, 10, "python", ""),
        "1": code_cli.ChunkRecord("1", "a.py", 11, 20, "python", ""),
        "2": code_cli.ChunkRecord("2", "b.py", 1, 5, "python", ""),
    }
    results = [
        SearchResult(id="1", text="", rank=1),
        SearchResult(id=None, text="header", rank=2),
        SearchResult(id="2", text="", rank=3),
        SearchResult(id="0", text="", rank=4),
    ]

    ordered_ids = code_cli.ordered_chunk_ids(results, records)
    id_segments = code_cli.result_segments_by_id(results, records)
    hits = code_cli.chunk_hits_from_order(
        ordered_ids=ordered_ids,
        id_segments=id_segments,
        memory_map=records,
        candidate_chunk_top_k=10,
    )
    fused = code_cli.fused_hits(chunk_hits=hits, display_top_k=10)

    assert [hit["memory_id"] for hit in hits] == ["1", "2", "0"]
    assert fused[0]["path"] == "a.py"
    assert fused[0]["ranges"] == [{"start_line": 1, "end_line": 20}]
    assert fused[1]["path"] == "b.py"


def test_user_query_context_keeps_question_intro_and_replaces_default_guidance() -> None:
    query_context = code_cli.build_query_context("where is parser", "Focus on public APIs.")

    assert query_context == (
        "Read the above code snippets carefully and answer the user's repository question: where is parser."
        "\n\n"
        "Focus on public APIs."
    )
    assert code_cli.QUERY_CONTEXT_DEFAULT_GUIDANCE not in query_context
    assert code_cli.QUERY_CONTEXT_DEFAULT_GUIDANCE in code_cli.build_query_context("where is parser")


def test_retrieve_hits_reranks_until_result_is_single_segment(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    config = default_config(tmp_path, "demo")
    records = {
        "0": code_cli.ChunkRecord("0", "a.py", 1, 10, "python", ""),
        "1": code_cli.ChunkRecord("1", "b.py", 1, 10, "python", ""),
    }

    class FakeClient:
        oneshot_calls = 0

        def health(self) -> bool:
            return True

        def restore_session(self, session_id: str) -> dict[str, str]:
            assert session_id == "demo"
            return {}

        def list_sessions(self) -> list[SessionStatus]:
            return [
                SessionStatus(
                    session_id="demo",
                    memory_count=2,
                    segment_count=2,
                    total_tokens=100,
                    resident_segments=0,
                    indexed_segments=2,
                    saved_segments=2,
                    indexed=True,
                    disk_cached=True,
                    plan_ready=True,
                    facts_dirty=False,
                    kv_persist=True,
                )
            ]

        def search(self, query: str, **kwargs: Any) -> list[SearchResult]:
            assert query == "Question: where"
            assert kwargs["query_context"]
            return [
                SearchResult(id="0", text="alpha", rank=1, segment_id=0),
                SearchResult(id="1", text="beta", rank=2, segment_id=1),
            ]

        def oneshot_search(self, query: str, memories: list[dict[str, str]], **kwargs: Any) -> list[SearchResult]:
            self.oneshot_calls += 1
            assert [memory.get("id") for memory in memories] == [None, "0", None, "1"]
            assert kwargs["system"] == code_cli.RERANK_SYSTEM_PROMPT
            if self.oneshot_calls == 1:
                return [
                    SearchResult(id="1", text="beta", rank=1, segment_id=1),
                    SearchResult(id="0", text="alpha", rank=2, segment_id=0),
                ]
            return [
                SearchResult(id="1", text="beta", rank=1, segment_id=0),
                SearchResult(id="0", text="alpha", rank=2, segment_id=0),
            ]

    fake = FakeClient()
    monkeypatch.setattr(code_cli, "client_for", lambda _: fake)

    result = code_cli.retrieve_hits(
        tmp_path,
        config,
        "where",
        records,
        display_top_k=2,
        candidate_chunk_top_k=2,
    )

    assert fake.oneshot_calls == 2
    assert result["oneshot_passes"] == 2
    assert [hit["memory_id"] for hit in result["chunk_hits"]] == ["1", "0"]
    assert [hit["path"] for hit in result["hits"]] == ["b.py", "a.py"]


def test_search_defaults_to_compact_context_output(
    tmp_path: Path,
    monkeypatch: Any,
    capsys: Any,
) -> None:
    record = write_single_chunk_index(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(code_cli, "retrieve_hits", lambda *args, **kwargs: fake_search_result(record))

    code = code_cli.main(["search", "where"])

    assert code == 0
    out = capsys.readouterr().out
    assert "<semantic_search_results>" in out
    assert "1. a.py:1" in out
    assert "--- Result" not in out


def test_search_raw_keeps_ranked_chunk_output(
    tmp_path: Path,
    monkeypatch: Any,
    capsys: Any,
) -> None:
    record = write_single_chunk_index(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(code_cli, "retrieve_hits", lambda *args, **kwargs: fake_search_result(record))

    code = code_cli.main(["search", "--raw", "where"])

    assert code == 0
    out = capsys.readouterr().out
    assert "--- Result 1 ---" in out
    assert "File: a.py:1-1 [python]" in out
    assert "print('a')" in out
    assert "<semantic_search_results>" not in out


def test_index_splits_only_before_new_file(tmp_path: Path) -> None:
    write_config(tmp_path, default_config(tmp_path, "demo"))
    (tmp_path / "a.py").write_text("print('a')\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("print('b')\n", encoding="utf-8")

    class FakeClient:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def health(self) -> bool:
            return True

        def create_session(self, session_id: str, *, kv_persist: bool) -> TokenUsage:
            self.calls.append(f"create:{session_id}:{kv_persist}")
            return usage(100000)

        def add_system(self, text: str, *, session_id: str) -> TokenUsage:
            self.calls.append("system")
            return usage(60000)

        def next_segment(self, session_id: str) -> dict[str, str]:
            self.calls.append("next")
            return {}

        def add_memory(self, memory: Any, *, session_id: str) -> TokenUsage:
            if isinstance(memory, str):
                self.calls.append(f"header:{memory}")
                return usage(1000)
            self.calls.append(f"chunk:{memory['id']}")
            return usage(100)

        def index_session(self, session_id: str) -> dict[str, str]:
            self.calls.append("index")
            return {}

        def save_session(self, session_id: str) -> dict[str, str]:
            self.calls.append("save")
            return {}

        def list_sessions(self) -> list[SessionStatus]:
            return [
                SessionStatus(
                    session_id="demo",
                    memory_count=2,
                    segment_count=2,
                    total_tokens=100,
                    resident_segments=0,
                    indexed_segments=2,
                    saved_segments=2,
                    indexed=True,
                    disk_cached=True,
                    plan_ready=True,
                    facts_dirty=False,
                    kv_persist=True,
                )
            ]

    fake = FakeClient()

    code_cli.build_index(tmp_path, load_config(tmp_path), client=fake)

    assert fake.calls[:4] == [
        "create:demo:True",
        "system",
        "next",
        "header:// the following code come from a.py",
    ]
    assert fake.calls[4].startswith("chunk:")
    assert fake.calls[4].endswith(":a.py:1-1")
    assert fake.calls[5:7] == [
        "next",
        "header:// the following code come from b.py",
    ]
    assert fake.calls.count("next") == 2


def test_index_resume_uses_existing_session_and_writes_metadata(tmp_path: Path, monkeypatch: Any) -> None:
    write_config(tmp_path, default_config(tmp_path, "demo"))
    (tmp_path / "a.py").write_text("print('a')\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    class FakeClient:
        def __init__(self) -> None:
            self.calls: list[str] = []
            self.indexed = False
            self.saved = False

        def health(self) -> bool:
            self.calls.append("health")
            return True

        def restore_session(self, session_id: str) -> dict[str, str]:
            self.calls.append(f"restore:{session_id}")
            return {}

        def list_sessions(self) -> list[SessionStatus]:
            return [
                SessionStatus(
                    session_id="demo",
                    memory_count=2,
                    segment_count=1,
                    total_tokens=42,
                    resident_segments=0,
                    indexed_segments=1 if self.indexed else 0,
                    saved_segments=1 if self.saved else 0,
                    indexed=self.indexed,
                    disk_cached=self.saved,
                    plan_ready=True,
                    facts_dirty=False,
                    kv_persist=True,
                )
            ]

        def index_session(self, session_id: str) -> dict[str, str]:
            self.calls.append(f"index:{session_id}")
            self.indexed = True
            return {}

        def save_session(self, session_id: str) -> dict[str, str]:
            self.calls.append(f"save:{session_id}")
            self.saved = True
            return {}

    fake = FakeClient()
    monkeypatch.setattr(code_cli, "client_for", lambda _: fake)

    code = code_cli.main(["index", "--resume"])

    assert code == 0
    assert fake.calls == ["health", "restore:demo", "index:demo", "save:demo"]
    assert chunks_path(tmp_path).is_file()
    metadata = code_cli.load_index(tmp_path)
    assert metadata["memory_count"] == 2
    assert metadata["token_count"] == 42


def test_index_resume_repairs_metadata_when_session_is_already_indexed(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    write_config(tmp_path, default_config(tmp_path, "demo"))
    (tmp_path / "a.py").write_text("print('a')\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    class FakeClient:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def health(self) -> bool:
            self.calls.append("health")
            return True

        def restore_session(self, session_id: str) -> dict[str, str]:
            self.calls.append(f"restore:{session_id}")
            return {}

        def list_sessions(self) -> list[SessionStatus]:
            return [
                SessionStatus(
                    session_id="demo",
                    memory_count=2,
                    segment_count=1,
                    total_tokens=42,
                    resident_segments=0,
                    indexed_segments=1,
                    saved_segments=1,
                    indexed=True,
                    disk_cached=True,
                    plan_ready=True,
                    facts_dirty=False,
                    kv_persist=True,
                )
            ]

        def index_session(self, session_id: str) -> dict[str, str]:
            self.calls.append(f"index:{session_id}")
            return {}

        def save_session(self, session_id: str) -> dict[str, str]:
            self.calls.append(f"save:{session_id}")
            return {}

    fake = FakeClient()
    monkeypatch.setattr(code_cli, "client_for", lambda _: fake)

    code = code_cli.main(["index", "--resume"])

    assert code == 0
    assert fake.calls == ["health", "restore:demo"]
    assert chunks_path(tmp_path).is_file()


def usage(remaining_tokens: int) -> TokenUsage:
    return TokenUsage(
        prefill_tokens=0,
        ctx_length=262144,
        remaining_tokens=remaining_tokens,
        segment_id=0,
        segment_count=1,
    )


def session_status(*, memory_count: int, segment_count: int, total_tokens: int) -> SessionStatus:
    return SessionStatus(
        session_id="demo",
        memory_count=memory_count,
        segment_count=segment_count,
        total_tokens=total_tokens,
        resident_segments=0,
        indexed_segments=segment_count,
        saved_segments=segment_count,
        indexed=True,
        disk_cached=True,
        plan_ready=True,
        facts_dirty=False,
        kv_persist=True,
    )


def write_single_chunk_index(tmp_path: Path) -> code_cli.ChunkRecord:
    write_config(tmp_path, default_config(tmp_path, "demo"))
    (tmp_path / "a.py").write_text("print('a')\n", encoding="utf-8")
    record = code_cli.ChunkRecord("0", "a.py", 1, 1, "python", "")
    code_cli.write_local_index_metadata(
        tmp_path,
        load_config(tmp_path),
        records=[record],
        file_count=1,
        chunk_count=1,
        status=session_status(memory_count=2, segment_count=1, total_tokens=10),
    )
    return record


def fake_search_result(record: code_cli.ChunkRecord) -> dict[str, Any]:
    return {
        "hits": [
            {
                "path": "a.py",
                "language": "python",
                "rank": 1,
                "ranges": [{"start_line": 1, "end_line": 1}],
            }
        ],
        "chunk_hits": [
            {
                "path": "a.py",
                "start_line": 1,
                "end_line": 1,
                "chunk_rank": 1,
                "memory_id": "0",
                "segment_id": 0,
                "record": record,
            }
        ],
        "raw_result_count": 1,
        "raw_segment_count": 1,
        "oneshot_passes": 0,
    }



def test_add_to_gitignore_is_idempotent(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()

    add_to_gitignore(tmp_path)
    add_to_gitignore(tmp_path)

    text = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert text.count("/.attemory/") == 1


def test_index_dry_run_requires_no_server(tmp_path: Path, monkeypatch: Any, capsys: Any) -> None:
    write_config(tmp_path, default_config(tmp_path, "demo"))
    (tmp_path / "main.py").write_text("print('hello')\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    code = code_cli.main(["index", "--dry-run"])

    assert code == 0
    assert "files=1 chunks=1" in capsys.readouterr().out
    assert not chunks_path(tmp_path).exists()
    assert config_path(tmp_path).exists()
