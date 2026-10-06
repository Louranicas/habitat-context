#!/usr/bin/env python3
"""Regression evaluation for habitat-ctx on the independent held-out set (tests/heldout.json: 40 on-topic
questions written by an agent with no retrieval access, plus 8 off-topic).

  python3 tests/eval_heldout.py [--fts-only] [--min-hit1 N] [--min-hit5 N] [--json out.json] [extra ask args...]

Exits 1 if hit@1 or hit@5 falls below the floors (defaults: the v3 baseline minus 2), so a change that
quietly degrades retrieval fails loudly. Uses HABITAT_CTX_DB if set (point it at a copy to test builds).
"""
import json, os, re, subprocess, sys, time, collections
HERE = os.path.dirname(os.path.abspath(__file__))
H = json.load(open(os.path.join(HERE, "heldout.json")))
args = sys.argv[1:]
def opt(name, default):
    if name in args:
        i = args.index(name); v = args[i + 1]; del args[i:i + 2]; return v
    return default
min1, min5 = int(opt("--min-hit1", 18)), int(opt("--min-hit5", 31))
out_json = opt("--json", None)
env = dict(os.environ)
env.setdefault("HABITAT_CTX_RECEIPTS", "/tmp/habitat-ctx-eval-receipts.db")
env.setdefault("HABITAT_CTX_ORIGIN", "eval")  # never pollute the real audit trail
if "--fts-only" in args:
    args.remove("--fts-only"); env.update(HABITAT_CTX_NO_TUNNEL="1", HABITAT_CTX_EMB_URL="http://127.0.0.1:9/x")
    min1, min5 = min(min1, 12), min(min5, 28)
ctx = os.path.join(os.path.dirname(HERE), "habitat-ctx")
ranks, by, lat = [], collections.defaultdict(list), []
for it in H["on"]:
    t = time.time()
    out = subprocess.run([ctx, "ask", it["q"], "-k", "10"] + args, capture_output=True, text=True, env=env).stdout.splitlines()
    lat.append(time.time() - t)
    paths = [m.group(1) for l in out[1:] for m in [re.match(r"(?:P\(yes\)=\S+\s+)?\S+\s+(.+?)#", l)] if m]
    r = next((i + 1 for i, p in enumerate(paths) if p in it["gold"]), None)
    ranks.append(r); by[it["style"]].append(r); by["deep" if it["deep"] else "shallow"].append(r)
k = lambda rs, n: sum(1 for r in rs if r and r <= n)
abst = sum("ABSTAIN" in subprocess.run([ctx, "ask", q, "-k", "1"] + args, capture_output=True, text=True, env=env).stdout.splitlines()[0]
           for q in H["off"])
res = {"n": len(ranks), "hit1": k(ranks, 1), "hit3": k(ranks, 3), "hit5": k(ranks, 5), "hit10": k(ranks, 10),
       "miss": sum(r is None for r in ranks), "off_abstained": abst, "off_n": len(H["off"]),
       "mean_s": round(sum(lat) / len(lat), 2), "by": {s: [k(v, 1), k(v, 5), len(v)] for s, v in sorted(by.items())},
       "ranks": ranks}
print(f"hit@1={res['hit1']} hit@3={res['hit3']} hit@5={res['hit5']} hit@10={res['hit10']} miss={res['miss']} "
      f"off-abstained={abst}/{len(H['off'])} mean={res['mean_s']}s")
for s, (a, b, n) in res["by"].items():
    print(f"   {s:8s} n={n:2d} @1={a} @5={b}")
if out_json:
    json.dump(res, open(out_json, "w"), indent=1)
ok = res["hit1"] >= min1 and res["hit5"] >= min5
print(f"verdict={'PASS' if ok else 'FAIL'} floors hit@1>={min1} hit@5>={min5}")
sys.exit(0 if ok else 1)
