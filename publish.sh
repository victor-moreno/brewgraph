#!/bin/sh
# Create the public GitHub repo victor-moreno/brewgraph and push this folder.
# Run from this directory, outside the sandbox: ./publish.sh
set -e

cd "$(dirname "$0")"

# Needs an authenticated gh CLI; this logs you in if not already.
gh auth status >/dev/null 2>&1 || gh auth login

gh repo create victor-moreno/brewgraph --public --source=. --remote=origin --push

echo "Done: https://github.com/victor-moreno/brewgraph"
