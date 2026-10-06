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
ON_DESKTOP = os.path.isdir("/mnt/storage-10tb/fedora-obsidian-vaults")
# On the desktop build from the live vaults; on the laptop from the off-site snapshot.
SNAP = os.environ.get("HABITAT_SNAP", "/mnt/storage-10tb/fedora-obsidian-vaults" if ON_DESKTOP
                      else os.path.join(HOME, "Backups/herdr-habitat-offsite/snap/latest/vaults"))
DB = os.environ.get("HABITAT_CTX_DB", os.path.join(HOME, ".local/share/turso/context/habitat-context.db"))
REMOTE_VAULTS = "/mnt/storage-10tb/fedora-obsidian-vaults"
EMB_URL = "http://127.0.0.1:{port}/v1/embeddings"
EMB_MODEL = "qwen3-embedding:0.6b"
# Vaults whose sections are embedded; every other vault gets full-text search only.
PRIORITY = {"herdr.habitat.vault", "herdr-engineering-engine-v4.vault", "jev.vault", "turso.vault",
            "herdr-habitat-orchistration.vault", "toolshed.vault"}
FENCED = ("herdr-engineering-engine-v3.vault",)
MAX_SECTION = 2000
BUILD_VERSION = "5"  # v5: + doc2query rows (local model: 5 plain-language questions per note, cached by sha); v4 note cards; v3 contextual chunks, body FTS
# Held-out (n=40, 2026-10-06): v3 20/28/33/35, v4 21/29/33/35, v4+doc2query 21/31/35/36 @1/3/5/10; misses 5 -> 4.
D2Q_VAULTS = {"herdr.habitat.vault", "jev.vault", "toolshed.vault", "herdr-habitat-orchistration.vault", "herdr-engineering-engine-v4.vault"}
D2Q_MODEL = "gemma4:12b"
D2Q_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "d2q-cache.db")  # builder-only writer
FTS_COLS = "body"   # held-out (n=40, 2026-10-06): body-only FTS 14/29/30 @1/3/5; (ctx,body) 13/24/25; (ctx,body) ctx=2 10/17/21


def strip_frontmatter(text):
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[end + 4:]
    return text


def note_card(title, text):
    """One row per note for whole-note questions (held-out misses were all 'what is this note for'):
    the title, the opening prose before the first heading, and the outline of headings."""
    body = strip_frontmatter(text)
    first = re.split(r"\n#{1,3} ", "\n" + body, maxsplit=2)
    opening = " ".join(x for x in first[:2] if x).strip()[:900]
    outline = " | ".join(h.strip() for h in re.findall(r"^#{1,3} (.+)$", body, re.M))[:600]
    return f"{title}\n{opening}\nSections: {outline}"


def sections_of(text):
    for part in re.split(r"\n(?=#{1,3} )", strip_frontmatter(text)):
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


def d2q_questions(title, text, port):
    """Five plain-language questions the note answers (local model; bridges colloquial questions to
    metaphorical titles such as 'Dark factory readiness')."""
    body = {"model": D2Q_MODEL, "temperature": 0.3, "max_tokens": 220, "reasoning_effort": "none",
            "messages": [{"role": "user", "content": "Write 5 short questions a newcomer might ask that this note answers. "
                          "Use plain everyday wording, not the note's own jargon or title words. One question per line, no numbering."
                          "\n\nTitle: " + title + "\n\n" + text[:2500]}]}
    req = urllib.request.Request(EMB_URL.format(port=port).replace("/v1/embeddings", "/v1/chat/completions"),
                                 json.dumps(body).encode(), {"Content-Type": "application/json"})
    out = json.load(urllib.request.urlopen(req, timeout=180))["choices"][0]["message"]["content"]
    return [q.strip(" -*0123456789.") for q in out.splitlines() if len(q.strip()) > 12][:5]


