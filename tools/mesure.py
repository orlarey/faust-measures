#!/usr/bin/env python3
r"""mesure.py : versions of the Faust compiler measured against a reference (SPEC.md ; first
written in faust-migration, LA-MESURE-CONTRE-MASTER-DEV.md).

One reference per precision, R_f and R_d : the reference binary (named by --ref-name, master-dev
by default) in cpp, default options, compiled by the C++ compiler with --ref-flags (-O3
-ffast-math -march=native by default). Every version, in cpp and ocpp, is compiled with
--version-flags (by default the same as the reference) or, under --contract, under the execution
contract of the certification branch (-fno-fast-math -ffp-contract=fast -fno-math-errno, the
denormals flushed to zero at start), and divided by the reference of its precision ; the
reference in ocpp is given for information, against the same reference.

  mesure.py run --ref <faust> --version NAME=<faust> [--version NAME=<faust> ...] --out DIR
      [--entries basket/entries.txt] [--faust-root DIR] [--cxx clang++] [--rounds 9]
      [--configs cpp-f,cpp-d,ocpp-f,ocpp-d] [--no-info] [--cxx-times] [--jobs 4]
      [--allow-modified] [--archive NAME [--machine NAME]] [--levers LEVER,...]
      [--faust-options NAME="<options>" ...] [--describe NAME="<text>" ...]
    generates the code of every program of the basket for the reference, master-dev ocpp
    and every version of the branch, compiles it with the harness cpu_arch.cpp, then runs
    the executables program after program, 9 rounds, the versions of a program in an order
    shifted by one at each round ; writes DIR/results.json (every round kept),
    DIR/report.txt and the graphs DIR/panier-<version>.png (not archived). --cxx-times also measures the
    C++ compile time (CPU, the minimum of 3). results.json records the provenance of the
    measure : every binary (its commit, tree hash, branches, hash), the sources and the
    libraries (commit, local changes), the basket (the hash of every program), the hash of
    every generated code, the tool, the host. A binary built with local changes (-modified)
    is refused, unless --allow-modified. --archive NAME archives the measure at the end.
    --levers is the diagnostic of -ffast-math : master-dev and the branch are also compiled
    with each lever alone over the execution contract (contract, reassoc, nsz, recip,
    finite, approx, fastmath), and the report gives every time / R for every lever.
    --faust-options NAME="-fam ..." gives the options of faust for one version (none by
    default) ; they are recorded in results.json and shown by the report.
    --describe NAME="..." says in a sentence what a version tries to do (master-dev for the
    reference) ; recorded in results.json, shown on the site (see also versions.json).

  mesure.py report DIR [--median]
    the report again, from DIR/results.json.

  mesure.py plot DIR [--median]
    the graphs again, from DIR/results.json : for every version of the branch, one panel
    per precision, every time / R of its precision on a logarithmic axis ; hollow circles
    for master-dev, filled squares for the branch, blue for cpp, red for ocpp.

  mesure.py faust-times --ref <faust> --version NAME=<faust> [...] --out DIR
      [--faust-root DIR] [--corpus FILE] [--runs 3] [--jobs 1] [--allow-modified]
      [--archive NAME [--machine NAME]]
    the time of faust itself (CPU, user + system, the minimum of 3) over the corpus (the
    examples and the impulse tests by default), the branch in cpp and ocpp against
    master-dev in cpp of the same precision ; the same provenance as run.

  mesure.py provenance DIR --ref <faust> --version NAME=<faust> [...] --faust-root DIR
      [--entries FILE]
    the provenance added afterwards to a measure made without it, from binaries and trees
    still in place ; marked reconstructed, with its date.

  mesure.py archive DIR --name NAME [--machine NAME] [--note TEXT]
    copies the measure into sessions/<date>-<machine>-<C++ compiler>-<name>/ (results.json,
    the reports ; not the graphs) and appends its lines to sessions/INDEX.tsv ; a directory
    already under sessions/ is indexed in place. An archived measure is never rewritten.

  mesure.py history [--key KEY] [--machine TEXT] [--version TEXT] [--type TYPE]
      [--plot FILE]
    the archived measures, grouped by comparison key (the reference commit, its C++
    compiler and options, the machine, the basket) : only measures of the same key compare.

The report : for each configuration, the time of each version divided by R of its precision
(the minimum of the rounds) ; a case marked (=) has the code of the previous version, and
the rounds of both are pooled ; the geometric mean ; the cases above 1.05 ; the noise, the
relative gap between the two best rounds of every measure (median, 90th percentile).
"""
import argparse
import csv
import datetime
import hashlib
import json
import math
import os
import platform
import re
import resource
import shlex
import shutil
import statistics
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
MESURES = os.path.join(os.path.dirname(HERE), "sessions")

REF_FLAGS = "-O3 -ffast-math -march=native"
BRANCH_FLAGS = "-O3 -march=native -fno-fast-math -ffp-contract=fast -fno-math-errno"
FTZ_H = """// flush-to-zero and denormals-are-zero from the start of the program, as -ffast-math does
// at link time (crtfastmath.o) and -mdaz-ftz (clang 18 lacks the option) : the execution
// contract of the branch. x86 : FTZ (bit 15) and DAZ (bit 6) of MXCSR ; arm64 : FZ (bit 24)
// of FPCR, which flushes both the inputs and the results
#if defined(__x86_64__) || defined(__i386__)
#include <xmmintrin.h>
static const int faust_ftz_daz_init = (_mm_setcsr(_mm_getcsr() | 0x8040), 0);
#elif defined(__aarch64__)
#include <cstdint>
static int faust_ftz_daz_set()
{
    uint64_t fpcr;
    __asm__ __volatile__("mrs %0, fpcr" : "=r"(fpcr));
    __asm__ __volatile__("msr fpcr, %0" : : "r"(fpcr | (uint64_t(1) << 24)));
    return 0;
}
static const int faust_ftz_daz_init = faust_ftz_daz_set();
#endif
"""
PRECISION = {"f": "-single", "d": "-double"}
# the levers of -ffast-math, one at a time over the execution contract (the diagnostic of
# --levers) ; contract : the contract alone ; fastmath : -ffast-math whole
LEVERS = {
    "contract": [],
    "reassoc": ["-fassociative-math", "-fno-signed-zeros", "-fno-trapping-math"],
    "nsz": ["-fno-signed-zeros"],
    "recip": ["-freciprocal-math"],
    "finite": ["-ffinite-math-only"],
    "approx": ["-fapprox-func"],
    "fastmath": None,
}


# ----------------------------------------------------------------------------------------
# the variants

