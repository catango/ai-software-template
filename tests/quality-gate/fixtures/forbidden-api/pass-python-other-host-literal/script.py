#!/usr/bin/env python3
import urllib.request
urllib.request.urlopen(urllib.request.Request("https://example.invalid/artifact.tar.gz", method="GET"))
