# Line-based scan for workflow-policy.sh (POSIX awk; no YAML parser). Run with -v file=PATH -v allowed="owner/repo ...".
# It reads only the text patterns of the workflow rules: top-level keys, `uses:`, `run:` (inline and block scalars),
# permissions, secrets, and a few forbidden words. The rules that depend on structure (WP-10 to WP-13, WP-16, and the `cache:`
# input of WP-14) are checked on the parsed form by workflow_policy.py. Findings are "path:line: WP-n: message"; no file
# content is printed. Every message carries the rule id:
#   top-level permissions too broad -> WP-1      paths filter -> WP-7        pull_request_target, workflow_run -> WP-5
#   not pinned / no version comment -> WP-3      secret reference -> WP-6    download piped to a shell -> WP-9
#   untrusted expression inside a run block -> WP-8 (any `${{` in a run value)
function report(rule, msg) { printf "%s:%d: %s: %s\n", file, FNR, rule, msg }
function is_allowed(repo,   i, n, list) {
  n = split(allowed, list, " ")
  for (i = 1; i <= n; i++) if (list[i] == repo) return 1
  return 0
}
FNR == 1 { top = ""; inrun = 0; runind = 0 }
{
  line = $0
  sub(/\r$/, "", line)
  match(line, /^ */)
  ind = RLENGTH
  rest = substr(line, ind + 1)
  if (rest == "") next
  if (inrun && ind <= runind) inrun = 0
  if (substr(rest, 1, 1) == "#") next
  lr = tolower(rest)

  if (ind == 0 && match(rest, /^[A-Za-z_][A-Za-z0-9_-]*:/)) {
    top = substr(rest, 1, RLENGTH - 1)
    if (top == "permissions") {
      v = substr(rest, 13)
      sub(/^[ ]+/, "", v)
      sub(/[ ]*#.*$/, "", v)
      if (v ~ /^["']?(write-all|read-all)["']?$/) report("WP-1", "top-level permissions too broad (use {} and grant read scopes per job)")
      else if (v != "{}") report("WP-1", "top-level permissions must be {} (grant read scopes per job)")
    }
  }
  if (ind > 0 && rest ~ /^permissions:[ ]*["']?(write-all|read-all)["']?([ ]|$)/) report("WP-1", "permissions write-all and read-all are not allowed (grant read scopes)")
  if (top == "on" && rest ~ /(^|[{, ])paths(-ignore)?:/) report("WP-7", "paths filter on a trigger (required jobs must always run)")

  if (rest ~ /pull_request_target/) report("WP-5", "pull_request_target is not allowed")
  if (rest ~ /(^|[^A-Za-z_])workflow_run([^A-Za-z_]|$)/) report("WP-5", "workflow_run is not allowed")
  s = lr
  while (match(s, /secrets\.[a-z0-9_]+/)) {
    if (substr(s, RSTART, RLENGTH) != "secrets.github_token") report("WP-6", "secret reference (only GITHUB_TOKEN is allowed in v1)")
    s = substr(s, RSTART + RLENGTH)
  }
  if (lr ~ /tojson\([ ]*secrets[ ]*\)/) report("WP-6", "toJSON(secrets) exposes every secret")
  if (lr ~ /(^|[^a-z0-9_.-])secrets[ ]*\[/) report("WP-6", "secrets[ indexes the secrets by a dynamic name")
  if (lr ~ /^secrets:/) report("WP-6", "secrets: passes secrets to a called workflow")
  if (rest ~ /(curl|wget)[^#]*\|[ ]*(sudo[ ]+)?(ba|da|z|k)?sh([ ]|$)/ ||
      rest ~ /(ba|da)?sh[ ]+-c[ ]+["']?\$\([ ]*(curl|wget)/) report("WP-9", "download piped to a shell")

  if (inrun) {
    if (rest ~ /\$\{\{/) report("WP-8", "${{ inside a run block (pass the value through env:)")
    next
  }

  r = rest
  dash = 0
  if (match(r, /^- +/)) { dash = RLENGTH; r = substr(r, RLENGTH + 1) }
  if (r !~ /^(run|name):/ && r ~ /(^|[{, ])(actions|attestations|checks|contents|deployments|discussions|id-token|issues|models|packages|pages|pull-requests|repository-projects|security-events|statuses):[ ]*["']?write([^A-Za-z0-9_-]|$)/) report("WP-2", "write permission (v1 grants read scopes only, including id-token)")
  if (r ~ /^uses:/) {
    v = r
    sub(/^uses:[ ]*/, "", v)
    cmt = ""
    if (match(v, /[ ]#/)) { cmt = substr(v, RSTART + 1); v = substr(v, 1, RSTART - 1) }
    gsub(/["' ]/, "", v)
    if (substr(v, 1, 2) == "./") next
    n = split(v, parts, "@")
    sha = (n == 2) ? parts[2] : ""
    if (n != 2 || length(sha) != 40 || sha !~ /^[0-9a-f]+$/) report("WP-3", "action not pinned to a full 40-character commit SHA")
    else if (cmt !~ /^#[ ]*v?[0-9]/) report("WP-3", "pinned action lacks a trailing version comment")
    # WP-14 names win over WP-4: an allowlist entry cannot admit them. The repository is owner/repo, without a path or ref.
    split(tolower(parts[1]), rp, "/")
    repo = rp[1] "/" rp[2]
    if (repo == "actions/cache" || repo == "actions/upload-artifact" || repo == "actions/download-artifact" || repo == "actions/github-script") report("WP-14", "cache, artifact, and github-script actions are not allowed in v1")
    else if (!is_allowed(repo)) report("WP-4", "action repository is not in allowed_actions")
  } else if (r ~ /^run:/) {
    v = r
    sub(/^run:[ ]*/, "", v)
    if (v ~ /^[|>][-+0-9]*([ ]+#.*)?$/) { inrun = 1; runind = ind + dash }
    else if (v ~ /\$\{\{/) report("WP-8", "${{ inside a run value (pass the value through env:)")
  }
}