def variants(args):
    """name -> (faust binary, lang, precision letter, side) ; R-f, R-d the references,
    md-ocpp-f, md-ocpp-d the information, <version>-<lang>-<p> the branch ; with --levers,
    md-<lang>-<p>~<lever> and <version>-<lang>-<p>~<lever>, compiled with that lever alone"""
    configs = args.configs.split(",")
    precs = sorted({c.split("-")[1] for c in configs})
    V = {}
    for p in precs:
        V[f"R-{p}"] = (args.ref, "cpp", p, "ref")
        if not args.no_info and any(c == f"ocpp-{p}" for c in configs):
            V[f"md-ocpp-{p}"] = (args.ref, "ocpp", p, "ref")
    for name, binary in args.versions:
        for c in configs:
            lang, p = c.split("-")
            V[f"{name}-{c}"] = (binary, lang, p, "branch")
    for lever in args.levers.split(",") if args.levers else []:
        for c in configs:
            lang, p = c.split("-")
            # master-dev under -ffast-math and the branch under its contract are measured already
            if lever != "fastmath":
                V[f"md-{c}~{lever}"] = (args.ref, lang, p, f"lever:{lever}")
            if lever != "contract":
                for name, binary in args.versions:
                    V[f"{name}-{c}~{lever}"] = (binary, lang, p, f"lever:{lever}")
    return V


def lever_flags(lever, ftz):
    if LEVERS[lever] is None:
        return REF_FLAGS.split()
    return BRANCH_FLAGS.split() + LEVERS[lever] + ["-include", ftz]


def parse_versions(items):
    out = []
    for it in items:
        name, _, path = it.partition("=")
        if not name or not path or "-" in name:
            sys.exit(f"--version NAME=<faust> (a name without '-') : {it}")
        out.append((name, os.path.abspath(path)))
    return out


def parse_faust_options(items, versions):
    """--faust-options NAME="<options>" : options of faust for one version of the branch"""
    names, out = {n for n, _ in versions}, {}
    for it in items:
        name, _, opts = it.partition("=")
        if name not in names:
            sys.exit(f"--faust-options {it} : no version named {name}")
        out[name] = shlex.split(opts)
    return out


def parse_descriptions(items, versions, ref_name):
    """--describe NAME="<text>" : what a version tries to do, the reference by its name"""
    names, out = {n for n, _ in versions} | {ref_name}, {}
    for it in items:
        name, _, text = it.partition("=")
        if name not in names or not text.strip():
            sys.exit(f"--describe {it} : NAME=<text>, NAME a version or the reference ({ref_name})")
        out[name] = text.strip()
    return out


def version_flags(args):
    """the C++ options of the versions : those of the reference by default ; under --contract,
    the execution contract of the certification branch (no -ffast-math, ftz.h included)"""
    if args.contract and args.version_flags:
        sys.exit("--contract and --version-flags : one or the other")
    if args.contract:
        return BRANCH_FLAGS
    return args.version_flags or args.ref_flags


def faust_options(v, fopts):
    """the options of faust for variant v : those of its version, none for master-dev"""
    return [] if v.startswith(("R-", "md-")) else fopts.get(v.split("-")[0], [])


def entries_of(path):
    E = []
    for line in open(path):
        line = line.strip()
        if line and not line.startswith("#"):
            kind, _, rest = line.partition(":")
            E.append(rest if kind == "prog" else line)
    return E


def short(entry):
    return os.path.basename(entry)[:-4] if entry.endswith(".dsp") else entry.replace("/", "_")


def version_line(binary):
    r = subprocess.run([binary, "--version"], capture_output=True, text=True)
    lines = [l.strip() for l in r.stdout.splitlines() if "Version" in l or "commit" in l]
    return " ; ".join(lines)


def machine():
    try:
        for l in open("/proc/cpuinfo"):
            if l.startswith("model name"):
                return l.split(":", 1)[1].strip() + f", {os.cpu_count()} cpus"
    except OSError:
        pass
    r = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True)
    if r.returncode == 0 and r.stdout.strip():
        return f"{r.stdout.strip()}, {os.cpu_count()} cpus"
    return f"{os.uname().machine}, {os.cpu_count()} cpus"


def cxx_version(cxx):
    r = subprocess.run([cxx, "--version"], capture_output=True, text=True)
    return r.stdout.splitlines()[0] if r.stdout else cxx


def cpu_time(cmd, timeout):
    """the CPU time (user + system) of one command run alone, or None if it fails"""
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    if r.returncode != 0:
        return None
    return (after.ru_utime - before.ru_utime) + (after.ru_stime - before.ru_stime)


# ----------------------------------------------------------------------------------------
# provenance

def git(cwd, *args):
    """the output of a git command run in cwd, or None"""
    try:
        r = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, env={**os.environ, "LC_ALL": "C"})
    except OSError:
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def binary_provenance(binary):
    """the commit a faust binary declares (faust --version) and what the git tree around the
    binary says of it : the full SHA, the tree hash (the same after a rebase or an amended
    message when the content is the same), the subject, the branches that contain it"""
    line = version_line(binary)
    m = re.search(r"Source commit: ([0-9a-f]+)(-modified)?", line)
    P = {"path": binary, "version": line, "sha256": sha256_file(binary),
         "commit": m.group(1) if m else None, "modified": bool(m and m.group(2))}
    top = git(os.path.dirname(binary), "rev-parse", "--show-toplevel")
    full = git(top, "rev-parse", "--verify", "-q", P["commit"] + "^{commit}") if top and P["commit"] else None
    if full:
        P["repo"] = top
        P["commit_full"] = full
        P["tree"] = git(top, "rev-parse", full + "^{tree}")
        P["subject"] = git(top, "log", "-1", "--format=%s", full)
        P["committed"] = git(top, "log", "-1", "--format=%cI", full)
        P["head_branch"] = git(top, "rev-parse", "--abbrev-ref", "HEAD") if git(top, "rev-parse", "HEAD") == full else None
        branches = git(top, "branch", "-a", "--contains", full, "--format=%(refname:short)") or ""
        P["branches"] = [b for b in branches.splitlines() if b and not b.startswith("(")]
    return P


def tree_provenance(path, *pathspec):
    """a git tree : its commit, tree hash, branch, and the local changes (in pathspec)"""
    top = git(path, "rev-parse", "--show-toplevel")
    if not top:
        return {"path": path}
    changes = (git(top, "status", "--porcelain", "--untracked-files=no", "--", *pathspec) or "").splitlines()
    return {"path": path, "commit": git(top, "rev-parse", "HEAD"), "tree": git(top, "rev-parse", "HEAD^{tree}"),
            "subject": git(top, "log", "-1", "--format=%s"), "branch": git(top, "rev-parse", "--abbrev-ref", "HEAD"),
            "changes": changes[:50], "changed": len(changes)}


def host_provenance():
    H = {"hostname": platform.node(), "system": platform.platform(), "cpu": machine(), "load": os.getloadavg()}
    if hasattr(os, "sched_getaffinity"):
        H["affinity"] = sorted(os.sched_getaffinity(0))
    try:
        H["governor"] = open("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor").read().strip()
    except OSError:
        pass
    return H


