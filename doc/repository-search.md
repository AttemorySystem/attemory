# Repository Search Usage

Repository Search is Attemory's semantic retrieval workflow for code
repositories. It indexes a repository once, stores the index in an Attemory
session, and later returns ranked file and line evidence for natural-language
code questions.

It has two user-facing entry points:

- `attemory code`: local CLI for repository initialization, indexing, search,
  status, and maintenance. The short alias is `atcode`.
- Claude Code plugin: read-only agent integration that exposes the same
  repository search capability through MCP.

The existing top-level `attemory` CLI still exposes low-level retrieval engine
commands. `attemory code` and `atcode` are the higher-level Repository Search
workflow. The examples below use `attemory code`; replace it with `atcode` if
you prefer the shorter command.

## Local CLI Usage

### Start The Server

`attemory code` does not start `attemory-server` for you. Start the server
explicitly in another terminal:

```bash
attemory-server --small --backend gpu # start a server listening to 127.0.0.1:9006
```

### Basic Workflow

From a repository root:

```bash
attemory code init
attemory code index
attemory code search "Where is session restore implemented?"

attemory code reindex # re-index a repo when file changes
attemory code reset   # remove the index and configured session
```

The short alias `atcode` accepts the same subcommands.

## Project Metadata

`attemory code init` and `attemory code index` keep local project metadata under
`.attemory/`:

```text
.attemory/
  config.toml    # project config: session, server, indexing, search, include/exclude settings
  index.json     # local index summary written after indexing
  chunks.jsonl   # chunk id to file/range/language metadata written after indexing
```

`config.toml` records the Attemory session id, server host and port,
`kv_persist`, chunking settings, search defaults, include/exclude patterns, and
manually added files under `[include] files`.

`index.json` records the local index summary: session id, repository root, git
commit at indexing time, context structure, file count, chunk count, memory
count, token count, and segment count.

`chunks.jsonl` maps indexed chunk ids back to repository files, line ranges,
languages, and chunk metadata. Search results use this file to print compact
file/range evidence.

The actual searchable KV state lives in the configured Attemory session and, for
`kv_persist` sessions, the server-side disk KV cache.

## What Gets Indexed

By default, `attemory code` indexes common source and documentation files such
as:

```text
.bash, .bzl, .c, .cc, .clj, .cljc, .cljs, .cmake, .cpp, .cs,
.css, .cts, .cu, .cuh, .cxx, .d, .dart, .ex, .exs, .erl,
.fs, .fsi, .fsx, .go, .gradle, .h, .hh, .hpp, .hrl, .hs,
.htm, .html, .hxx, .java, .js, .jsx, .jl, .kt, .kts, .less,
.lhs, .lua, .m, .md, .mm, .mjs, .mts, .php, .proto, .pxd,
.pxi, .py, .pyi, .pyx, .r, .R, .rb, .rst, .rs, .sass, .sc,
.scala, .scss, .sh, .sql, .svelte, .swift, .toml, .ts, .tsx,
.vue, .vb, .yaml, .yml, .zig
```

It also includes common project files such as `Dockerfile`, `Makefile`,
`Justfile`, `CMakeLists.txt`, `BUILD`, `WORKSPACE`, `deno.json`,
`package.json`, `tsconfig.json`, `pyproject.toml`, and common lockfiles.
Generic `.json` files are not indexed by default because many repositories use
them as data; add an include pattern when JSON or JSONL files are part of the
code context you want searched.

It skips common generated or dependency directories such as:

```text
.git, .venv, venv, __pycache__, node_modules, site-packages, build, dist,
.attemory
```

To check whether a file is indexed, grep the `.attemory` metadata directory. If
a file you want was not included, add it manually:

```bash
grep -R "path/to/file" .attemory
attemory code index --file path/to/file
```

Edit `.attemory/config.toml` to change include/exclude patterns or default
search settings. `include.patterns` uses glob patterns over repository-relative
paths, so you can add project-specific file types:

```toml
[include]
patterns = [
  "**/*.py",
  "**/*.md",
  "**/*.jsonl",
  "**/*.prompt",
]
```

Files matched by `include.patterns` are indexed even when their suffix is not
part of Repository Search's default source-file list. Unknown suffixes are
indexed as plain text unless Attemory recognizes the language.
`exclude.patterns` still take precedence over `include.patterns`.

Use `exclude.patterns` to remove generated files, data files, or file types
that are too noisy for code search:

```toml
[exclude]
patterns = [
  "generated/**",
  "data/large/**",
  "**/*.min.js",
  "**/*.pb.go",
  "**/*.json",
  "**/*.jsonl",
]
```

The scan order is:

