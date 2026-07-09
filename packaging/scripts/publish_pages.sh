#!/usr/bin/env bash
set -euo pipefail

REPO_SSH="git@github.com:AttemorySystem/attemory.git"
PAGES_BRANCH="gh-pages"
COMMIT_MESSAGE="Update GitHub Pages landing page and explorer"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"
HOMEPAGE_DIR="${REPO_ROOT}/assets/homepage"
WORK_DIR="$(mktemp -d)"
PAGES_DIR="${WORK_DIR}/pages"

cleanup() {
  rm -rf "${WORK_DIR}"
}
trap cleanup EXIT

git clone --branch "${PAGES_BRANCH}" --single-branch "${REPO_SSH}" "${PAGES_DIR}"

rm -rf "${PAGES_DIR}/explorer"
mkdir -p "${PAGES_DIR}/assets"
mkdir -p "${PAGES_DIR}/explorer"

cp "${HOMEPAGE_DIR}/index.html" "${PAGES_DIR}/index.html"
rm -f "${PAGES_DIR}/assets/attemory_icon.png" \
  "${PAGES_DIR}/assets/attemory_logo.png" \
  "${PAGES_DIR}/assets/attemory_logo_orange.png" \
  "${PAGES_DIR}/assets/attemory_logo_compact.png" \
  "${PAGES_DIR}/assets/attemory_logo_transparent.png" \
  "${PAGES_DIR}/assets/attemory_mark_transparent.png"
cp "${HOMEPAGE_DIR}/assets/attemory_logo.png" "${PAGES_DIR}/assets/attemory_logo.png"
cp "${HOMEPAGE_DIR}/table-understanding.png" "${PAGES_DIR}/table-understanding.png"
cp "${HOMEPAGE_DIR}/root-cause.png" "${PAGES_DIR}/root-cause.png"
cp "${HOMEPAGE_DIR}/temporal-reasoning.png" "${PAGES_DIR}/temporal-reasoning.png"

cp "${HOMEPAGE_DIR}/explorer/index.html" "${PAGES_DIR}/explorer/index.html"
cp "${HOMEPAGE_DIR}/explorer/plaintext_heatmap.json" "${PAGES_DIR}/explorer/plaintext_heatmap.json"
cp "${HOMEPAGE_DIR}/explorer/table_heatmap.json" "${PAGES_DIR}/explorer/table_heatmap.json"
cp "${HOMEPAGE_DIR}/explorer/incident_heatmap.json" "${PAGES_DIR}/explorer/incident_heatmap.json"

git -C "${PAGES_DIR}" add -A index.html assets \
  table-understanding.png root-cause.png temporal-reasoning.png \
  explorer/index.html explorer/plaintext_heatmap.json explorer/table_heatmap.json \
  explorer/incident_heatmap.json explorer

if git -C "${PAGES_DIR}" diff --cached --quiet; then
  echo "No GitHub Pages changes to publish."
  exit 0
fi

git -C "${PAGES_DIR}" commit -m "${COMMIT_MESSAGE}"
git -C "${PAGES_DIR}" push origin "${PAGES_BRANCH}"

echo "Published:"
echo "  https://attemorysystem.github.io/attemory/"
echo "  https://attemorysystem.github.io/attemory/explorer/"