def provenance(ref, versions, root, entries=None):
    """everything a measure needs to be found and judged again : the binaries, the sources and
    libraries they compiled, the basket, the tool, the host, the command"""
    P = {"binaries": {"ref": binary_provenance(ref), **{n: binary_provenance(b) for n, b in versions}},
         "sources": tree_provenance(root, "examples", "tests"),
         "libraries": tree_provenance(os.path.join(root, "libraries")),
         "tool": tree_provenance(HERE, "tools", "basket"),
         "host": host_provenance(),
         "command": sys.argv}
    if entries:
        P["basket"] = {"file": os.path.abspath(entries), "sha256": sha256_file(entries),
                       "programs": {short(e): sha256_file(os.path.join(root, e)) for e in entries_of(entries)
                                    if os.path.exists(os.path.join(root, e))}}
    return P


def check_modified(P, allowed):
    """a binary built with local changes belongs to no commit : refused unless allowed"""
    bad = [f"{k} ({p['version']})" for k, p in P["binaries"].items() if p["modified"] or not p["commit"]]
    if bad and not allowed:
        sys.exit("binaries with local changes or without a commit, refused (--allow-modified for a working "
                 "measure) : " + ", ".join(bad))
    P["allow_modified"] = bool(bad)


def measure_kind(D):
    return "panier" if "configs" in D["meta"] else "faust-times"


def comparison_key(D):
    """two measures compare only under the same key : the reference (its commit, its C++
    compiler and options), the machine, the programs measured ; with a provenance, the
    programs are their sources and the libraries they import (a measure without provenance
    knows only their names)"""
    meta, kind = D["meta"], measure_kind(D)
    ref = re.search(r"Source commit: (\S+)", meta["binaries"]["ref"][1])
    parts = [kind, ref.group(1) if ref else "?", meta["machine"]]
    if kind == "panier":
        parts += [meta["cxx"], meta["flags"]["ref"], ",".join(sorted(meta["entries"]))]
    else:
        parts += [",".join(sorted(D["times"]))]
    prov = meta.get("provenance")
    if prov and prov.get("libraries", {}).get("commit"):
        programs = prov.get("basket", {}).get("programs") or prov.get("corpus") or {}
        parts += ["libraries " + prov["libraries"]["commit"],
                  "sources " + hashlib.sha256(json.dumps(programs, sort_keys=True).encode()).hexdigest()]
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:10]


# ----------------------------------------------------------------------------------------
# run

def code_body(path):
    """the generated code without its header lines and its comments : two versions with the
    same body compute the same thing in the same order"""
    out = []
    for l in open(path, errors="replace"):
        if "Code generated with Faust" in l or "ompilation options" in l or "compile_options" in l:
            continue
        out.append(re.sub(r"//.*", "", l).rstrip())
    return [l for l in out if l]


def cmd_run(args):
    args.versions = parse_versions(args.version)
    unknown = [l for l in (args.levers.split(",") if args.levers else []) if l not in LEVERS]
    if unknown:
        sys.exit(f"unknown levers : {', '.join(unknown)} (among {', '.join(LEVERS)})")
    root = os.path.abspath(args.faust_root)
    lib = os.path.join(root, "libraries")
    arch = os.path.abspath(args.arch) if args.arch else os.path.join(lib, "arch", "cpu_arch.cpp")
    out = os.path.abspath(args.out)
    os.makedirs(out, exist_ok=True)
    ftz = os.path.join(out, "ftz.h")
    open(ftz, "w").write(FTZ_H)
    V = variants(args)
    E = entries_of(args.entries)
    fopts = parse_faust_options(args.faust_options, args.versions)
    prov = provenance(args.ref, args.versions, root, args.entries)
    check_modified(prov, args.allow_modified)
    vflags = version_flags(args)
    flags = {"ref": shlex.split(args.ref_flags),
             "branch": shlex.split(vflags) + (["-include", ftz] if args.contract else []),
             **{f"lever:{l}": lever_flags(l, ftz) for l in LEVERS}}

    def build(e, v):
        binary, lang, p, side = V[v]
        cpp = os.path.join(out, f"{short(e)}.{v}.cpp")
        exe = os.path.join(out, f"{short(e)}.{v}")
        r = subprocess.run([binary, *faust_options(v, fopts), "-lang", lang, PRECISION[p], "-I", lib,
                            "-I", os.path.join(lib, "dx7"),
                            "-a", arch, os.path.join(root, e), "-o", cpp], capture_output=True, text=True,
                           timeout=900)
        if r.returncode != 0:
            return f"faust : {r.stderr.strip()[:120]}"
        # the directory of the .dsp too : the headers that stand beside it (instrument.h of faust-stk)
        r = subprocess.run([args.cxx, *flags[side], "-std=c++17", "-I", os.path.join(root, "architecture"),
                            "-I", os.path.dirname(os.path.join(root, e)), cpp,
                            "-o", exe], capture_output=True, text=True, timeout=900)
        return None if r.returncode == 0 else f"{args.cxx} : {r.stderr.strip()[:120]}"

    with ThreadPoolExecutor(args.jobs) as ex:
        futs = {(e, v): ex.submit(build, e, v) for e in E for v in V}
    failed = {f"{short(e)} {v}": f.result() for (e, v), f in futs.items() if f.result()}
    for k, msg in failed.items():
        print(f"not built : {k} : {msg}", file=sys.stderr)
    ok = [e for e in E if not any(f"{short(e)} {v}" in failed for v in V)]

    # the versions of the branch with the code of the previous one, per program and config
    same = {}
    names = [n for n, _ in args.versions]
    for e in ok:
        for c in args.configs.split(","):
            for a, b in zip(names, names[1:]):
                pa = os.path.join(out, f"{short(e)}.{a}-{c}.cpp")
                pb = os.path.join(out, f"{short(e)}.{b}-{c}.cpp")
                same[f"{short(e)} {b}-{c}"] = code_body(pa) == code_body(pb)
    # the code of every variant, headers and comments aside : the same hash in two measures
    # is the same code
    prov["code"] = {f"{short(e)} {v}": hashlib.sha256(
        "\n".join(code_body(os.path.join(out, f"{short(e)}.{v}.cpp"))).encode()).hexdigest()
        for e in ok for v in V}

    cxxtimes = {}
    if args.cxx_times:
        # alone, the minimum of 3 : the CPU time of clang on the generated code
        for e in ok:
            for v in V:
                side = V[v][3]
                cpp = os.path.join(out, f"{short(e)}.{v}.cpp")
                ts = [cpu_time([args.cxx, *flags[side], "-std=c++17", "-I", os.path.join(root, "architecture"),
                                "-I", os.path.dirname(os.path.join(root, e)),
                                "-c", cpp, "-o", os.devnull], 900) for _ in range(3)]
                ts = [t for t in ts if t is not None]
                if ts:
                    cxxtimes[f"{short(e)} {v}"] = min(ts)
            print(f"{short(e)} : C++ compile times done", file=sys.stderr, flush=True)

    meta = {
        "date": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "machine": machine(),
        "cxx": cxx_version(args.cxx),
        "ref_name": args.ref_name,
        "flags": {"ref": args.ref_flags, "branch": vflags + (" + ftz.h" if args.contract else ""),
                  **{f"lever:{l}": " ".join(lever_flags(l, "ftz.h")) for l in
                     (args.levers.split(",") if args.levers else [])}},
        "levers": args.levers.split(",") if args.levers else [],
        "faust_options": {n: " ".join(o) for n, o in fopts.items()},
        "descriptions": parse_descriptions(args.describe, args.versions, args.ref_name),
        "arch": [arch, sha256_file(arch) if os.path.exists(arch) else None],
        "rounds": args.rounds,
        "configs": args.configs.split(","),
        "versions": names,
        "binaries": {"ref": [args.ref, version_line(args.ref)],
                     **{n: [b, version_line(b)] for n, b in args.versions}},
        "entries": [short(e) for e in ok],
        "failed": failed,
        "provenance": prov,
    }
    times = {short(e): {v: [] for v in V} for e in ok}
    finite = {}
    path = os.path.join(out, "results.json")

    def dump():
        tmp = path + ".tmp"
        json.dump({"meta": meta, "variants": {v: list(V[v]) for v in V}, "same": same, "times": times,
                   "nonfinite": finite, "cxx_times": cxxtimes}, open(tmp, "w"), indent=1)
        os.replace(tmp, path)

    vs = list(V)
    for e in ok:
        for r in range(args.rounds):
            for v in vs[r % len(vs):] + vs[:r % len(vs)]:
                res = subprocess.run([os.path.join(out, f"{short(e)}.{v}")], capture_output=True, text=True,
                                     timeout=600)
                try:
                    times[short(e)][v].append(float(res.stdout.split()[0]))
                except (IndexError, ValueError):
                    continue
                if "finite=0" in res.stdout:
                    finite[f"{short(e)} {v}"] = True
        dump()
        print(f"{short(e)} done", file=sys.stderr, flush=True)
    prov["host"]["load_end"] = os.getloadavg()
    dump()
    text = report(json.load(open(path)), median=False)
    open(os.path.join(out, "report.txt"), "w").write(text)
    print(text)
    plot_all(out)
    if args.archive:
        archive(out, args.archive, args.machine)