1. skip built-in generated/dependency directories such as `.git`,
   `node_modules`, and `.attemory`
2. skip paths matched by `exclude.patterns`
3. index paths matched by `include.patterns`
4. append explicit `[include].files` entries, such as files added with
   `attemory code index --file`, if they are inside the repository and contain
   indexable text

`exclude.patterns` applies before `include.patterns`, so an excluded path is not
indexed during the normal recursive scan even if an include pattern also matches
it. Explicit `[include].files` entries are meant for user-selected files and are
handled separately from the recursive include/exclude scan.

## Repository Indexing

Repository Search stores each repository as a structured memory stream:

```text
system prompt
file header for path/to/file.py
code chunk for path/to/file.py:1-30
code chunk for path/to/file.py:31-60
file header for path/to/other.py
code chunk for path/to/other.py:1-30
...
```

The context structure recorded in `.attemory/index.json` is:

```text
system-v1-file-header-v1-code-chunk-v1
```

The system prompt tells Attemory to read code and retrieve the most relevant
code for the query. Each file contributes one file header memory, which gives
later chunks their file-path context:

```text
// the following code come from path/to/file.py
```

Each code chunk is stored with a stable chunk id, but the memory text is only
the source lines. File paths, line ranges, language, and content hashes are kept
in `.attemory/chunks.jsonl` metadata and used when search results are printed.

By default, files are split into roughly 30-line chunks:

```toml
[index]
chunk_lines = 30
blank_line_window = 10
context_window = 262144
file_boundary_reserve_percent = 25
```

When a chunk boundary falls near a blank line, Repository Search can extend the
chunk by up to `blank_line_window` lines to end at that blank line. This keeps
nearby code together while preserving predictable chunk sizes.

During indexing, Repository Search adds the system prompt, then file headers and
chunks in repository path order. It starts a new Attemory segment only before a
new file when the remaining token budget is below
`file_boundary_reserve_percent` of `context_window`; with the defaults above,
that means it prefers to start a new segment before a file when fewer than
65,536 tokens remain. It does not split a file between segments when that file
fits in the current segment.

## Commands

### `attemory code init`

Initialize the current repository for Repository Search.

```bash
attemory code init
```

This creates:

```text
.attemory/config.toml
```

If the repository has a `.git` directory, `attemory code init` also adds
`/.attemory/` to `.gitignore`.

To choose the session id explicitly:

```bash
attemory code init --session-id attemory-code-myrepo
```

Server host and port are stored in `.attemory/config.toml`. Edit that file if
your server is not running at `127.0.0.1:9006`.

### `attemory code index`

Build the repository index if it does not already exist.

```bash
attemory code index
```

`attemory code index` is non-destructive. If the repo is already indexed, it
exits without rebuilding and tells you to use `attemory code reindex` when you
really want a rebuild.

Preview the scan without contacting the server:

```bash
attemory code index --dry-run
```

Resume an interrupted index after the repo memories were already added to the
configured session:

```bash
attemory code index --resume
```

`--resume` uses the configured `session_id`, continues server-side indexing only
when segment KV is missing, persists KV only when needed, and rewrites local
`.attemory/index.json` and `.attemory/chunks.jsonl`. If the session is already
indexed and disk-cached, this repairs only local metadata. It checks that the
session memory count matches the current repo scan before writing metadata.

Add a file that was intentionally skipped by the default scan:

```bash
attemory code index --file path/to/file.json
```

`--file` appends one file to an existing index. Repeat it to add multiple files.
Added files are recorded in `.attemory/config.toml` under `[include] files`, so
later `attemory code reindex` includes them automatically.

### `attemory code reindex`

Explicitly rebuild the repository index from scratch.

```bash
attemory code reindex
```

This can delete and recreate the configured Attemory session and rewrite local
metadata. It asks for confirmation by default.

Skip the prompt:

```bash
attemory code reindex -f
```

Preview the scan:

```bash
attemory code reindex --dry-run
```

### `attemory code search`

Search the existing code index and print compact file/range evidence for an
agent prompt.

Use natural-language questions or descriptions, not keyword-only queries. Ask
for the behavior, responsibility, implementation path, entry point, definition,
or cross-file flow you want to understand.

```bash
attemory code search "Where does session restore load persisted segment KV state?"
```

Default output:

```text
<semantic_search_results>
The following files and line ranges are semantic-search candidate evidence from the repository.

1. src/context/session/session_manager.cpp:467-528
2. src/context/kv/segment_kv_cache_commands.cpp:227-326
</semantic_search_results>
```

Options:

