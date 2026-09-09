"""Strict subprocess transport fixture, not proof of live Codex compatibility."""

import json
import pathlib
import sys
import time

from jsonschema import validate

root = pathlib.Path(__file__).parent / "fixtures/codex-0.153.4"
mode = sys.argv[1]
if mode == "malformed":
    print("this is not JSON", flush=True)
    time.sleep(0.5)
    sys.exit()
if mode == "partial":
    sys.stdout.write('{"id":')
    sys.stdout.flush()
    time.sleep(1)
    sys.exit()
if mode == "eof":
    sys.exit()
queued = []
for line in sys.stdin:
    request = json.loads(line)
    if "id" not in request:
        continue
    method = request["method"]
    if mode == "silent":
        time.sleep(2)
        continue
    if mode == "reverse":
        queued.append(request)
        if len(queued) == 2:
            for item in reversed(queued):
                print(
                    json.dumps({"id": item["id"], "result": {"method": item["method"]}}), flush=True
                )
        continue
    schemas = {
        "thread/start": "ThreadStartParams",
        "thread/resume": "ThreadResumeParams",
        "turn/start": "TurnStartParams",
    }
    try:
        validate(request["params"], json.loads((root / (schemas[method] + ".json")).read_text()))
    except Exception:
        print(
            json.dumps(
                {"id": request["id"], "error": {"code": -32602, "message": "Invalid params"}}
            ),
            flush=True,
        )
    else:
        print(json.dumps({"id": request["id"], "result": {"accepted": True}}), flush=True)
