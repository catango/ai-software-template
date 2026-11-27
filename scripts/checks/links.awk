# Prints "LINE<TAB>TARGET" for every Markdown link target in a file (POSIX awk).
# Skips fenced code blocks and inline code spans; handles [t](target "title"), ![i](target),
# and reference definitions "[id]: target".
{
  line = $0
  if (line ~ /^[ ]*(```|~~~)/) { fence = !fence; next }
  if (fence) next
  gsub(/`[^`]*`/, "", line)
  if (match(line, /^[ ]{0,3}\[[^]]+\]:[ ]+[^ ]+/)) {
    t = substr(line, RSTART, RLENGTH)
    sub(/^[ ]{0,3}\[[^]]+\]:[ ]+/, "", t)
    print FNR "\t" t
    next
  }
  while (match(line, /\]\([^)]*\)/)) {
    t = substr(line, RSTART + 2, RLENGTH - 3)
    line = substr(line, RSTART + RLENGTH)
    sub(/^[ ]+/, "", t)
    if (substr(t, 1, 1) == "<") { sub(/>.*$/, "", t); sub(/^</, "", t) }
    else sub(/[ ].*$/, "", t)
    if (t != "") print FNR "\t" t
  }
}
