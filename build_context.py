#!/usr/bin/env python3
"""habitat-context builder — curated, searchable context for agent sessions (habitat R-095 draft).

Builds ~/.local/share/turso/context/habitat-context.db (Turso 0.8.1 via pyturso) from the laptop's
off-site vault snapshot. Read-only toward the desktop: embeddings come from the desktop's ollama
through an SSH local forward (compute only), and Jev eligibility from `jev-boundary` run there.

  sections(id, vault, path, heading, body, sha, jev_ok, emb)   one row per note section
  notes(path, vault, sha, mtime)                               change detection
  meta(key, value)                                             build provenance
  FTS index sections_fts on sections(body) (experimental index_method)

Incremental: a note whose sha is unchanged keeps its rows and embeddings.
v3 is fenced (V4-9) and never indexed.
"""
import argparse, glob, hashlib, json, os, re, shutil, subprocess, sys, time, urllib.request

import turso

HOME = os.path.expanduser("~")
SNAP = os.path.join(HOME, "Backups/herdr-habitat-offsite/snap/latest/vaults")
DB = os.path.join(HOME, ".local/share/turso/context/habitat-context.db")
REMOTE_VAULTS = "/mnt/storage-10tb/fedora-obsidian-vaults"
EMB_URL = "http://127.0.0.1:{port}/v1/embeddings"
EMB_MODEL = "qwen3-embedding:0.6b"
# Vaults whose sections are embedded; every other vault gets full-text search only.
PRIORITY = {"herdr.habitat.vault", "herdr-engineering-engine-v4.vault", "jev.vault", "turso.vault",
            "herdr-habitat-orchistration.vault", "toolshed.vault"}
FENCED = ("herdr-engineering-engine-v3.vault",)
MAX_SECTION = 2000


def sections_of(text):
    for part in re.split(r"\n(?=#{1,3} )", text):
        part = part.strip()
        if len(part) < 80:
            continue
        head = part.splitlines()[0].lstrip("# ").strip()[:160]
        yield head, part[:MAX_SECTION]


def embed(texts, port):
    out = []
    for i in range(0, len(texts), 64):
        req = urllib.request.Request(EMB_URL.format(port=port),
                                     json.dumps({"model": EMB_MODEL, "input": texts[i:i + 64]}).encode(),
                                     {"Content-Type": "application/json"})
        out += [d["embedding"] for d in json.load(urllib.request.urlopen(req, timeout=600))["data"]]
    return out


