"""
Dumps the full request (method, URL, headers, body) for one HAR entry by
index, so a new endpoint's exact shape can be inspected and replicated.

    python scripts/dump_har_entry.py debug_output/linkedin.har 90
"""

import json
import sys

path = sys.argv[1]
index = int(sys.argv[2])

har = json.load(open(path))
entry = har["log"]["entries"][index]
req = entry["request"]

print("METHOD:", req["method"])
print("URL:", req["url"])
print()
print("HEADERS:")
for h in req["headers"]:
    name = h["name"]
    if name.lower() in ("cookie",):
        continue  # never print cookies
    print(f"  {name}: {h['value'][:150]}")
print()
if req.get("postData"):
    print("POST BODY:")
    print(req["postData"].get("text", ""))
