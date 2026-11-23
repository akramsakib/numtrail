#!/usr/bin/env bash
# Create the GitHub repo and push this tree, in one step.
#
#   ./scripts/push.sh                     # repo name: numtrail
#   ./scripts/push.sh my-repo-name        # custom name
#   GH_USER=myuser ./scripts/push.sh      # explicit account
#
# Needs either the GitHub CLI (`gh auth login`) or an SSH key already added to
# GitHub. Nothing here touches the files themselves.
set -euo pipefail

REPO_NAME="${1:-numtrail}"
cd "$(dirname "$0")/.."

if [ ! -d .git ]; then
  echo "no git repo here — run: git init -b main && git add -A && git commit -m init"
  exit 1
fi

if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
  OWNER="$(gh api user --jq .login)"
  echo "→ creating github.com/$OWNER/$REPO_NAME and pushing"
  if gh repo view "$OWNER/$REPO_NAME" >/dev/null 2>&1; then
    echo "  repo exists, pushing to it instead"
    git remote remove origin 2>/dev/null || true
    git remote add origin "git@github.com:$OWNER/$REPO_NAME.git"
    git push -u origin main
  else
    gh repo create "$REPO_NAME" --public --source . --remote origin --push \
      --description "Phone attribution across 160 services: CLI + web UI, 15 automated probes"
  fi
  OWNER_SLUG="$OWNER"
else
  GH_USER="${GH_USER:?set GH_USER=your-github-username, or install/authenticate the gh CLI}"
  echo "→ pushing to git@github.com:$GH_USER/$REPO_NAME.git"
  echo "  (create the empty repo first: https://github.com/new — name it $REPO_NAME, no README)"
  git remote remove origin 2>/dev/null || true
  git remote add origin "git@github.com:$GH_USER/$REPO_NAME.git"
  git push -u origin main
  OWNER_SLUG="$GH_USER"
fi

SITE="https://$OWNER_SLUG.github.io/$REPO_NAME/"
echo
echo "pushed."
echo
echo "next, either:"
echo "  A. full stack, all 15 probes (recommended)"
echo "     dashboard.render.com → New → Blueprint → pick $REPO_NAME"
echo "     → you get a working URL with the API attached"
echo
echo "  B. GitHub Pages front end + worker API"
echo "     1. Settings → Pages → Source: GitHub Actions"
echo "     2. npx wrangler deploy numtrail/web/worker.js --name numtrail-api"
echo "     3. Settings → Secrets and variables → Actions → Variables"
echo "        NUMTRAIL_API_BASE = <the worker URL>"
echo "     4. Actions → 'deploy web UI to GitHub Pages' → Re-run all jobs"
echo "     → $SITE"
echo
echo "details: DEPLOY.md"
