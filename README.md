<p align="center">
  <img src="assets/attemory_logo.png" alt="Attemory" width="320">
</p>

<hr>

<p align="center">
  <sub><b>Attention-native retrieval for AI agents.</b></sub>
</p>

Attemory is an attention-native semantic retrieval engine for long memory,
documents, and codebases.

It indexes raw corpora into reusable KV state, then retrieves evidence by
letting a local model attend over that memory. This is a different retrieval
primitive from nearest-vector lookup: Attemory does not rely on embedding
similarity, BM25, or a vector database as the core retriever.

## Why Attemory

- **Attention-based retrieval path:** search runs through model attention over
  indexed memory. The query is evaluated against model-readable memory through
  the same attention mechanism LLMs use to reason over context, rather than
  only vector distance over compressed embeddings.
- **SOTA-class retrieval quality:** Attemory reaches
  [SOTA-class results on public benchmarks](benchmarks/) across LongMemEval,
  LoCoMo, and Semble without benchmark-specific retrieval hacks.
- **Lower coding-agent token use:** on [SWE-QA](benchmarks/sweqa.md), an
  end-to-end repository question-answering benchmark, one Attemory code-search
  hint reduced Claude Code model tokens by **43.8%** with near-tied judge
  quality across **15 repositories and 720 questions**.

Attemory is benchmark-backed software, not a marketing claim. Reproducible
benchmark notes, adapter patches, run commands, and result summaries are all
available in [benchmarks/](benchmarks/), including
[LongMemEval](benchmarks/LongMemEval.md), [LoCoMo](benchmarks/LoCoMo.md),
[Semble](benchmarks/semble.md), and [SWE-QA](benchmarks/sweqa.md).

