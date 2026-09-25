#!/usr/bin/env bash
# Push every RescueGrid branch to GitHub. Needs credentials once:  ~/.local/bin/gh auth login  (then: gh auth setup-git)
#   ./push_all.sh
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
export PATH="$HOME/.local/bin:$PATH"
git push -u origin legacy-main jayvant-adapters kenil-reasoning shresth-backend aditya-vision pranay-frontend
git push -f -u origin main            # main is replaced by the consolidated app; the previous main lives on as legacy-main
echo; git ls-remote --heads origin
