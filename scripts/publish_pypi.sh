#!/usr/bin/env bash
set -euo pipefail

VERSION="${1:-}"
if [[ -z "$VERSION" ]]; then
    echo "Usage: ./scripts/publish_pypi.sh <version>"
    echo "Example: ./scripts/publish_pypi.sh 1.0.1"
    exit 1
fi

WORKTREE="/tmp/nrev-lite-release"
REPO_ROOT="$(git rev-parse --show-toplevel)"

echo "=== Step 1: Create worktree from origin/prod ==="
git fetch origin prod
if [[ -d "$WORKTREE" ]]; then
    echo "Cleaning up existing worktree at $WORKTREE"
    git worktree remove "$WORKTREE" --force
fi
git worktree add "$WORKTREE" origin/prod

echo ""
echo "=== Step 2: Verify version in pyproject.toml ==="
TOML_VERSION=$(grep -m1 '^version' "$WORKTREE/pyproject.toml" | sed 's/.*"\(.*\)".*/\1/')
if [[ "$TOML_VERSION" != "$VERSION" ]]; then
    echo "ERROR: pyproject.toml has version \"$TOML_VERSION\", expected \"$VERSION\""
    echo "Bump the version on the prod branch first."
    git worktree remove "$WORKTREE" --force
    exit 1
fi
echo "Version matches: $VERSION"

echo ""
echo "=== Step 3: Build ==="
cd "$WORKTREE"
rm -rf dist/
python -m build

echo ""
echo "=== Step 4: Validate ==="
twine check dist/nrev_lite-"$VERSION"*

echo ""
echo "=== Step 5: Confirm artifacts ==="
echo "Built:"
ls -lh dist/
echo ""

WHL="dist/nrev_lite-${VERSION}-py3-none-any.whl"
if [[ ! -f "$WHL" ]]; then
    echo "ERROR: Expected $WHL not found"
    cd "$REPO_ROOT"
    git worktree remove "$WORKTREE" --force
    exit 1
fi

echo ""
read -p "Upload nrev-lite $VERSION to PyPI? (y/N) " CONFIRM
if [[ "$CONFIRM" != "y" && "$CONFIRM" != "Y" ]]; then
    echo "Aborted. Artifacts are at $WORKTREE/dist/"
    cd "$REPO_ROOT"
    exit 0
fi

echo ""
echo "=== Step 6: Upload to PyPI ==="
twine upload dist/nrev_lite-"$VERSION"*

echo ""
echo "=== Step 7: Cleanup ==="
cd "$REPO_ROOT"
git worktree remove "$WORKTREE" --force

echo ""
echo "Done. Verify at: https://pypi.org/project/nrev-lite/$VERSION/"
