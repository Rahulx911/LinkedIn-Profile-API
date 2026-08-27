"""
Lists every rsc-action request captured in a HAR file, with its componentId
and response length, so the right one for a new section can be identified.

    python scripts/list_rsc_actions.py debug_output/linkedin.har
"""

import json
import re
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "debug_output/linkedin.har"
har = json.load(open(path))

for i, entry in enumerate(har["log"]["entries"]):
    url = entry["request"]["url"]
    if "rsc-action" not in url:
        continue
    m = re.search(r"componentId=([^&]+)", url)
    cid = m.group(1) if m else "(none)"
    m2 = re.search(r"sduiid=([^&]+)", url)
    sduiid = m2.group(1) if m2 else "?"
    resp_text = entry.get("response", {}).get("content", {}).get("text", "") or ""
    print(f"[{i}] componentId={cid}")
    print(f"     sduiid={sduiid}")
    print(f"     resp_len={len(resp_text)}")
