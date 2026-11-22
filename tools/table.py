#!/usr/bin/env python3
"""The flat table of every session : one row per session, program, series and precision, with
the minimum and the median of its rounds (ns per frame). A series is a Faust binary (its role,
its commit), its generator (cpp, ocpp) and its Faust options.

    table.py [SESSIONS_DIR] [--out table/measures]

writes table/measures.json ({rows, versions, meta}), table/measures.csv, and docs/measures.json,
the copy the site reads. versions describes every version measured, by its name and commit : its
branch, its last commit (subject, date, full hash), its Faust options and what it tries to do, from
the provenance of the sessions, their --describe, and versions.json, which completes and overrides
them.
A session without results.json (an older protocol, see its NOTICE.md) has no rows.
"""
import argparse
import csv
import glob
import json
import os
import re
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
COLUMNS = ["session", "date", "machine", "cxx", "cxx_full", "version", "commit", "options", "generator",
           "precision", "program", "ns_min", "ns_median", "rounds"]


def short_machine(m):
    if m.startswith("Apple M1"):
        return "Mac M1"
    if "Ryzen" in m:
        return "rizen (Ryzen AI 9)"
    if "Xeon" in m:
        return "nuage (Xeon)"
    return m


def short_cxx(c):
    if c.startswith("Apple clang"):
        return "Apple clang " + re.search(r"version (\d+)", c).group(1)
    if c.startswith("g++"):
        m = re.search(r"\) (\d+)\.", c)
        return "g++ " + m.group(1) if m else c
    m = re.search(r"clang version (\d+)", c)
    return "clang " + m.group(1) if m else c


def note_version(info, name, commit, prov, options, description):
    """what a session says of a version : the most informative of its sessions"""
    e = info.setdefault(name + "|" + commit, {"version": name, "commit": commit, "branch": "", "subject": "",
                                              "date": "", "commit_full": "", "options": options, "description": ""})
    if prov.get("head_branch") and prov["head_branch"] != "HEAD":
        e["branch"] = prov["head_branch"]
    elif not e["branch"]:
        others = [b for b in prov.get("branches", []) if b != "HEAD"]
        e["branch"] = others[0] if others else ""
    for k, src in (("subject", "subject"), ("commit_full", "commit_full")):
        e[k] = e[k] or prov.get(src, "")
    e["date"] = e["date"] or prov.get("committed", "")[:10]
    e["description"] = description or e["description"]


def apply_versions_file(info, path):
    """versions.json : an entry matches a version by its name and the start of its commit"""
    if not os.path.exists(path):
        return
    for v in json.load(open(path))["versions"]:
        for e in info.values():
            if e["commit"].startswith(v["commit"]) and v.get("version", e["version"]) == e["version"]:
                for k in ("branch", "subject", "date", "description"):
                    if v.get(k):
                        e[k] = v[k]


def rows_of(path, info):
    D = json.load(open(path))
    if "meta" not in D:
        return []
    meta, times = D["meta"], D["times"]
    name = os.path.basename(os.path.dirname(path))
    commit = {k: v[1].rpartition(": ")[2] for k, v in meta["binaries"].items()}
    fopts = meta.get("faust_options", {})
    when = meta["date"][:16].replace("T", " ") + "Z"
    machine, cxx = short_machine(meta["machine"]), short_cxx(meta["cxx"])
    prov = meta.get("provenance", {}).get("binaries", {})
    for b in meta["binaries"]:
        vname = "master-dev" if b == "ref" else b
        note_version(info, vname, commit.get(b, ""), prov.get(b, {}), fopts.get(vname, ""),
                     meta.get("descriptions", {}).get(vname, ""))
    series = [("R", "master-dev", "cpp", "ref"), ("md-ocpp", "master-dev", "ocpp", "ref")]
    series += [(f"{n}-{g}", n, g, n) for n in meta["versions"] for g in ("cpp", "ocpp")]
    out = []
    for prog in meta["entries"]:
        tv = times.get(prog, {})
        for key, version, gen, b in series:
            for p in "fd":
                t = tv.get(f"{key}-{p}")
                if not t:
                    continue
                out.append({
                    "session": name, "date": when, "machine": machine, "cxx": cxx, "cxx_full": meta["cxx"],
                    "version": version, "commit": commit.get(b, ""), "options": fopts.get(version, ""),
                    "generator": gen, "precision": "float" if p == "f" else "double", "program": prog,
                    "ns_min": round(min(t), 4), "ns_median": round(statistics.median(t), 4), "rounds": len(t),
                })
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("sessions", nargs="?", default=os.path.join(ROOT, "sessions"))
    ap.add_argument("--out", default=os.path.join(ROOT, "table", "measures"))
    a = ap.parse_args()
    rows, info = [], {}
    for f in sorted(glob.glob(os.path.join(a.sessions, "*", "results.json"))):
        rows += rows_of(f, info)
    apply_versions_file(info, os.path.join(ROOT, "versions.json"))
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    json.dump({"rows": rows, "versions": info, "meta": [
        {"label": "table.py", "value": open(__file__).read(), "type": "code", "lang": "python"},
        {"label": "Input", "value": "faust-measures/sessions/*/results.json"},
    ]}, open(a.out + ".json", "w"), separators=(",", ":"))
    # the copy the site reads (GitHub Pages serves docs/)
    site = os.path.join(ROOT, "docs", "measures.json")
    json.dump({"rows": rows, "versions": info}, open(site, "w"), separators=(",", ":"))
    with open(a.out + ".csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} rows from {len({r['session'] for r in rows})} sessions : {a.out}.json, {a.out}.csv",
          file=sys.stderr)


if __name__ == "__main__":
    main()