# ----------------------------------------------------------------------------------------
# report

def gmean(xs):
    xs = [x for x in xs if x and x > 0]
    return math.exp(sum(map(math.log, xs)) / len(xs)) if xs else float("nan")


def report(D, median=False):
    stat = statistics.median if median else min
    meta, times, same = D["meta"], D["times"], D["same"]
    names, configs = meta["versions"], meta["configs"]
    L = []
    L.append(f"{meta['date']} - {meta['machine']} - {meta['cxx']}")
    L.append(f"reference R : {meta['binaries']['ref'][1]} - cpp - {meta['flags']['ref']}")
    for n in names:
        fo = meta.get("faust_options", {}).get(n)
        L.append(f"{n} : {meta['binaries'][n][1]}" + (f" - faust {fo}" if fo else "") + f" - {meta['flags']['branch']}")
    L.append(f"{meta['rounds']} rounds, the {'median' if median else 'minimum'} ; every time / R of its precision")
    if meta["failed"]:
        L.append("not built : " + ", ".join(sorted(meta["failed"])))
    above = []
    for c in configs:
        lang, p = c.split("-")
        info = f"md-ocpp-{p}" if lang == "ocpp" and any(f"md-ocpp-{p}" in t for t in times.values()) else None
        cols = ([info] if info else []) + names
        L.append("")
        L.append(f"== {c}" + ("   (md-ocpp : master-dev ocpp, for information)" if info else ""))
        L.append(f"{'program':22s} " + " ".join(f"{(x if x != info else 'md-ocpp'):>12s}" for x in cols))
        col_vals = {x: [] for x in cols}
        for prog, tv in times.items():
            ref = tv.get(f"R-{p}")
            if not ref:
                continue
            r = stat(ref)
            # the versions with the same code form groups, whose rounds are pooled : every
            # member shows the value of its group
            group, groups = {}, []
            for n in names:
                if groups and same.get(f"{prog} {n}-{c}"):
                    groups[-1].append(n)
                else:
                    groups.append([n])
                group[n] = groups[-1]
            cells = []
            for x in cols:
                if x == info:
                    t = tv.get(info, [])
                    mark = ""
                else:
                    t = [y for m in group[x] for y in tv.get(f"{m}-{c}", [])]
                    mark = " (=)" if group[x][0] != x else ""
                if not t:
                    cells.append(f"{'-':>12s}")
                    continue
                val = stat(t) / r
                col_vals[x].append(val)
                if x != info and val > 1.05:
                    above.append(f"{prog} {x}-{c} {val:.2f}")
                cells.append(f"{val:.2f}{mark}".rjust(12))
            L.append(f"{prog:22s} " + " ".join(cells))
        L.append(f"{'geometric mean':22s} " + " ".join(f"{gmean(col_vals[x]):12.2f}" for x in cols))
    # the pooled cases repeat the value of the previous version : the cases above 1.05 once
    L.append("")
    L.append("above 1.05 : " + (", ".join(sorted(set(above))) if above else "none"))
    gaps = []
    for tv in times.values():
        for t in tv.values():
            if len(t) >= 2:
                s = sorted(t)
                gaps.append((s[1] - s[0]) / s[0])
    if gaps:
        gaps.sort()
        L.append(f"noise, the gap between the two best rounds : median {100 * gaps[len(gaps) // 2]:.1f} %, "
                 f"90th percentile {100 * gaps[int(0.9 * (len(gaps) - 1))]:.1f} %")
    if D.get("nonfinite"):
        L.append("non-finite outputs : " + ", ".join(sorted(D["nonfinite"])))
    if D.get("cxx_times"):
        L.append("")
        L.append("C++ compile time (CPU, the minimum of 3), / the compile time of R of the precision")
        ct = D["cxx_times"]
        for c in configs:
            lang, p = c.split("-")
            ratios = {n: [] for n in names}
            for prog in times:
                r = ct.get(f"{prog} R-{p}")
                for n in names:
                    t = ct.get(f"{prog} {n}-{c}")
                    if r and t:
                        ratios[n].append(t / r)
            L.append(f"  {c} : " + "  ".join(f"{n} {gmean(ratios[n]):.2f}" for n in names))
    if meta.get("levers"):
        L += lever_report(D, stat)
    return "\n".join(L) + "\n"


def lever_times(tv, side, c, lever):
    """the rounds of master-dev (side md) or of a version of the branch in configuration c,
    compiled with one lever : master-dev's own variant is -ffast-math, the branch's the contract"""
    lang, p = c.split("-")
    if side == "md" and lever == "fastmath":
        return tv.get(f"R-{p}" if lang == "cpp" else f"md-ocpp-{p}", [])
    if side != "md" and lever == "contract":
        return tv.get(f"{side}-{c}", [])
    return tv.get(f"{side}-{c}~{lever}", [])


