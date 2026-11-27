#!/usr/bin/env bash
set -euo pipefail
gh api -X PATCH "user/repository_invitations/$ID"