def d2q_for(changed, port):
    """{rel: [questions]} for changed notes in D2Q_VAULTS, from the sha-keyed cache or the local model."""
    import concurrent.futures as cf
    cache = turso.connect(D2Q_CACHE)
    cache.execute("create table if not exists d2q(path text, sha text, questions text, primary key(path, sha))")
    want = [(rel, f, sha) for rel, f, sha in changed if rel.split("/", 1)[0] in D2Q_VAULTS]
    have = {}
    for rel, f, sha in want:
        row = cache.execute("select questions from d2q where path=? and sha=?", (rel, sha)).fetchone()
        if row:
            have[rel] = json.loads(row[0])
    todo = [(rel, f, sha) for rel, f, sha in want if rel not in have]

    def gen(x):
        rel, f, sha = x
        try:
            return rel, sha, d2q_questions(os.path.splitext(os.path.basename(rel))[0], open(f, errors="replace").read(), port)
        except Exception:
            return rel, sha, None
    failed = 0
    with cf.ThreadPoolExecutor(3) as ex:
        for rel, sha, qs in ex.map(gen, todo):
            if qs:
                have[rel] = qs
                cache.execute("insert or replace into d2q values(?,?,?)", (rel, sha, json.dumps(qs)))
            else:
                failed += 1
    cache.commit()
    cache.close()
    del cache
    print(f"doc2query: {len(want)} notes, {len(want) - len(todo)} cached, {len(todo) - failed} generated, {failed} failed", flush=True)
    if todo and failed > len(todo) // 2:
        sys.exit(f"doc2query: {failed}/{len(todo)} generations failed (is the local model up?); not publishing")
    return have