def jev_eligible(rel_paths):
    """Ask the desktop's real boundary (local-only, exit 3 = refuse) for each note, by path and text."""
    script = ("import sys,json,subprocess\n"
              "res={}\n"
              "for p in json.load(sys.stdin):\n"
              "  f=%r+'/'+p\n"
              "  a=subprocess.run(['jev-boundary','path',f],capture_output=True).returncode\n"
              "  b=subprocess.run(['jev-boundary','text'],stdin=open(f,'rb'),capture_output=True).returncode if a==0 else 3\n"
              "  res[p]=(a==0 and b==0)\n"
              "print(json.dumps(res))\n") % REMOTE_VAULTS
    # Never pass code through `ssh host python3 -c …`: the remote shell re-parses it (the 2026-10-06
    # door clobber). Ship the checker as a file into the desktop test cache, then run it by path.
    local = os.path.join(os.path.dirname(DB), "elig_check.py")
    with open(local, "w") as fh:
        fh.write(script)
    remote = "/home/louranicas/.cache/claude-test-20261006/ctx/elig_check.py"
    subprocess.run(["scp", "-q", local, f"desktop-native-tail:{remote}"], check=True, timeout=60)
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", "desktop-native-tail", "python3", remote],
                       input=json.dumps(rel_paths).encode(), capture_output=True, timeout=1800)
    if r.returncode != 0:  # noqa
        sys.exit(f"boundary check failed rc={r.returncode}: {r.stderr.decode()[:300]}")
    return json.loads(r.stdout)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=11435, help="local end of the ssh -L forward to desktop ollama")
    ap.add_argument("--no-embed", action="store_true")
    a = ap.parse_args()
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    # Publish by generation: build into a private copy, checkpoint it, then atomically rename over the
    # live file. Readers (habitat-ctx, the read-only MCP server) never see a half-built DB, and the
    # live file never has a writer (tursodb 0.8.1 does not coordinate WAL across processes by default).
    work = DB + ".building"
    for ext in ("", "-wal", "-shm"):
        if os.path.exists(work + ext):
            os.remove(work + ext)
    if os.path.exists(DB):
        shutil.copy2(DB, work)
        if os.path.exists(DB + "-wal") and os.path.getsize(DB + "-wal") > 0:
            shutil.copy2(DB + "-wal", work + "-wal")
    con = turso.connect(work, experimental_features="index_method")
    cur = con.cursor()
    cur.execute("create table if not exists notes(path text primary key, vault text, sha text, mtime real)")
    cur.execute("create table if not exists sections(id integer primary key, vault text, path text, heading text,"
                " body text, sha text, jev_ok integer, emb blob)")
    cur.execute("create table if not exists meta(key text primary key, value text)")
    cur.execute("drop table if exists prime_receipts")  # receipts live in receipts.db (single writer: habitat-ctx)
    cur.execute("delete from meta where key='live_probe'")
    con.commit()
    have = dict(cur.execute("select path, sha from notes").fetchall())
    files = [f for f in glob.glob(os.path.join(SNAP, "*.vault", "**", "*.md"), recursive=True)
             if not any(x in f for x in FENCED) and "/.obsidian/" not in f and "/.trash/" not in f
             and "/_sources/" not in f]  # raw doc captures duplicate "90 Source Docs" (measured noise)
    changed, seen = [], set()
    for f in files:
        rel = os.path.relpath(f, SNAP); seen.add(rel)
        sha = hashlib.sha256(open(f, "rb").read()).hexdigest()
        if have.get(rel) != sha:
            changed.append((rel, f, sha))
    gone = [p for p in have if p not in seen]
    for p in gone:
        cur.execute("delete from sections where path=?", (p,)); cur.execute("delete from notes where path=?", (p,))
    print(f"notes={len(files)} changed={len(changed)} removed={len(gone)}", flush=True)
    elig = jev_eligible([c[0] for c in changed]) if changed else {}
    t0 = time.time(); rows = []
    for rel, f, sha in changed:
        vault = rel.split("/", 1)[0]
        cur.execute("delete from sections where path=?", (rel,))
        for head, body in sections_of(open(f, errors="replace").read()):
            rows.append([vault, rel, head, body, sha, int(bool(elig.get(rel))), None])
        cur.execute("insert or replace into notes values(?,?,?,?)", (rel, vault, sha, os.path.getmtime(f)))
    todo = [r for r in rows if r[0] in PRIORITY] if not a.no_embed else []
    if todo:
        vecs = embed([f"{r[2]}\n{r[3]}" for r in todo], a.port)
        for r, v in zip(todo, vecs):
            r[6] = json.dumps(v)
    for r in rows:
        if r[6] is None:
            cur.execute("insert into sections(vault,path,heading,body,sha,jev_ok,emb) values(?,?,?,?,?,?,NULL)", r[:6])
        else:
            cur.execute("insert into sections(vault,path,heading,body,sha,jev_ok,emb) values(?,?,?,?,?,?,vector32(?))", r)
    con.commit()
    try:
        cur.execute("create index if not exists sections_fts on sections using fts(body)")
    except Exception as e:
        print("fts index:", e)
    snap = os.path.realpath(os.path.join(SNAP, ".."))
    for k, v in {"built_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "snapshot": os.path.basename(snap),
                 "emb_model": EMB_MODEL, "tursodb": "0.8.1"}.items():
        cur.execute("insert or replace into meta values(?,?)", (k, v))
    con.commit()
    n, e, j = cur.execute("select count(*), count(emb), sum(jev_ok) from sections").fetchone()
    assert cur.execute("pragma integrity_check").fetchone()[0] == "ok", "integrity_check failed; not publishing"
    cur.execute("pragma wal_checkpoint(TRUNCATE)")
    con.close()
    for ext in ("-wal", "-shm"):
        if os.path.exists(work + ext) and os.path.getsize(work + ext) == 0:
            os.remove(work + ext)
    if os.path.exists(work + "-wal"):
        sys.exit("checkpoint left WAL frames; not publishing")
    os.replace(work, DB)  # atomic publish
    # A WAL beside the live name belongs to the previous generation (the live file has no writer), and
    # would be replayed over the new file by the next opener. Remove it as part of publishing.
    for ext in ("-wal", "-shm"):
        if os.path.exists(DB + ext):
            os.remove(DB + ext)
    print(f"sections={n} embedded={e} jev_ok={j} new_rows={len(rows)} embedded_now={len(todo)} in {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
