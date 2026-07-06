from __future__ import annotations

import ast
import fnmatch
import hashlib
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping


CONFIG_DIR = ".attemory"
CONFIG_NAME = "config.toml"
INDEX_NAME = "index.json"
CHUNKS_NAME = "chunks.jsonl"

SYSTEM_PROMPT = "Read the following code carefully and find the most relevant code to the query."
FILE_HEADER_PREFIX = "// the following code come from "
CONTEXT_STRUCTURE = "system-v1-file-header-v1-code-chunk-v1"

DEFAULT_CODE_SUFFIXES = {
    ".bash",
    ".bzl",
    ".c",
    ".cc",
    ".clj",
    ".cljc",
    ".cljs",
    ".cmake",
    ".cpp",
    ".cs",
    ".css",
    ".cts",
    ".cu",
    ".cuh",
    ".cxx",
    ".d",
    ".dart",
    ".ex",
    ".exs",
    ".erl",
    ".fs",
    ".fsi",
    ".fsx",
    ".go",
    ".gradle",
    ".h",
    ".hh",
    ".hpp",
    ".hrl",
    ".hs",
    ".htm",
    ".html",
    ".hxx",
    ".java",
    ".js",
    ".jsx",
    ".jl",
    ".kt",
    ".kts",
    ".less",
    ".lhs",
    ".lua",
    ".m",
    ".md",
    ".mm",
    ".mjs",
    ".mts",
    ".php",
    ".proto",
    ".pxd",
    ".pxi",
    ".py",
    ".pyi",
    ".pyx",
    ".R",
    ".r",
    ".rb",
    ".rst",
    ".rs",
    ".sass",
    ".sc",
    ".scala",
    ".scss",
    ".sh",
    ".sql",
    ".svelte",
    ".swift",
    ".ts",
    ".tsx",
    ".toml",
    ".vue",
    ".vb",
    ".yaml",
    ".yml",
    ".zig",
}
SPECIAL_CODE_FILENAMES = {
    "BUILD",
    "BUILD.bazel",
    "CMakeLists.txt",
    "Dockerfile",
    "Justfile",
    "Makefile",
    "WORKSPACE",
    "deno.json",
    "package.json",
    "pnpm-lock.yaml",
    "pyproject.toml",
    "tsconfig.json",
    "uv.lock",
    "yarn.lock",
}
SKIP_DIRS = {
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".svn",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "site-packages",
    "venv",
}
DEFAULT_INCLUDE_PATTERNS = sorted(
    [f"**/*{suffix}" for suffix in DEFAULT_CODE_SUFFIXES]
    + list(SPECIAL_CODE_FILENAMES)
    + [f"**/{name}" for name in SPECIAL_CODE_FILENAMES]
)

DEFAULT_EXCLUDE_PATTERNS = [
    ".git/**",
    ".hg/**",
    ".mypy_cache/**",
    ".pytest_cache/**",
    ".ruff_cache/**",
    ".svn/**",
    ".tox/**",
    ".venv/**",
    "__pycache__/**",
    "build/**",
    "dist/**",
    "node_modules/**",
    "site-packages/**",
    "venv/**",
    ".attemory/**",
]