def prune_d2q(current_sha):
    """Bounded cache: drop question rows for notes that are gone or whose content changed."""
    if not os.path.exists(D2Q_CACHE):
        return
    c = turso.connect(D2Q_CACHE)
    rows = c.execute("select path, sha from d2q").fetchall()
    stale = [(p, h) for p, h in rows if current_sha.get(p) != h]
    for p, h in stale:
        c.execute("delete from d2q where path=? and sha=?", (p, h))
    c.commit(); c.close(); del c
    if stale:
        print(f"doc2query cache: pruned {len(stale)} stale rows", flush=True)


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
    cdir = os.path.join(HOME, ".cache", "habitat-context"); os.makedirs(cdir, exist_ok=True)
    local = os.path.join(cdir, "elig_check.py")
    with open(local, "w") as fh:
        fh.write(script)
    if ON_DESKTOP:  # the boundary is local here
        r = subprocess.run(["python3", local], input=json.dumps(rel_paths).encode(), capture_output=True, timeout=1800)
        if r.returncode != 0:
            sys.exit(f"boundary check failed rc={r.returncode}: {r.stderr.decode()[:300]}")
        return json.loads(r.stdout)
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
    # Never open the live file here: pyturso holds an exclusive lock until the object is released, which
    # would lock readers out for the whole build. The version lives in a sidecar written at publish.
    vfile = DB + ".version"
    prev_version = open(vfile).read().strip() if os.path.exists(vfile) else None
    if os.path.exists(DB) and prev_version == BUILD_VERSION:
        shutil.copy2(DB, work)
        if os.path.exists(DB + "-wal") and os.path.getsize(DB + "-wal") > 0:
            shutil.copy2(DB + "-wal", work + "-wal")
    con = turso.connect(work, experimental_features="index_method")
    cur = con.cursor()
    cur.execute("create table if not exists notes(path text primary key, vault text, sha text, mtime real)")
    cur.execute("create table if not exists sections(id integer primary key, vault text, path text, heading text,"
                " body text, sha text, jev_ok integer, emb blob, ctx text)")
    cur.execute("create table if not exists meta(key text primary key, value text)")
    cur.execute("drop table if exists prime_receipts")  # receipts live in receipts.db (single writer: habitat-ctx)
    cur.execute("delete from meta where key='live_probe'")
    con.commit()
    have = dict(cur.execute("select path, sha from notes").fetchall())
    files = [f for f in glob.glob(os.path.join(SNAP, "*.vault", "**", "*.md"), recursive=True)
             if not any(x in f for x in FENCED) and "/.obsidian/" not in f and "/.trash/" not in f
             and "/_sources/" not in f]  # raw doc captures duplicate "90 Source Docs" (measured noise)
    changed, seen, current_sha = [], set(), {}
    for f in files:
        rel = os.path.relpath(f, SNAP); seen.add(rel)
        sha = hashlib.sha256(open(f, "rb").read()).hexdigest()
        current_sha[rel] = sha
        if have.get(rel) != sha:
            changed.append((rel, f, sha))
    gone = [p for p in have if p not in seen]
    for p in gone:
        cur.execute("delete from sections where path=?", (p,)); cur.execute("delete from notes where path=?", (p,))
    print(f"notes={len(files)} changed={len(changed)} removed={len(gone)}", flush=True)
    elig = jev_eligible([c[0] for c in changed]) if changed else {}
    t0 = time.time(); rows = []
    d2q = d2q_for(changed, a.port) if not a.no_embed else {}
    for rel, f, sha in changed:
        vault = rel.split("/", 1)[0]
        cur.execute("delete from sections where path=?", (rel,))
        title = os.path.splitext(os.path.basename(rel))[0]
        text = open(f, errors="replace").read()
        rows.append([vault, rel, "[note]", note_card(title, text), sha, int(bool(elig.get(rel))), None,
                     f"{vault.replace('.vault', '')} > {title} / note summary"])
        for q in d2q.get(rel, []):
            rows.append([vault, rel, "[q] " + q[:80], q, sha, int(bool(elig.get(rel))), None,
                         f"{vault.replace('.vault', '')} > {title} / likely question"])
        for head, body in sections_of(text):
            ctx = f"{vault.replace('.vault', '')} > {title} / {head}"
            rows.append([vault, rel, head, body, sha, int(bool(elig.get(rel))), None, ctx])
        cur.execute("insert or replace into notes values(?,?,?,?)", (rel, vault, sha, os.path.getmtime(f)))
    embed_all = os.environ.get("HABITAT_EMBED_ALL") == "1"  # experiment switch: embed every vault
    todo = [r for r in rows if embed_all or r[0] in PRIORITY] if not a.no_embed else []
    if todo:
        vecs = embed([f"{r[7]}\n{r[3]}" for r in todo], a.port)
        for r, v in zip(todo, vecs):
            r[6] = json.dumps(v)
    for r in rows:
        if r[6] is None:
            cur.execute("insert into sections(vault,path,heading,body,sha,jev_ok,emb,ctx) values(?,?,?,?,?,?,NULL,?)", r[:6] + [r[7]])
        else:
            cur.execute("insert into sections(vault,path,heading,body,sha,jev_ok,emb,ctx) values(?,?,?,?,?,?,vector32(?),?)", r)
    con.commit()
    try:
        cur.execute(f"create index if not exists sections_fts on sections using fts({FTS_COLS})")
    except Exception as e:
        print("fts index:", e)
    prune_d2q(current_sha)
    snap = "live" if ON_DESKTOP else os.path.basename(os.path.realpath(os.path.join(SNAP, "..")))
    for k, v in {"built_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "snapshot": snap,
                 "emb_model": EMB_MODEL, "tursodb": "0.8.1", "build_version": BUILD_VERSION, "fts_cols": FTS_COLS}.items():
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
    with open(DB + ".version.tmp", "w") as fh:
        fh.write(BUILD_VERSION + "\n")
    os.replace(DB + ".version.tmp", DB + ".version")
    # A WAL beside the live name belongs to the previous generation (the live file has no writer), and
    # would be replayed over the new file by the next opener. Remove it as part of publishing.
    for ext in ("-wal", "-shm"):
        if os.path.exists(DB + ext):
            os.remove(DB + ext)
    print(f"sections={n} embedded={e} jev_ok={j} new_rows={len(rows)} embedded_now={len(todo)} in {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
