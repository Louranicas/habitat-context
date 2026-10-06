#!/usr/bin/env python3
"""Safety tests for habitat-ctx (run with the SDK venv python). Each test must be able to fail.

1. fts_terms() emits only [A-Za-z0-9_-] words: SQL metacharacters and FTS operators cannot reach the literal.
2. Injection attempts through ask() leave the context DB unchanged (row counts and schema hash).
3. A planted control: an unsanitised literal containing a quote really would break the query.
"""
import hashlib, importlib.machinery, importlib.util, os, shutil, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
loader = importlib.machinery.SourceFileLoader("hctx", os.path.join(ROOT, "habitat-ctx"))
spec = importlib.util.spec_from_loader("hctx", loader); m = importlib.util.module_from_spec(spec); loader.exec_module(m)
import turso
fails = 0
def ok(cond, name):
    global fails
    print(("PASS " if cond else "FAIL ") + name); fails += 0 if cond else 1

attacks = ["'; drop table sections; --", "backups' OR 1=1 --", 'x") ; delete from meta; --', "a'||(select 1)||'",
           "lavish* NOT body:x^9 \"phrase\"", "\x00null\x00byte", "'" * 50, "ünïcödé ' quote"]
for a in attacks:
    terms = m.fts_terms(a)
    ok(all(c.isalnum() or c in "_- " for c in terms), f"fts_terms sanitises {a[:28]!r} -> {terms[:30]!r}")

# Work on a copy (lesson: never test against the live DB)
tmp = tempfile.mkdtemp(); db = os.path.join(tmp, "h.db"); shutil.copy(m.DB, db)
def fingerprint(path):
    c = turso.connect(path, experimental_features="index_method")
    sch = hashlib.sha256("".join(r[0] or "" for r in c.execute("select sql from sqlite_master order by name")).encode()).hexdigest()
    n = (c.execute("select count(*) from sections").fetchone()[0], c.execute("select count(*) from meta").fetchone()[0])
    c.close(); return sch, n
before = fingerprint(db)
env = dict(os.environ, HABITAT_CTX_DB=db, HABITAT_CTX_RECEIPTS=os.path.join(tmp, "r.db"), HABITAT_CTX_ORIGIN="eval",
           HABITAT_CTX_NO_TUNNEL="1", HABITAT_CTX_EMB_URL="http://127.0.0.1:9/x")
for a in [x for x in attacks if "\x00" not in x]:  # argv cannot carry NUL (OS rule); NUL is covered above
    r = subprocess.run([os.path.join(ROOT, "habitat-ctx"), "ask", a, "-k", "2"], capture_output=True, text=True, env=env)
    ok(r.returncode == 0 and "Traceback" not in r.stderr, f"ask survives {a[:28]!r}")
ok(fingerprint(db) == before, "context DB unchanged after injection attempts (schema hash and row counts)")

# Planted control: an unsanitised quote does break a literal-built query, so the sanitiser is load-bearing
c = turso.connect(db, experimental_features="index_method")
try:
    c.execute("select count(*) from sections where fts_match(body, 'it's')").fetchall(); broke = False
except Exception:
    broke = True
c.close(); shutil.rmtree(tmp)
ok(broke, "planted control: a raw quote in the literal breaks the query (sanitiser is load-bearing)")
print(f"safety verdict={'PASS' if not fails else 'FAIL'} failures={fails}")
sys.exit(1 if fails else 0)