_LANG_BY_SUFFIX = {
    ".bash": "bash",
    ".bzl": "starlark",
    ".c": "c",
    ".cc": "cpp",
    ".clj": "clojure",
    ".cljc": "clojure",
    ".cljs": "clojure",
    ".cpp": "cpp",
    ".cs": "csharp",
    ".css": "css",
    ".cts": "typescript",
    ".cu": "cuda",
    ".cuh": "cuda",
    ".cxx": "cpp",
    ".d": "d",
    ".dart": "dart",
    ".ex": "elixir",
    ".exs": "elixir",
    ".erl": "erlang",
    ".fs": "fsharp",
    ".fsi": "fsharp",
    ".fsx": "fsharp",
    ".go": "go",
    ".gradle": "gradle",
    ".h": "c",
    ".hh": "cpp",
    ".hpp": "cpp",
    ".hrl": "erlang",
    ".hs": "haskell",
    ".htm": "html",
    ".html": "html",
    ".hxx": "cpp",
    ".java": "java",
    ".js": "javascript",
    ".jsx": "javascript",
    ".jl": "julia",
    ".json": "json",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".less": "less",
    ".lhs": "haskell",
    ".lua": "lua",
    ".m": "objective-c",
    ".mjs": "javascript",
    ".mm": "objective-cpp",
    ".md": "markdown",
    ".mts": "typescript",
    ".php": "php",
    ".proto": "protobuf",
    ".pxd": "python",
    ".pxi": "python",
    ".py": "python",
    ".pyi": "python",
    ".pyx": "python",
    ".r": "r",
    ".rb": "ruby",
    ".rst": "restructuredtext",
    ".rs": "rust",
    ".sass": "sass",
    ".sc": "scala",
    ".scala": "scala",
    ".scss": "scss",
    ".sh": "shell",
    ".sql": "sql",
    ".svelte": "svelte",
    ".swift": "swift",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".toml": "toml",
    ".vue": "vue",
    ".vb": "vbnet",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".zig": "zig",
}
_LANG_BY_FILENAME = {
    "BUILD": "starlark",
    "BUILD.bazel": "starlark",
    "CMakeLists.txt": "cmake",
    "Dockerfile": "dockerfile",
    "Justfile": "just",
    "Makefile": "makefile",
    "WORKSPACE": "starlark",
    "deno.json": "json",
    "package.json": "json",
    "pnpm-lock.yaml": "yaml",
    "pyproject.toml": "toml",
    "tsconfig.json": "json",
    "uv.lock": "toml",
    "yarn.lock": "yaml",
}


