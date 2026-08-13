# Attemory Code on Semble

This benchmark evaluates Attemory on
[Semble](https://github.com/MinishLab/semble), a code-retrieval benchmark with
1,251 queries over 63 pinned repositories and 19 programming languages.

Semble provides a natural-language or symbol query and one or more relevant
source files. Attemory retrieves code chunks, globally reranks candidates from
multiple segments, fuses chunk ranks into a file ranking, and reports
file-level NDCG@10.

The benchmark has two deliberately separate stages:

1. `benchmarks.baselines.attemory_index` delegates repository indexing to
   `attemory code index`.
2. `benchmarks.baselines.attemory` only restores those indexes, searches them,
   and computes the metric.

The evaluation script never creates, updates, or rebuilds an index.

## Prepare Semble

Prepare the pinned upstream checkout:

```bash
cd benchmarks
./prepare_bench.sh semble
cd semble

export ATTEMORY_ROOT="$(realpath ../..)"
export PYTHONPATH="${ATTEMORY_ROOT}/python"
```

The 63 pinned repositories are stored by Semble under:

```text
~/.cache/semble-bench/
```

The benchmark uses each repository's `benchmark_root` from Semble's
`benchmarks/repos.json`, rather than indexing the complete checkout
unconditionally. For example:

```text
click -> ~/.cache/semble-bench/click/src/click
redis -> ~/.cache/semble-bench/redis/src
zig   -> ~/.cache/semble-bench/zig/lib/std
```

The scripts verify each checkout's Git revision against Semble's pinned
revision before indexing or evaluation.

## Start Attemory

Start one Attemory server separately. The published run used the medium tier:

```bash
attemory-server \
  --medium \
  --backend gpu \
  --port 9006
```

The benchmark scripts do not start or stop the server.

Set another server address when needed:

```bash
export ATTEMORY_HOST=127.0.0.1
export ATTEMORY_PORT=9006
```

## Inspect the Index Plan

Run an inexpensive scan before building KV:

```bash
python -m benchmarks.baselines.attemory_index --dry-run
```

Run one repository:

```bash
python -m benchmarks.baselines.attemory_index \
  --repo click \
  --dry-run
```

The default `compatible` profile preserves the source-file scope used by the
published Attemory/Semble run. With the current pinned repositories it scans
approximately:

```text
9,545 files
94,731 chunks
```

The earlier benchmark-owned chunker produced 102,199 chunks from the same
files. The lower count is expected: `attemory code` starts with 30 lines and
extends forward to a nearby blank line, producing chunks of roughly 30-40
lines.

To evaluate the normal `attemory code` include defaults instead, use:

```bash
python -m benchmarks.baselines.attemory_index \
  --profile default \
  --dry-run
```

That profile also includes supported documentation and configuration files. It
currently scans approximately 9,878 files and 96,116 chunks, so its result must
be reported separately from the compatibility run.

## Build Indexes

Build a smoke-test index:

```bash
python -m benchmarks.baselines.attemory_index \
  --repo click
```

Build all 63 indexes:

```bash
python -m benchmarks.baselines.attemory_index
```

For each Semble repository, the index script:

1. writes a deterministic `.attemory/config.toml` under its benchmark root
2. assigns a revision-scoped session id:

   ```text
   attemory-semble-atcode-{repo}-{revision12}
   ```

3. invokes the real user-facing command:

   ```text
   python -m attemory code index
   ```

4. reads `.attemory/index.json` written by Repository Search
5. records index time, files, chunks, tokens, and segments in:

   ```text
   ~/.cache/attemory-semble-atcode/index-manifest.json
   ```

No benchmark code scans files, splits chunks, calls `add_memory`, or chooses
segment boundaries.

If indexing was interrupted after the server received memories, resume
explicitly:

```bash
python -m benchmarks.baselines.attemory_index --resume
```

Rebuilding is also explicit:

```bash
python -m benchmarks.baselines.attemory_index --reindex
```

An existing index is otherwise reused. A mismatched `.attemory/config.toml`
causes an error instead of an implicit rebuild.

## Run the Evaluation

Evaluate the click smoke-test index:

```bash
python -m benchmarks.baselines.attemory \
  --repo click \
  --output /tmp/attemory-semble-click.json
```

Run the full benchmark:

```bash
python -m benchmarks.baselines.attemory
```

The default output is:

```text
benchmarks/results/semble/attemory-code.json
```

The output is written after every completed repository. Running the same
command again resumes from existing per-repository results. Resume is rejected
if search parameters such as `search_top_k` or file aggregation differ.

The evaluator also compares the completed repository set with the published
result in `benchmarks/results/semble/attemory.json` and reports the overall and
per-repository NDCG deltas.

Results also report how many queries returned candidates from multiple
segments and the total number of oneshot rerank passes.

Useful options:

```text
--repo NAME                 evaluate one or more repositories
--language NAME             evaluate one or more languages
--search-top-k 20           chunk candidates retained before file fusion
--top-k 10                  files used for NDCG@10
--latency-runs 1            repeated search measurements per query
--file-aggregation sum      chunk-to-file fusion mode
--no-oneshot-rerank         disable multi-segment global reranking
--verbose                   print per-query targets and top files
```

## Index Structure

`attemory code` stores one structured memory stream per repository:

```text
system prompt
file header for path/to/file.py
raw code chunk for path/to/file.py
raw code chunk for path/to/file.py
file header for path/to/other.py
...
```

File headers are context memories without ids:

```text
// the following code come from path/to/file.py
```

Code chunks contain only their raw source lines. Stable chunk ids and
file/range/language metadata are stored in:

```text
.attemory/chunks.jsonl
```

The persistent session and segment KV are built and saved entirely by
`attemory code index`.

## Search Protocol

The search protocol remains aligned with the published `0.9055` run so the new
score primarily measures the effect of the Repository Search index.

Each Semble query is sent as:

```text
Query: {query}
```

with a fixed code-navigation query context that prefers concrete definitions,
implementations, declarations, and exports over weaker mentions, wrappers,
tests, examples, generated files, or documentation.

The initial persistent-session call retains 20 chunk candidates from each
segment. If results come from multiple segments, the evaluator:

1. selects only returned code chunks
2. restores them in original `.attemory/chunks.jsonl` order
3. inserts the same no-id file header before each file
4. calls `oneshot_search` for a global ranking
5. repeats only if the temporary result still spans multiple segments, up to
   eight passes

The benchmark question and fixed query context are reused unchanged during
oneshot reranking.

## Chunk to File Ranking

Semble is evaluated primarily at file level. For each retained chunk at
1-based rank `r`:

```text
score[file] += 1 / log2(r + 1)
```

Multiple highly ranked chunks therefore reinforce the same file. Files are
sorted by:

```text
(-score, first_chunk_rank, file_path)
```

The top 10 files are evaluated with binary NDCG@10. The final score is the
macro average of the 63 per-repository means, matching Semble's reporting
convention.

## Published Reference

The previous benchmark-owned index implementation produced:

```text
Average file-level NDCG@10: 0.9055
```

Its result remains available at:

```text
benchmarks/results/semble/attemory.json
```

The new `attemory code` result must be stored separately until its full run has
been reviewed.
