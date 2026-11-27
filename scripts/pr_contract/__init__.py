"""pr-contract: checks a pull request against the rules PC-1 to PC-9, TC-1 and TC-2.

Standard library only. PR text (title, body, branch, paths) is untrusted input: it is parsed
with line scanners, never passed to a process, and sanitized before it is printed. Every policy
input (configuration, manifest) is read from the BASE commit, never from the head being judged.
"""