def lever_report(D, stat):
    """for every configuration, master-dev and the branch compiled with each lever of
    -ffast-math alone, every time / R of its precision"""
    meta, times = D["meta"], D["times"]
    levers = [l for l in LEVERS if l in meta["levers"]]
    L = ["", "the levers of -ffast-math, one at a time over the contract, every time / R of its precision :"]
    for l in levers:
        L.append(f"  {l:9s} {meta['flags'].get(f'lever:{l}', '')}")
    for c in meta["configs"]:
        p = c.split("-")[1]
        for side in ["md"] + meta["versions"]:
            L.append("")
            L.append(f"== {c} {'master-dev' if side == 'md' else side}")
            L.append(f"{'program':22s} " + " ".join(f"{l:>9s}" for l in levers))
            cols = {l: [] for l in levers}
            for prog, tv in times.items():
                r = tv.get(f"R-{p}")
                cells = []
                for l in levers:
                    t = lever_times(tv, side, c, l)
                    if not r or not t:
                        cells.append(f"{'-':>9s}")
                        continue
                    cols[l].append(stat(t) / stat(r))
                    cells.append(f"{cols[l][-1]:9.2f}")
                L.append(f"{prog:22s} " + " ".join(cells))
            L.append(f"{'geometric mean':22s} " + " ".join(f"{gmean(cols[l]):9.2f}" for l in levers))
    return L


def cmd_report(args):
    text = report(json.load(open(os.path.join(args.dir, "results.json"))), median=args.median)
    print(text, end="")


# ----------------------------------------------------------------------------------------
# plot

def group_times(tv, same, names, n, c, prog):
    """the rounds of version n in configuration c, pooled with the versions of the same code"""
    groups = []
    for m in names:
        if groups and same.get(f"{prog} {m}-{c}"):
            groups[-1].append(m)
        else:
            groups.append([m])
    group = next(g for g in groups if n in g)
    return [y for m in group for y in tv.get(f"{m}-{c}", [])]