@dataclass(frozen=True)
class CodeConfig:
    version: int
    session_id: str
    host: str
    port: int
    kv_persist: bool
    chunk_lines: int
    blank_line_window: int
    context_window: int
    file_boundary_reserve_percent: int
    display_top_k: int
    candidate_chunk_top_k: int
    include_patterns: list[str]
    include_files: list[str]
    exclude_patterns: list[str]

    @property
    def min_remaining_tokens_before_file(self) -> int:
        return max(1, self.context_window * self.file_boundary_reserve_percent // 100)


@dataclass(frozen=True)
class CodeChunk:
    id: str
    file: str
    start_line: int
    end_line: int
    text: str
    language: str

    @property
    def lines(self) -> str:
        return f"{self.start_line}-{self.end_line}"

    @property
    def memory_text(self) -> str:
        return self.text


@dataclass(frozen=True)
class IndexedFile:
    path: Path
    rel_path: str
    language: str
    chunks: list[CodeChunk]


@dataclass(frozen=True)
class ChunkRecord:
    id: str
    file: str
    start_line: int
    end_line: int
    language: str
    sha256: str

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "file": self.file,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "language": self.language,
            "sha256": self.sha256,
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> ChunkRecord:
        return cls(
            id=str(data["id"]),
            file=str(data["file"]),
            start_line=int(data["start_line"]),
            end_line=int(data["end_line"]),
            language=str(data.get("language", "text")),
            sha256=str(data.get("sha256", "")),
        )


@dataclass(frozen=True)
class ScanSummary:
    files: int
    chunks: int


def config_path(root: Path) -> Path:
    return root / CONFIG_DIR / CONFIG_NAME


def index_path(root: Path) -> Path:
    return root / CONFIG_DIR / INDEX_NAME


def chunks_path(root: Path) -> Path:
    return root / CONFIG_DIR / CHUNKS_NAME


def find_project_root(start: Path) -> Path | None:
    current = start.resolve()
    while True:
        if config_path(current).is_file():
            return current
        if current.parent == current:
            return None
        current = current.parent


def default_config(root: Path, session_id: str | None = None) -> CodeConfig:
    return CodeConfig(
        version=1,
        session_id=session_id or default_session_id(root),
        host="127.0.0.1",
        port=9006,
        kv_persist=True,
        chunk_lines=30,
        blank_line_window=10,
        context_window=262144,
        file_boundary_reserve_percent=25,
        display_top_k=8,
        candidate_chunk_top_k=20,
        include_patterns=list(DEFAULT_INCLUDE_PATTERNS),
        include_files=[],
        exclude_patterns=list(DEFAULT_EXCLUDE_PATTERNS),
    )


def default_session_id(root: Path) -> str:
    slug = _slug(root.name or "repo")
    suffix = git_commit(root, short=True) or hashlib.sha1(str(root).encode("utf-8")).hexdigest()[:12]
    return f"attemory-code-{slug}-{suffix}"


def write_config(root: Path, config: CodeConfig) -> None:
    config_path(root).parent.mkdir(parents=True, exist_ok=True)
    config_path(root).write_text(_format_config(config), encoding="utf-8")


def load_config(root: Path) -> CodeConfig:
    raw = _read_toml(config_path(root))
    index = _mapping(raw.get("index"))
    search = _mapping(raw.get("search"))
    include = _mapping(raw.get("include"))
    exclude = _mapping(raw.get("exclude"))
    return CodeConfig(
        version=int(raw.get("version", 1)),
        session_id=str(raw["session_id"]),
        host=str(raw.get("host", "127.0.0.1")),
        port=int(raw.get("port", 9006)),
        kv_persist=bool(raw.get("kv_persist", True)),
        chunk_lines=int(index.get("chunk_lines", 30)),
        blank_line_window=int(index.get("blank_line_window", 10)),
        context_window=int(index.get("context_window", 262144)),
        file_boundary_reserve_percent=int(index.get("file_boundary_reserve_percent", 25)),
        display_top_k=int(search.get("display_top_k", search.get("top_k", 8))),
        candidate_chunk_top_k=int(search.get("candidate_chunk_top_k", search.get("chunk_top_k", 20))),
        include_patterns=[str(item) for item in include.get("patterns", DEFAULT_INCLUDE_PATTERNS)],
        include_files=[str(item) for item in include.get("files", [])],
        exclude_patterns=[str(item) for item in exclude.get("patterns", DEFAULT_EXCLUDE_PATTERNS)],
    )


def add_to_gitignore(root: Path) -> None:
    if not (root / ".git").is_dir():
        return
    gitignore = root / ".gitignore"
    entry = f"/{CONFIG_DIR}/"
    comment = "# Attemory"
    if gitignore.exists():
        text = gitignore.read_text(encoding="utf-8")
        if entry in text.splitlines():
            return
        if text and not text.endswith("\n"):
            text += "\n"
        text += f"{comment}\n{entry}\n"
    else:
        text = f"{comment}\n{entry}\n"
    gitignore.write_text(text, encoding="utf-8")


def remove_from_gitignore(root: Path) -> None:
    gitignore = root / ".gitignore"
    if not gitignore.is_file():
        return
    entry = f"/{CONFIG_DIR}/"
    comment = "# Attemory"
    lines = gitignore.read_text(encoding="utf-8").splitlines(keepends=True)
    kept: list[str] = []
    for line in lines:
        if line.rstrip("\r\n") == entry:
            if kept and kept[-1].rstrip("\r\n") == comment:
                kept.pop()
            continue
        kept.append(line)
    gitignore.write_text("".join(kept), encoding="utf-8")


def iter_indexed_files(root: Path, config: CodeConfig) -> Iterator[IndexedFile]:
    seen: set[str] = set()
    for path in iter_source_paths(root, config):
        item = indexed_file_from_path(root, config, path)
        if item is None:
            continue
        seen.add(item.rel_path)
        yield item

    for rel_path in config.include_files:
        path = (root / rel_path).resolve()
        try:
            rel = path.relative_to(root)
        except ValueError:
            continue
        if rel.parts and rel.parts[0] == CONFIG_DIR:
            continue
        rel_posix = rel.as_posix()
        if rel_posix in seen:
            continue
        item = indexed_file_from_path(root, config, path)
        if item is None:
            continue
        seen.add(item.rel_path)
        yield item


def indexed_file_from_path(root: Path, config: CodeConfig, path: Path) -> IndexedFile | None:
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    rel_path = path.relative_to(root).as_posix()
    language = detect_language(path)
    chunks = split_code(rel_path, text, language, config.chunk_lines, config.blank_line_window)
    if not chunks:
        return None
    return IndexedFile(path=path, rel_path=rel_path, language=language, chunks=chunks)


def iter_source_paths(root: Path, config: CodeConfig) -> Iterator[Path]:
    paths: list[Path] = []
    for current, dirs, files in os.walk(root):
        current_path = Path(current)
        rel_dir = _rel(current_path, root)
        kept_dirs = []
        for name in dirs:
            rel = f"{rel_dir}/{name}" if rel_dir else name
            if name not in SKIP_DIRS and not _matches_any(rel, config.exclude_patterns):
                kept_dirs.append(name)
        dirs[:] = sorted(kept_dirs)

        for name in files:
            path = current_path / name
            rel = _rel(path, root)
            if _matches_any(rel, config.exclude_patterns):
                continue
            if _matches_any(rel, config.include_patterns):
                paths.append(path)
    yield from sorted(paths, key=lambda item: item.relative_to(root).as_posix())


def split_code(
    rel_path: str,
    text: str,
    language: str,
    chunk_lines: int,
    blank_line_window: int,
) -> list[CodeChunk]:
    lines = text.splitlines(keepends=True)
    chunks: list[CodeChunk] = []
    plain_lines = [line.rstrip("\n\r") for line in lines]
    start = 1
    total = len(lines)
    while start <= total:
        end = min(total, start + max(chunk_lines, 1) - 1)
        if end < total and blank_line_window > 0:
            search_to = min(total, end + blank_line_window)
            for candidate in range(end, search_to + 1):
                if not plain_lines[candidate - 1].strip():
                    end = candidate
                    break
        chunk_text = "".join(lines[start - 1 : end])
        if chunk_text.strip():
            chunks.append(
                CodeChunk(
                    id=chunk_id(rel_path, start, end),
                    file=rel_path,
                    start_line=start,
                    end_line=end,
                    text=chunk_text,
                    language=language,
                )
            )
        start = end + 1
    return chunks


def scan_summary(root: Path, config: CodeConfig) -> ScanSummary:
    files = 0
    chunks = 0
    for item in iter_indexed_files(root, config):
        files += 1
        chunks += len(item.chunks)
    return ScanSummary(files=files, chunks=chunks)


def chunk_record(chunk: CodeChunk) -> ChunkRecord:
    return ChunkRecord(
        id=chunk.id,
        file=chunk.file,
        start_line=chunk.start_line,
        end_line=chunk.end_line,
        language=chunk.language,
        sha256=hashlib.sha256(chunk.memory_text.encode("utf-8")).hexdigest(),
    )


def write_chunks(path: Path, records: Iterable[ChunkRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as out:
        for record in records:
            out.write(json.dumps(record.to_json(), ensure_ascii=False, separators=(",", ":")))
            out.write("\n")


def load_chunks(path: Path) -> dict[str, ChunkRecord]:
    records: dict[str, ChunkRecord] = {}
    with path.open("r", encoding="utf-8") as src:
        for line in src:
            line = line.strip()
            if not line:
                continue
            record = ChunkRecord.from_json(json.loads(line))
            records[record.id] = record
    return records


def load_index(root: Path) -> dict[str, Any]:
    return json.loads(index_path(root).read_text(encoding="utf-8"))


def git_commit(root: Path, *, short: bool = False) -> str | None:
    args = ["git", "-C", str(root), "rev-parse"]
    if short:
        args.append("--short=12")
    args.append("HEAD")
    try:
        result = subprocess.run(args, check=True, capture_output=True, text=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    value = result.stdout.strip()
    return value or None


def detect_language(path: Path) -> str:
    if path.name in _LANG_BY_FILENAME:
        return _LANG_BY_FILENAME[path.name]
    return _LANG_BY_SUFFIX.get(path.suffix.lower(), "text")


def file_header(file_path: str) -> str:
    return f"{FILE_HEADER_PREFIX}{file_path}"


def chunk_id(file_path: str, start_line: int, end_line: int) -> str:
    digest = hashlib.sha1(f"{file_path}:{start_line}:{end_line}".encode("utf-8")).hexdigest()[:12]
    return f"{digest}:{file_path}:{start_line}-{end_line}"


def read_snippet(root: Path, record: ChunkRecord) -> str:
    path = root / record.file
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    start = max(record.start_line - 1, 0)
    end = min(record.end_line, len(lines))
    return "\n".join(lines[start:end])


def _format_config(config: CodeConfig) -> str:
    return "\n".join(
        [
            "version = 1",
            f'session_id = "{config.session_id}"',
            f'host = "{config.host}"',
            f"port = {config.port}",
            f"kv_persist = {_toml_bool(config.kv_persist)}",
            "",
            "[index]",
            f"chunk_lines = {config.chunk_lines}",
            f"blank_line_window = {config.blank_line_window}",
            f"context_window = {config.context_window}",
            f"file_boundary_reserve_percent = {config.file_boundary_reserve_percent}",
            "",
            "[search]",
            f"display_top_k = {config.display_top_k}",
            f"candidate_chunk_top_k = {config.candidate_chunk_top_k}",
            "",
            "[include]",
            f"patterns = {_format_string_list(config.include_patterns)}",
            f"files = {_format_string_list(config.include_files)}",
            "",
            "[exclude]",
            f"patterns = {_format_string_list(config.exclude_patterns)}",
            "",
        ]
    )


def _read_toml(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    try:
        import tomllib
    except ModuleNotFoundError:
        return _parse_simple_toml(text)
    return dict(tomllib.loads(text))


def _parse_simple_toml(text: str) -> dict[str, Any]:
    root: dict[str, Any] = {}
    current = root
    lines = list(text.splitlines())
    index = 0
    while index < len(lines):
        raw = lines[index].strip()
        index += 1
        if not raw or raw.startswith("#"):
            continue
        if raw.startswith("[") and raw.endswith("]"):
            name = raw[1:-1].strip()
            current = root.setdefault(name, {})
            continue
        if "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value.startswith("[") and not value.endswith("]"):
            parts = [value]
            while index < len(lines):
                parts.append(lines[index].strip())
                if lines[index].strip().endswith("]"):
                    index += 1
                    break
                index += 1
            value = " ".join(parts)
        current[key] = _parse_simple_value(value)
    return root


def _parse_simple_value(value: str) -> Any:
    if value == "true":
        return True
    if value == "false":
        return False
    if value.startswith("[") or value.startswith('"'):
        return ast.literal_eval(value)
    try:
        return int(value)
    except ValueError:
        return value


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _format_string_list(values: Iterable[str]) -> str:
    items = ", ".join(json.dumps(value) for value in values)
    return f"[{items}]"


def _toml_bool(value: bool) -> str:
    return "true" if value else "false"


def _rel(path: Path, root: Path) -> str:
    try:
        rel = path.relative_to(root)
    except ValueError:
        return path.as_posix()
    return "" if rel == Path(".") else rel.as_posix()


def _matches_any(rel_path: str, patterns: Iterable[str]) -> bool:
    rel = rel_path.strip("/")
    for pattern in patterns:
        pat = pattern.strip("/")
        if not pat:
            continue
        if fnmatch.fnmatchcase(rel, pat):
            return True
        if pat.startswith("**/") and fnmatch.fnmatchcase(rel, pat[3:]):
            return True
        if pat.endswith("/**"):
            prefix = pat[:-3]
            if rel == prefix or rel.startswith(prefix + "/"):
                return True
    return False


def _slug(value: str) -> str:
    chars = [ch.lower() if ch.isalnum() else "-" for ch in value]
    slug = "-".join(part for part in "".join(chars).split("-") if part)
    return slug or "repo"
