"""20 independent authenticated HTTP users, fixed five-minute mixed read/write run."""
from concurrent.futures import ThreadPoolExecutor
import argparse
import csv
import http.cookiejar
import json
import math
import os
from pathlib import Path
import platform
import threading
import time
import urllib.parse
import urllib.request


def percentile(values, fraction):
    return sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)] if values else None


def run(root, base, duration=300):
    fixture = json.loads((root / "fixture.json").read_text())
    barrier = threading.Barrier(21)
    records, curve = [], []
    guard = threading.Lock()
    active = 0
    times = {}

    def user(index):
        nonlocal active
        identity = fixture["users"][index]
        jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        opener.open(base + "/accounts/login/", timeout=30).read()
        token = next(c.value for c in jar if c.name == "csrftoken")
        payload = urllib.parse.urlencode({"username": identity["username"], "password": fixture["password"], "csrfmiddlewaretoken": token}).encode()
        response = opener.open(urllib.request.Request(base + "/accounts/login/", data=payload), timeout=30)
        assert "/login" not in response.url, "Fixture login failed"
        response.read()
        owned = [t for t in fixture["tickets"] if identity["role"] == "admin" or t["student"] == index or t["worker"] == index]
        barrier.wait(timeout=120)
        sequence = 0
        while time.perf_counter() < times["end"]:
            ticket = owned[sequence % len(owned)]["id"]
            kind = sequence % 5
            path = "/tickets/" if kind == 0 else f"/tickets/{ticket}/" if kind in (1, 3) else (
                "/tickets/tasks/" if identity["role"] == "maintenance" else "/tickets/manage/" if identity["role"] == "admin" else "/accounts/profile/")
            data = None
            operation = "list" if kind == 0 else "detail" if kind in (1, 3) else "role_page"
            if kind == 4:
                path = f"/tickets/{ticket}/notes/"
                token = next(c.value for c in jar if c.name == "csrftoken")
                data = urllib.parse.urlencode({"text": f"验收进度说明 {index}-{sequence}", "csrfmiddlewaretoken": token}).encode()
                operation = "append_note"
            started = time.perf_counter()
            with guard: active += 1
            status, success = 0, False
            try:
                response = opener.open(urllib.request.Request(base + path, data=data), timeout=30)
                body = response.read().decode()
                status = response.status
                success = status == 200 and "/login" not in response.url
                if data is not None:
                    success = success and "说明已追加" in body
            except Exception as error:
                status = getattr(error, "code", 0)
            elapsed = time.perf_counter() - started
            with guard:
                active -= 1
                records.append({"start_s": round(started-times["start"], 4), "user": index,
                    "operation": operation, "status": status, "success": success, "seconds": round(elapsed, 6)})
            sequence += 1
            time.sleep(0.25)

    with ThreadPoolExecutor(max_workers=20) as pool:
        futures = [pool.submit(user, i) for i in range(20)]
        times["start"] = time.perf_counter()
        times["end"] = times["start"] + duration
        barrier.wait(timeout=120)
        # Begin measured interval after all twenty login sessions are ready.
        times["start"] = time.perf_counter(); times["end"] = times["start"] + duration
        while time.perf_counter() < times["end"]:
            with guard: curve.append({"second": round(time.perf_counter()-times["start"], 2), "active_requests": active, "virtual_users": 20, "completed": len(records)})
            time.sleep(1)
        for future in futures: future.result()
    root.mkdir(exist_ok=True)
    for name, rows in (("load-raw.csv", records), ("load-concurrency.csv", curve)):
        with (root / name).open("w", newline="", encoding="utf-8") as stream:
            writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    summary = {"platform": platform.platform(), "cpu": os.environ.get("PROCESSOR_IDENTIFIER", platform.processor()),
        "logical_cpus": os.cpu_count(), "duration_seconds": duration, "virtual_users": 20, "think_seconds": 0.25,
        "seed_users": 20, "seed_tickets": len(fixture["tickets"]), "requests": len(records),
        "unexpected_responses": sum(not r["success"] for r in records),
        "p95_seconds_all": percentile([r["seconds"] for r in records], .95),
        "p95_seconds_success": percentile([r["seconds"] for r in records if r["success"]], .95),
        "operations": {k: {"requests": sum(r["operation"]==k for r in records),
            "p95_seconds": percentile([r["seconds"] for r in records if r["operation"]==k], .95)}
            for k in {r["operation"] for r in records}}}
    summary["unexpected_ratio"] = summary["unexpected_responses"]/summary["requests"]
    summary["latency_requirement_met"] = summary["p95_seconds_all"] <= 2 and summary["p95_seconds_success"] <= 2
    (root / "load-summary.json").write_text(json.dumps(summary,indent=2),encoding="utf-8")
    print(json.dumps(summary,indent=2),flush=True)


if __name__ == "__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--root",type=Path,required=True);parser.add_argument("--base",default="http://127.0.0.1:8765")
    args=parser.parse_args();run(args.root,args.base)