def plot(D, n, path, median=False):
    """the graph of the execution for version n : one panel per precision, every time / R of
    its precision on a logarithmic axis ; a hollow circle for master-dev, a filled square for
    the branch ; blue for cpp, red for ocpp"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D

    stat = statistics.median if median else min
    meta, times, same = D["meta"], D["times"], D["same"]
    names, configs = meta["versions"], meta["configs"]
    progs = [e for e in meta["entries"] if e in times]
    precs = [p for p in "fd" if any(c.endswith(f"-{p}") for c in configs)]
    BLUE, RED = "#2a6fdb", "#d6403a"
    rn = meta.get("ref_name", "master-dev")
    series = [("R", None, f"{rn} cpp", BLUE, "o"),
              ("md-ocpp", None, f"{rn} ocpp", RED, "o"),
              (n, "cpp", f"{n} cpp", BLUE, "s"),
              (n, "ocpp", f"{n} ocpp", RED, "s")]

    def values(key, lang, p):
        out = []
        for prog in progs:
            tv = times[prog]
            r = tv.get(f"R-{p}")
            if key in ("R", "md-ocpp"):
                t = tv.get(f"{key}-{p}", [])
            elif f"{lang}-{p}" in configs:
                t = group_times(tv, same, names, n, f"{lang}-{p}", prog)
            else:
                t = []
            out.append(stat(t) / stat(r) if r and t else None)
        return out

    data = {p: [values(key, lang, p) for key, lang, *_ in series] for p in precs}
    allv = [v for p in precs for ys in data[p] for v in ys if v]
    if not allv:
        print(f"no graph for {n} : nothing measured", file=sys.stderr)
        return
    # the axis runs from the tick below the smallest value to the tick above the largest
    T = (0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1, 1.2, 1.4, 1.7, 2, 2.5, 3, 4, 5, 10)
    t_lo = max([t for t in T if t <= min(allv)] or [T[0]])
    t_hi = min([t for t in T if t >= max(allv)] or [T[-1]])
    ticks = [t for t in T if t_lo <= t <= t_hi]
    lo, hi = t_lo / 1.04, t_hi * 1.04

    fig, axes = plt.subplots(len(precs), 1, figsize=(12, 4.25 * len(precs) + 0.5), sharex=True, squeeze=False)
    for ax, p in zip(axes[:, 0], precs):
        x = range(len(progs))
        for i in x:
            ax.axvline(i, color="#c8c8c8", lw=0.8, zorder=1)
        ax.axhline(1, color="#888", lw=0.8, zorder=1)
        gm = []
        for k, ((key, lang, label, col, mk), ys) in enumerate(zip(series, data[p])):
            pts = [(i + (k - 1.5) * 0.12, y) for i, y in zip(x, ys) if y]
            if not pts:
                continue
            ax.scatter(*zip(*pts), s=70, marker=mk, zorder=3, linewidths=2, edgecolors=col,
                       facecolors=col if mk == "s" else "white")
            if key != "R":
                gm.append(f"{label.replace(' (-ffast-math)', '')} {gmean([y for _, y in pts]):.2f}")
        ax.set_yscale("log")
        ax.set_ylim(lo, hi)
        ax.set_yticks(ticks)
        ax.set_yticklabels([f"{t:g}".replace(".", ",") for t in ticks])
        ax.minorticks_off()
        ax.grid(axis="y", color="#ddd", lw=0.6)
        ax.set_axisbelow(True)
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
        ax.set_ylabel(f"time / {rn} cpp")
        name = "simple précision (float)" if p == "f" else "double précision"
        ax.set_title(f"{name} — moyennes géométriques : " + ", ".join(gm).replace(".", ","), loc="left", fontsize=11)
    axes[-1, 0].set_xticks(range(len(progs)))
    axes[-1, 0].set_xticklabels(progs, rotation=30, ha="right")
    handles = [Line2D([], [], ls="", marker=mk, ms=9, mew=2, mec=col, mfc=col if mk == "s" else "white", label=label)
               for _, _, label, col, mk in series]
    fig.legend(handles=handles, loc="upper center", ncol=4, frameon=False, bbox_to_anchor=(0.5, 0.99))
    commit = lambda b: meta["binaries"][b][1].rpartition(": ")[2]
    fig.text(0.01, 0.005,
             f"{meta['machine']}, {meta['cxx']}, {meta['date'][:10]} ; {rn} {commit('ref')} {meta['flags']['ref']} ;\n"
             f"{n} {commit(n)} {meta['flags']['branch']} ; {'médiane' if median else 'minimum'} de "
             f"{meta['rounds']} tours ; < 1 = plus rapide", fontsize=8, color="#555")
    fig.tight_layout(rect=(0, 0.04, 1, 0.95))
    fig.savefig(path, dpi=130)
    plt.close(fig)


def plot_all(directory, median=False):
    """DIR/panier-<version>.png for every version of the branch, or a note without matplotlib"""
    D = json.load(open(os.path.join(directory, "results.json")))
    try:
        import matplotlib  # noqa: F401
    except ImportError:
        print("no graph : matplotlib is missing (pip install matplotlib, then mesure.py plot DIR)", file=sys.stderr)
        return
    for n in D["meta"]["versions"]:
        path = os.path.join(directory, f"panier-{n}.png")
        plot(D, n, path, median)
        print(f"graph : {path}", file=sys.stderr)


def cmd_plot(args):
    plot_all(args.dir, args.median)


# ----------------------------------------------------------------------------------------
# faust-times

def corpus_of(root, path):
    if path:
        return [l.strip().partition(":")[2] if l.startswith("prog:") else l.strip() for l in open(path) if l.strip()]
    r = subprocess.run(["git", "-C", root, "ls-files", "examples/*.dsp", "tests/impulse-tests/dsp/*.dsp"],
                       capture_output=True, text=True)
    return [f for f in r.stdout.split() if "/error/" not in f and "/badcode/" not in f]


def cmd_faust_times(args):
    args.versions = parse_versions(args.version)
    root = os.path.abspath(args.faust_root)
    lib = os.path.join(root, "libraries")
    out = os.path.abspath(args.out)
    os.makedirs(out, exist_ok=True)
    C = corpus_of(root, args.corpus)
    prov = provenance(args.ref, args.versions, root)
    check_modified(prov, args.allow_modified)
    prov["corpus"] = {f: sha256_file(os.path.join(root, f)) for f in C}
    runs = {"R-f": (args.ref, "cpp", "f"), "R-d": (args.ref, "cpp", "d")}
    for n, b in args.versions:
        for lang in ("cpp", "ocpp"):
            for p in ("f", "d"):
                runs[f"{n}-{lang}-{p}"] = (b, lang, p)

    def one(f):
        res = {}
        for k, (b, lang, p) in runs.items():
            ts = []
            for _ in range(args.runs):
                try:
                    t = cpu_time([b, "-lang", lang, PRECISION[p], "-I", lib, "-I", os.path.join(lib, "dx7"),
                                  os.path.join(root, f), "-o", os.devnull], 600)
                except subprocess.TimeoutExpired:
                    t = None
                if t is None:
                    break
                ts.append(t)
            res[k] = min(ts) if len(ts) == args.runs else None
        return f, res

    T = {}
    with ThreadPoolExecutor(args.jobs) as ex:
        for f, res in ex.map(one, C):
            T[f] = res
    meta = {"date": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "machine": machine(), "runs": args.runs, "jobs": args.jobs,
            "binaries": {"ref": [args.ref, version_line(args.ref)],
                         **{n: [b, version_line(b)] for n, b in args.versions}},
            "versions": [n for n, _ in args.versions], "provenance": prov}
    prov["host"]["load_end"] = os.getloadavg()
    json.dump({"meta": meta, "times": T}, open(os.path.join(out, "faust-times.json"), "w"), indent=1)
    L = [f"{meta['date']} - {meta['machine']} - faust CPU time, the minimum of {args.runs}, {len(C)} programs",
         f"reference R : {meta['binaries']['ref'][1]} -lang cpp"]
    for n, b in args.versions:
        L.append(f"{n} : {meta['binaries'][n][1]}")
    for k in runs:
        if k.startswith("R-"):
            continue
        p = k[-1]
        pairs = [(T[f][f"R-{p}"], T[f][k]) for f in C if T[f].get(f"R-{p}") and T[f].get(k)]
        if not pairs:
            continue
        ratios = sorted(b / a for a, b in pairs)
        total = sum(b for _, b in pairs) / sum(a for a, _ in pairs)
        L.append(f"{k:14s} programs {len(pairs):4d}  total {total:.2f}  geometric mean {gmean(ratios):.2f}  "
                 f"median {ratios[len(ratios) // 2]:.2f}  max {ratios[-1]:.2f}")
    # the slowest programs (0.5 s or more on either side), the first version in cpp-f
    if args.versions:
        k = f"{args.versions[0][0]}-cpp-f"
        slow = [(f, T[f]["R-f"], T[f][k]) for f in C if T[f].get("R-f") and T[f].get(k)
                and max(T[f]["R-f"], T[f][k]) >= 0.5]
        slow.sort(key=lambda x: -x[2])
        L.append(f"slowest ({k}, at least 0.5 s) :")
        for f, a, b in slow[:15]:
            L.append(f"  {f:60s} {a:6.2f} s -> {b:6.2f} s  x{b / a:.2f}")
    text = "\n".join(L) + "\n"
    open(os.path.join(out, "faust-times.txt"), "w").write(text)
    print(text, end="")
    if args.archive:
        archive(out, args.archive, args.machine)


# ----------------------------------------------------------------------------------------
# provenance afterwards

def load_measure(directory):
    for name in ("results.json", "faust-times.json"):
        p = os.path.join(directory, name)
        if os.path.exists(p):
            return json.load(open(p)), p
    sys.exit(f"{directory} : neither results.json nor faust-times.json")


def cmd_provenance(args):
    d = os.path.abspath(args.dir)
    if os.path.dirname(d) == MESURES:
        sys.exit(f"{d} is archived : an archived measure is never rewritten")
    D, path = load_measure(d)
    if D["meta"].get("provenance"):
        sys.exit(f"{path} already has its provenance")
    root = os.path.abspath(args.faust_root)
    kind = measure_kind(D)
    P = provenance(args.ref, parse_versions(args.version), root, args.entries if kind == "panier" else None)
    # the binaries must be those of the measure
    for k, p in P["binaries"].items():
        recorded = D["meta"]["binaries"].get(k, [None, None])[1]
        if p["version"] != recorded:
            sys.exit(f"{k} : the binary says '{p['version']}', the measure recorded '{recorded}'")
    P["reconstructed"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    P["host"]["note"] = "the host at the reconstruction : affinity and load are not those of the measure"
    if kind == "panier":
        P["code"] = {}
        for prog in D["times"]:
            for v in D["variants"]:
                cpp = os.path.join(d, f"{prog}.{v}.cpp")
                if os.path.exists(cpp):
                    P["code"][f"{prog} {v}"] = hashlib.sha256("\n".join(code_body(cpp)).encode()).hexdigest()
    else:
        P["corpus"] = {f: sha256_file(os.path.join(root, f)) for f in D["times"] if os.path.exists(os.path.join(root, f))}
    D["meta"]["provenance"] = P
    tmp = path + ".tmp"
    json.dump(D, open(tmp, "w"), indent=1)
    os.replace(tmp, path)
    print(f"provenance added to {path}", file=sys.stderr)


# ----------------------------------------------------------------------------------------
# archive

INDEX = os.path.join(MESURES, "INDEX.tsv")
COLUMNS = ["id", "date", "type", "machine", "cxx", "ref", "key", "version", "commit", "tree", "branch",
           "cpp-f", "cpp-d", "ocpp-f", "ocpp-d", "md-ocpp-f", "md-ocpp-d", "provenance", "note"]


def read_index():
    if not os.path.exists(INDEX):
        return []
    with open(INDEX, newline="") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def versions_of(D):
    return D["meta"].get("versions") or [n for n in D["meta"]["binaries"] if n != "ref"]


def panier_means(D, n, median=False):
    """the geometric means of version n against R of its precision, and of master-dev ocpp"""
    stat = statistics.median if median else min
    meta, times, same = D["meta"], D["times"], D["same"]
    M = {}
    for c in meta["configs"]:
        p = c.split("-")[1]
        vals = []
        for prog, tv in times.items():
            r, t = tv.get(f"R-{p}"), group_times(tv, same, meta["versions"], n, c, prog)
            if r and t:
                vals.append(stat(t) / stat(r))
        M[c] = gmean(vals)
    for p in "fd":
        vals = [stat(tv[f"md-ocpp-{p}"]) / stat(tv[f"R-{p}"]) for tv in times.values()
                if tv.get(f"md-ocpp-{p}") and tv.get(f"R-{p}")]
        if vals:
            M[f"md-ocpp-{p}"] = gmean(vals)
    return M


def ftimes_means(D, n):
    """the geometric means of the faust times of version n against master-dev cpp"""
    T, M = D["times"], {}
    for lang in ("cpp", "ocpp"):
        for p in "fd":
            vals = [T[f][f"{n}-{lang}-{p}"] / T[f][f"R-{p}"] for f in T
                    if T[f].get(f"R-{p}") and T[f].get(f"{n}-{lang}-{p}")]
            if vals:
                M[f"{lang}-{p}"] = gmean(vals)
    return M


def cxx_short(cxx):
    m = re.search(r"(clang|g\+\+|gcc)\D*?(\d+)\.", cxx or "")
    if not m:
        return "cxx"
    return ("gcc" if m.group(1) != "clang" else "apple-clang" if "Apple" in cxx else "clang") + m.group(2)


def branch_of(bp):
    if bp.get("head_branch") not in (None, "HEAD"):
        return bp["head_branch"]
    return ",".join(b for b in bp.get("branches", []) if "HEAD" not in b)[:120]


def provenance_state(prov):
    if not prov:
        return "partielle"
    if prov.get("allow_modified"):
        return "de travail"
    return "reconstruite" if prov.get("reconstructed") else "complète"


def append_index(rows):
    new = not os.path.exists(INDEX)
    os.makedirs(MESURES, exist_ok=True)
    with open(INDEX, "a", newline="") as f:
        w = csv.DictWriter(f, COLUMNS, delimiter="\t", lineterminator="\n", extrasaction="ignore")
        if new:
            w.writeheader()
        for r in rows:
            w.writerow({k: str(v).replace("\t", " ").replace("\n", " ") for k, v in r.items()})


def archive(directory, name, machine_name=None, note=None, legacy_date=None):
    """sessions/<id>/ and its lines in sessions/INDEX.tsv ; never over an archived measure"""
    directory = os.path.abspath(directory)
    for x in (name, machine_name or ""):
        if not re.fullmatch(r"[A-Za-z0-9_.+-]*", x):
            sys.exit(f"'{x}' : letters, digits, '.', '_', '+' and '-' only")
    inside = os.path.dirname(directory) == MESURES
    if legacy_date:
        # a measure of an older protocol : kept whole, indexed by one line, compared with nothing
        if not machine_name or not note:
            sys.exit("an older measure needs --machine and --note")
        mid = "-".join([legacy_date, machine_name, "ancienne", name])
        rows = [{"id": mid, "date": legacy_date, "type": "ancienne", "machine": machine_name, "key": "-",
                 "provenance": "ancienne", "note": note}]
        D = None
    else:
        D, _ = load_measure(directory)
        meta, kind = D["meta"], measure_kind(D)
        prov = meta.get("provenance")
        machine_name = machine_name or (prov["host"]["hostname"].split(".")[0].lower() if prov else None)
        if not machine_name:
            sys.exit("no host recorded in this measure : --machine NAME")
        # the date and the hour (UTC) of the start of the measure : two measures of a day apart
        stamp = meta["date"][:10] + "-" + meta["date"][11:13] + meta["date"][14:16] + "Z"
        mid = os.path.basename(directory) if inside else "-".join(
            [stamp, machine_name, cxx_short(meta.get("cxx")) if kind == "panier" else "faust-times", name])
        ref = re.search(r"Source commit: (\S+)", meta["binaries"]["ref"][1])
        rows = []
        for n in versions_of(D):
            bp = (prov or {}).get("binaries", {}).get(n, {})
            m = re.search(r"Source commit: (\S+)", meta["binaries"][n][1])
            means = panier_means(D, n) if kind == "panier" else ftimes_means(D, n)
            rows.append({"id": mid, "date": meta["date"], "type": kind, "machine": meta["machine"],
                         "cxx": meta.get("cxx", ""), "ref": ref.group(1) if ref else "", "key": comparison_key(D),
                         "version": n, "commit": m.group(1) if m else "", "tree": (bp.get("tree") or "")[:12],
                         "branch": branch_of(bp), **{c: f"{v:.3f}" for c, v in means.items()},
                         "provenance": provenance_state(prov), "note": note or ""})
    if any(r["id"] == mid for r in read_index()):
        sys.exit(f"{mid} is already archived : an archived measure is never rewritten")
    dest = directory if inside else os.path.join(MESURES, mid)
    if not inside:
        if os.path.exists(dest):
            sys.exit(f"{dest} exists")
        if D is None:
            shutil.copytree(directory, dest)
        else:
            os.makedirs(dest)
            for f in ("results.json", "ftz.h", "faust-times.json", "faust-times.txt"):
                if os.path.exists(os.path.join(directory, f)):
                    shutil.copy2(os.path.join(directory, f), dest)
    if D is not None and measure_kind(D) == "panier":
        # the reports derive from results.json : written when missing ; the graphs are not
        # archived (the site draws them, mesure.py plot DIR redraws them from results.json)
        for f, med in (("report.txt", False), ("report-median.txt", True)):
            if not os.path.exists(os.path.join(dest, f)):
                open(os.path.join(dest, f), "w").write(report(D, median=med))
    append_index(rows)
    rel = os.path.relpath(dest, os.path.dirname(MESURES))
    print(f"archived : {rel} ({len(rows)} line(s) in sessions/INDEX.tsv)\n"
          f"  git -C {os.path.dirname(MESURES)} add {rel} sessions/INDEX.tsv && git commit", file=sys.stderr)


def cmd_archive(args):
    archive(args.dir, args.name, args.machine, args.note, args.legacy)


# ----------------------------------------------------------------------------------------
# history

def cmd_history(args):
    rows = [r for r in read_index()
            if (not args.key or r["key"] == args.key) and (not args.type or r["type"] == args.type)
            and (not args.machine or args.machine.lower() in r["machine"].lower())
            and (not args.version or any(args.version in r[c] for c in ("version", "commit", "branch")))]
    groups = {}
    for r in sorted(rows, key=lambda r: r["date"]):
        groups.setdefault(r["key"], []).append(r)
    cols = ["cpp-f", "cpp-d", "ocpp-f", "ocpp-d", "md-ocpp-f", "md-ocpp-d"]
    for key, rs in groups.items():
        r0 = rs[0]
        print(f"== key {key} : {r0['type']}, reference {r0['ref'] or '-'}, {r0['cxx'] or '-'}, {r0['machine']}")
        w = max(len(r["id"]) for r in rs)
        print(f"{'date':10s} {'id':{w}s} {'version':10s} {'commit':20s} " + " ".join(f"{c:>9s}" for c in cols)
              + "  provenance")
        for r in rs:
            print(f"{r['date'][:10]:10s} {r['id']:{w}s} {r['version']:10s} {r['commit']:20s} "
                  + " ".join(f"{r[c] or '-':>9s}" for c in cols) + f"  {r['provenance']}" + (
                      f"  ({r['note']})" if r["note"] else ""))
        print()
    if args.plot:
        plot_history({k: rs for k, rs in groups.items() if rs[0]["type"] != "ancienne"}, args.plot)


def plot_history(groups, path):
    """one panel per key : the geometric means of every archived measure, in date order"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    if not groups:
        sys.exit("nothing to plot")
    BLUE, RED = "#2a6fdb", "#d6403a"
    styles = {"cpp-f": (BLUE, "s", True), "cpp-d": (BLUE, "s", False), "ocpp-f": (RED, "s", True),
              "ocpp-d": (RED, "s", False), "md-ocpp-f": (RED, "o", True), "md-ocpp-d": (RED, "o", False)}
    fig, axes = plt.subplots(len(groups), 1, figsize=(12, 3.8 * len(groups) + 0.6), squeeze=False)
    for ax, (key, rs) in zip(axes[:, 0], groups.items()):
        x = range(len(rs))
        for c, (col, mk, single) in styles.items():
            pts = [(i, float(r[c])) for i, r in zip(x, rs) if r.get(c)]
            if pts:
                ax.plot(*zip(*pts), marker=mk, ms=7, mew=1.5, color=col, mfc=col if single else "white",
                        ls="-" if single else "--", lw=1, label=c)
        ax.axhline(1, color="#888", lw=0.8)
        ax.set_yscale("log")
        decimal = matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:.2g}".replace(".", ","))
        ax.yaxis.set_major_formatter(decimal)
        ax.yaxis.set_minor_formatter(decimal)
        ax.grid(axis="y", which="both", color="#e4e4e4", lw=0.6)
        ax.set_xticks(list(x))
        ax.set_xticklabels([f"{r['date'][:10]}\n{r['version']} {r['commit'][:9]}" for r in rs], fontsize=8)
        ax.set_title(f"{rs[0]['type']} — référence {rs[0]['ref']}, {rs[0]['cxx'] or '-'}, {rs[0]['machine']}"
                     f" (clé {key})", loc="left", fontsize=10)
        ax.set_ylabel("moyenne géométrique / R")
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
        ax.legend(fontsize=8, ncol=6, frameon=False)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    print(f"graph : {path}", file=sys.stderr)