Attemory can be used at two levels. See [Documentation](#documentation) for the
full guides:

| Layer | Use it for | Interface |
| --- | --- | --- |
| [**Retrieval engine**](#retrieval-engine-api) | long memory, documents, custom apps, benchmark adapters | Python API / HTTP API |
| [**Repository search**](#repository-search) | index a codebase once, return files and line ranges for agents | `atcode`, Claude Code plugin |

## How It Works

Attemory runs as a local retrieval service:

1. **Index memory into KV state.** Add raw memories, documents, or code chunks
   to a session and build reusable searchable state.
2. **Search by attention.** A local Qwen3.5 retrieval model attends over the
   indexed memory and the query.
3. **Return compact evidence.** Applications receive memory ids, text snippets,
   or file and line ranges that a downstream agent can inspect first.

Large sessions are split into segments internally. Sessions can be configured
with `kv_persist` so indexing writes segment KV cache state to disk and later
searches can restore it without rebuilding.

For implementation details, server options, persistence, templates, and API
behavior, see [doc/usage.md](doc/usage.md).

## Benchmarks

Attemory is evaluated in two ways:

1. **Agent token savings:** can high-recall code search reduce downstream coding-agent exploration?
2. **Retrieval quality:** can it retrieve the right evidence from long memory and code?

### Agent Token Savings

The SWE-QA comparison keeps the downstream agent the same and changes only the
initial context:

```text
Baseline: Claude Code + read-only tools + Task subagents + DeepSeek v4
Attemory: Claude Code + read-only tools + Task subagents + DeepSeek v4
          + one pre-run Attemory semantic-search hint
```

Attemory only gives it likely files and line ranges before the
agent loop starts.

| system | judge score | total tokens | main-agent tokens | subagent tokens | tool calls | cost estimate |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Baseline | 83.39 | 285.39M | 122.60M | 162.80M | 26,997 | $453.47 |
| Attemory hint | 83.17 | 160.39M | 86.60M | 73.79M | 17,340 | $296.68 |
| Change | -0.23 | **-43.8%** | **-29.4%** | **-54.7%** | **-35.8%** | **-34.6%** |

> `Cost estimate` is the `total_cost_usd` value emitted by Claude Code in the
> final `stream-json` result event. See [Claude Code documents](https://code.claude.com/docs/en/statusline)

The token drop comes from giving Claude Code a better starting point before it
begins repository exploration. The main agent still has normal read-only tools,
but it performs fewer broad search/read loops and launches fewer exploratory
subagent calls. See [the SWE-QA benchmark note](benchmarks/sweqa.md) for the
full per-repo breakdown, methodology, and reproduction commands.

### Retrieval Quality

Token savings only matter if recall stays high. Attemory reaches SOTA-class
results across long conversations, million-token memory, and multi-language
codebases. LongMemEval-M is especially important: its context is long enough
that few memory systems evaluate on it directly, while Attemory still retrieves
all labeled evidence messages in the top 50 for **92.55%** of answerable
queries.

These results come without benchmark-specific hacks: no query rewrite, no
summarization, no agent-driven exploration, and no external cloud services for
retrieval. Only the raw corpus and raw benchmark query are used to run the
benchmarks.

| Benchmark | What it tests | Context size | Attemory result |
| --- | --- | ---: | --- |
| [LongMemEval-S](benchmarks/LongMemEval.md) | memory retrieval, the split most memory systems evaluate | about 40 sessions / 115k tokens | **98.72% session Recall_any@5**, **92.77% session Recall_all@5**, **98.94% message Recall_all@50** |
| [LongMemEval-M](benchmarks/LongMemEval.md) | Million-token memory retrieval, a scale few memory systems attempt | about 500 sessions / 1.5M tokens / 5k messages | **94.89% session Recall_any@5**, **83.62% session Recall_all@5**, **92.55% message Recall_all@50** |
| [LoCoMo](benchmarks/LoCoMo.md) | End-to-end long-conversation QA | 10 long conversations / 1,540 QA items | **94.52% accuracy** with GPT-4.1-mini as answer model and GPT-4o-mini as judge |
| [Semble](benchmarks/semble.md) | Code retrieval | 63 repos / 19 languages | **0.9055 file-level NDCG@10** |

All benchmarks are **reproducible in a local environment**. See
[`benchmarks/`](benchmarks/) for detailed results and run instructions.

## Getting Started

### Install

Attemory supports Linux and macOS. Hardware acceleration is available on NVIDIA
CUDA and Apple Metal.

```bash
uv pip install attemory           # macOS Apple Silicon, includes Metal runtime
uv pip install "attemory[cpu]"    # Linux CPU

# Linux CUDA
uv pip install "attemory[cuda]" \
  --extra-index-url https://attemorysystem.github.io/Attemory/whl/cu126/
```

The same install targets work with `pip`:

```bash
pip install attemory
pip install "attemory[cpu]"
pip install "attemory[cuda]" \
  --extra-index-url https://attemorysystem.github.io/Attemory/whl/cu126/
```

On macOS Apple Silicon, `attemory` automatically installs the Metal runtime. On
Linux, choose `cpu` or a CUDA extra explicitly. Use `cuda-cu126` by default:

```bash
pip install "attemory[cuda]" \
  --extra-index-url https://attemorysystem.github.io/Attemory/whl/cu126/
```

If you are using a Blackwell GPU such as RTX 50 series, use `cuda-cu129` with
the CUDA 12.9 wheel index:

```bash
pip install "attemory[cuda-cu129]" \
  --extra-index-url https://attemorysystem.github.io/Attemory/whl/cu129/
```

Use `cuda-cu124` or `cuda-cu121` only when your NVIDIA driver is too old for
CUDA 12.6.

Start a local server:

```bash
attemory-server --small --backend gpu --port 9006
attemory-server --small --backend metal --port 9006
attemory-server --tiny --backend cpu --port 9006
```

Attemory has two usage levels. Use the API when you are building a retrieval
engine into your own application. Use Repository Search, through `atcode`, when
you want a ready-made repository understanding and search tool.

<a id="retrieval-engine-api"></a>
<details>
<summary><b>Retrieval Engine API</b></summary>

Use the Python API when you want Attemory as a general retrieval engine for
memory, documents, or application-specific corpora.

```bash
attemory-server --small --backend gpu --port 9006
```

```python
from attemory import AttemoryClient, MemoryInput

client = AttemoryClient(host="127.0.0.1", port=9006, session_id="weekly-diary")
client.create_session()

client.add_system(
    "Read the memory carefully and retrieve the evidence that answers the query."
)
client.add_memory(
    MemoryInput(
        id="diary-20",
        text="In the evening, I had dinner with Clara at a Japanese restaurant.",
    )
)

client.index_session()

results = client.search(
    "Who did I have dinner with at the Japanese restaurant?",
    top_k=3,
)

for result in results:
    print(result.id, result.text)
```

See [examples/weekly_diary.py](examples/weekly_diary.py) for a complete example
and [doc/usage.md](doc/usage.md) for the full API guide.

</details>

<a id="repository-search"></a>
<details>
<summary><b>Repository Search</b></summary>

Use `atcode` when you want to index a repository and ask natural-language code
questions.

Initialize and index a repository:

```bash
cd /path/to/repo
atcode init
atcode index
```

Search it:

```bash
atcode search "where is session restore implemented"
```

Example output:

```text
<semantic_search_results>
The following files and line ranges are semantic-search candidate evidence from the repository.

1. src/context/session/session_manager.cpp:467-528
2. src/context/kv/segment_kv_cache_commands.cpp:227-326
</semantic_search_results>
```

`atcode search` returns compact file and line evidence by default. Add
`--include-snippets` when you want source snippets in the output, or `--raw`
when you want the underlying ranked chunk view.

Use it from Claude Code:

```bash
claude plugin marketplace add AttemorySystem/attemory-claude-code
claude plugin install attemory-code@attemory
```

Then ask Claude Code to use `attemory-code search` for repository questions.
See [Repository Search usage](doc/repository-search.md#mcp-and-agent-usage) for the
full workflow.

</details>

## Documentation

| Topic | Link |
| --- | --- |
| Python and HTTP retrieval API | [doc/usage.md](doc/usage.md) |
| Repository Search CLI and Claude Code plugin usage | [doc/repository-search.md](doc/repository-search.md) |
| Benchmarks and reproduction | [benchmarks/](benchmarks/) |

## Build From Source

Developers building Attemory from source need a C++17 compiler, CMake 3.18 or
newer, and an attemory-core SDK.

Prebuilt `attemory-core-sdk` archives are published on the GitHub Releases
page. Download the SDK that matches your target runtime, then extract it to a
local directory:

```bash
mkdir -p 3rd/attemory-core-sdk
tar -xzf attemory-core-sdk.tar.gz -C 3rd/attemory-core-sdk --strip-components=1
```

Then pass the extracted SDK root to CMake with `ATMCORE_SDK`:

```bash
cmake -S . -B build \
  -DCMAKE_BUILD_TYPE=Release \
  -DATMCORE_SDK="$PWD/3rd/attemory-core-sdk"

cmake --build build --target attemory_server --parallel
```

Use the matching SDK archive for CUDA or macOS Metal builds, for example
`attemory-core-sdk-linux-cuda-cu126-...tar.gz` or
`attemory-core-sdk-macos-metal-...tar.gz`.

## Future Work

- [ ] MCP support for agent and tool integrations.
- [ ] Continued performance optimization for indexing, search, and native backends.
- [ ] Broader test coverage across APIs, packaging, persistence, and runtime
  variants.

## Acknowledgements

Attemory is built on the work of the [Qwen team](https://github.com/QwenLM) and
the [ggml/llama.cpp community](https://github.com/ggml-org/llama.cpp).

## Citation

If you use Attemory in research or benchmarks, please cite it as:

```bibtex
@software{attemory2026,
  title        = {Attemory: Attention-Native Memory Retrieval System},
  author       = {Lance Fang},
  year         = {2026},
  url          = {https://github.com/AttemorySystem/Attemory},
}
```

## License

Attemory is released under the MIT License. See [LICENSE](LICENSE).
