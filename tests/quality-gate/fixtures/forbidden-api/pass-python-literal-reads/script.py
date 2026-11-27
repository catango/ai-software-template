#!/usr/bin/env python3
import subprocess
import urllib.request
subprocess.run(["gh", "api", f"repos/{OWNER}/{REPO}/pulls/{N}/reviews"], check=True)
subprocess.run(["gh", "api", "-X", "POST", f"repos/{OWNER}/{REPO}/issues/{N}/comments", "-f", "body=x"], check=True)
urllib.request.urlopen("https://api.github.com/repos/o/r/pulls/1")
urllib.request.urlopen(urllib.request.Request("https://api.github.com/repos/o/r/commits/abc/check-runs"))
