#!/usr/bin/env python3
import subprocess
subprocess.run(["gh", "api", f"repos/{OWNER}/{REPO}/pulls/{N}/reviews", "-f", "event=APPROVE"], check=True)