def main():
    here = HERE
    P = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    S = P.add_subparsers(dest="cmd", required=True)
    r = S.add_parser("run")
    r.add_argument("--ref", required=True)
    r.add_argument("--version", action="append", default=[], required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--entries", default=os.path.join(here, "..", "basket", "entries.txt"))
    r.add_argument("--faust-root", default="/home/user/faust")
    r.add_argument("--arch", help="the harness (default <faust-root>/libraries/arch/cpu_arch.cpp)")
    r.add_argument("--cxx", default="clang++")
    r.add_argument("--rounds", type=int, default=9)
    r.add_argument("--configs", default="cpp-f,cpp-d,ocpp-f,ocpp-d")
    r.add_argument("--no-info", action="store_true")
    r.add_argument("--cxx-times", action="store_true")
    r.add_argument("--jobs", type=int, default=4)
    r.add_argument("--allow-modified", action="store_true")
    r.add_argument("--archive", metavar="NAME")
    r.add_argument("--machine")
    r.add_argument("--faust-options", action="append", default=[], metavar='NAME="<options>"',
                   help="options of faust for the version NAME")
    r.add_argument("--describe", action="append", default=[], metavar='NAME="<text>"',
                   help="what the version NAME, or the reference, tries to do")
    r.add_argument("--ref-name", default="master-dev", help="the name of the reference (default master-dev)")
    r.add_argument("--ref-flags", default=REF_FLAGS, help=f"C++ options of the reference (default {REF_FLAGS})")
    r.add_argument("--version-flags", help="C++ options of the versions (default : those of the reference)")
    r.add_argument("--contract", action="store_true",
                   help=f"the versions under the contract of the certification branch : {BRANCH_FLAGS} + ftz.h")
    r.add_argument("--levers", help="the diagnostic : a comma-separated list among " + ",".join(LEVERS))
    r.set_defaults(func=cmd_run)
    q = S.add_parser("report")
    q.add_argument("dir")
    q.add_argument("--median", action="store_true")
    q.set_defaults(func=cmd_report)
    g = S.add_parser("plot")
    g.add_argument("dir")
    g.add_argument("--median", action="store_true")
    g.set_defaults(func=cmd_plot)
    f = S.add_parser("faust-times")
    f.add_argument("--ref", required=True)
    f.add_argument("--version", action="append", default=[])
    f.add_argument("--out", required=True)
    f.add_argument("--faust-root", default="/home/user/faust")
    f.add_argument("--corpus")
    f.add_argument("--runs", type=int, default=3)
    f.add_argument("--jobs", type=int, default=1)
    f.add_argument("--allow-modified", action="store_true")
    f.add_argument("--archive", metavar="NAME")
    f.add_argument("--machine")
    f.set_defaults(func=cmd_faust_times)
    v = S.add_parser("provenance")
    v.add_argument("dir")
    v.add_argument("--ref", required=True)
    v.add_argument("--version", action="append", default=[])
    v.add_argument("--faust-root", required=True)
    v.add_argument("--entries", default=os.path.join(here, "..", "basket", "entries.txt"))
    v.set_defaults(func=cmd_provenance)
    a = S.add_parser("archive")
    a.add_argument("dir")
    a.add_argument("--name", required=True)
    a.add_argument("--machine")
    a.add_argument("--note")
    a.add_argument("--legacy", metavar="YYYY-MM-DD", help="a measure of an older protocol, kept whole")
    a.set_defaults(func=cmd_archive)
    h = S.add_parser("history")
    h.add_argument("--key")
    h.add_argument("--machine")
    h.add_argument("--version")
    h.add_argument("--type", choices=["panier", "faust-times", "ancienne"])
    h.add_argument("--plot", metavar="FILE")
    h.set_defaults(func=cmd_history)
    args = P.parse_args()
    args.ref = os.path.abspath(args.ref) if getattr(args, "ref", None) else None
    args.func(args)


if __name__ == "__main__":
    main()