```bash
attemory code search "Which code enforces the resident KV RAM budget during search?" --display-top-k 20
attemory code search "How does route parsing dispatch session commands?" --candidate-chunk-top-k 40
attemory code search "Where does the model resolver build HTTP request headers?" --query-context "Prefer code that builds outbound HTTP requests."
attemory code search "Where does the model resolver build HTTP request headers?" --include-snippets
attemory code search "Where does the model resolver build HTTP request headers?" --format markdown
attemory code search "Where does the model resolver build HTTP request headers?" --json
attemory code search "Where does the model resolver build HTTP request headers?" --raw
```

`--display-top-k` controls the number of results printed.
`--candidate-chunk-top-k` controls the chunk candidate pool used by search and
one-shot reranking. `--query-context` adds request-specific retrieval guidance.
Attemory still prefixes the repository question framing before your custom
context. If omitted, `attemory code search` uses the default code-navigation
query context. `--raw` prints the ranked chunk/snippet view instead of compact
file/range evidence.

`attemory code search` never rebuilds the index. If you want a fresh index, run
`attemory code reindex` yourself.

When raw results come from multiple Attemory segments, `attemory code search`
runs one-shot reranking over the returned chunks. If a one-shot rerank result
still spans multiple segments, reranking continues, up to 8 passes.

### `attemory code status`

Show project, metadata, server, and session status.

```bash
attemory code status
```

Example fields:

```text
Project: /path/to/repo
Config: .attemory/config.toml
Session: attemory-code-myrepo-9f3a12c0
Metadata: present
Indexed files: 812
Indexed chunks: 4260
Server: 127.0.0.1:9006 healthy
Session status: indexed=true kv_persist=true
```

### `attemory code doctor`

Diagnose common setup problems.

```bash
attemory code doctor
```

It checks whether:

- the project has `.attemory/config.toml`
- local index metadata exists
- `attemory-server` is reachable
- the configured session can be restored
- the session is indexed
- the session uses `kv_persist`

### `attemory code reset`

Remove local index metadata and the configured Attemory session.

```bash
attemory code reset
```

By default, this deletes `.attemory/index.json`, `.attemory/chunks.jsonl`, and
the configured server-side session. It keeps `.attemory/config.toml`, so the
project can be indexed again with the same settings.

Remove config and the `.gitignore` entry too:

```bash
attemory code reset --all
```

Skip confirmation:

```bash
attemory code reset --all -f
```

## Important Behavior

`attemory code` intentionally keeps expensive operations explicit:

- it does not start `attemory-server`
- `attemory code search` does not run `attemory code index`
- `attemory code index` does not rebuild an existing index
- `attemory code reindex` is the explicit rebuild command

`attemory code` also does not actively compare the current git commit with the
commit recorded when the index was built. If you changed the repository and
want a fresh index, run:

```bash
attemory code reindex
```

## MCP And Agent Usage

The Claude Code plugin lets agents use Repository Search over repositories
that were already indexed with `attemory code`.

```bash
attemory-server --small --backend gpu --port 9006
cd /path/to/repo
attemory code init
attemory code index
```

### Claude Code

#### Install

Install the plugin:

```bash
claude plugin marketplace add AttemorySystem/attemory-claude-code
claude plugin install attemory-code@attemory
```

If Claude Code is already running, reload plugins:

```text
/reload-plugins
```

#### Verify

```bash
claude mcp list
```

Expected:

```text
plugin:attemory-code:attemory-code: attemory-mcp - Connected
```

If there is also an old non-plugin `attemory-code` entry, remove it:

```bash
claude mcp remove attemory-code -s user
```

#### Use

```text
Use attemory-code search to find where session exception flow is implemented.
```

Ask with natural-language code questions. More specific questions usually
return better file and line evidence.

#### Delete

```bash
claude plugin uninstall attemory-code@attemory
```

If needed, remove the marketplace too:

```bash
claude plugin marketplace remove attemory
```

### Codex

Codex integration is planned, but the plugin has not been published or tested
yet.

### Pi-agent

Pi-agent integration is planned, but the plugin has not been published or
tested yet.

### opencode

opencode integration is planned, but the plugin has not been published or
tested yet.

## Troubleshooting

Server is not reachable:

```text
error: cannot reach Attemory server at 127.0.0.1:9006
```

Start the server:

```bash
attemory-server --small --backend gpu --port 9006
```

No project config:

```text
error: not in an Attemory code project; run `atcode init`
```

Run:

```bash
attemory code init
```

No index metadata:

```text
error: no Attemory code index found for this repository; run `atcode index`
```

Run:

```bash
attemory code index
```

Existing index:

```text
repo is already indexed; run `atcode reindex` to rebuild
```

Run:

```bash
attemory code reindex
```
