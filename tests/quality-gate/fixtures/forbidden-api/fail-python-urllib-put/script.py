#!/usr/bin/env python3
import urllib.request
urllib.request.urlopen(urllib.request.Request("https://api.github.com/repos/o/r/pulls/1/merge", method="PUT"))
