"""Claude 구성 파일에 rhino7 MCP 서버를 등록합니다. 기존 서버 항목은 유지하고,
JSON 형식이 깨져 있으면 서버 항목만 추려서 올바른 JSON 으로 다시 씁니다.
사용: python merge_config.py <python.exe 경로>"""
import glob
import json
import os
import re
import shutil
import sys
import time

py = sys.argv[1]
here = os.path.dirname(os.path.abspath(__file__))
server = os.path.join(here, "rhino_mcp_server.py")

paths = glob.glob(os.path.expandvars(r"%LOCALAPPDATA%\Claude-Data\*\claude_desktop_config.json"))
paths += glob.glob(os.path.expandvars(r"%LOCALAPPDATA%\Packages\Claude_*\LocalCache\Roaming\Claude\claude_desktop_config.json"))
paths += glob.glob(os.path.expandvars(r"%APPDATA%\Claude\claude_desktop_config.json"))
paths = sorted(set(paths))
print("config candidates:", paths)
if not paths:
    data_dirs = glob.glob(os.path.expandvars(r"%LOCALAPPDATA%\Claude-Data\*"))
    d = data_dirs[0] if data_dirs else os.path.expandvars(r"%APPDATA%\Claude")
    os.makedirs(d, exist_ok=True)
    paths = [os.path.join(d, "claude_desktop_config.json")]

for p in paths:
    raw = ""
    if os.path.exists(p):
        with open(p, encoding="utf-8-sig") as f:
            raw = f.read()
        shutil.copy(p, p + ".bak-" + time.strftime("%Y%m%d-%H%M%S"))
    print("--- original:", p)
    print(raw)

    try:
        data = json.loads(raw) if raw.strip() else {}
        if not isinstance(data, dict):
            data = {}
        servers = data.get("mcpServers") or {}
        print("--- parsed as valid JSON")
    except ValueError as e:
        print("--- invalid JSON (%s); recovering server entries" % e)
        data, servers = {}, {}
        dec = json.JSONDecoder()
        for m in re.finditer(r'"([^"]+)"\s*:\s*(\{)\s*"command"', raw):
            try:
                obj, _ = dec.raw_decode(raw, m.start(2))
                servers[m.group(1)] = obj
            except ValueError:
                pass
        print("    recovered:", list(servers))

    servers["rhino7"] = {"command": py, "args": [server]}
    data["mcpServers"] = servers
    out = json.dumps(data, indent=2, ensure_ascii=False)
    with open(p, "w", encoding="utf-8") as f:
        f.write(out + "\n")
    print("--- written:", p)
    print(out)
