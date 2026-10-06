# External changes (not in any git repository)

Patch records for edits made outside version control on 2026-10-07, during production hardening. Each
live file keeps a `.bak-*` sibling holding its previous version. Revert with `patch -R` or by restoring that backup.

| Host | File | Record | Why |
|---|---|---|---|
| Laptop | `~/.local/bin/ssh-vault-sync` | `laptop-ssh-vault-sync.patch` | Falls back to native SSH (`desktop-native-tail`, port 2222) when Tailscale SSH asks for re-authentication, so vault sync does not stall unattended. Backup: `.bak-20261007-fallback`. |
| Desktop | `~/.local/bin/habitat-index-sync` | `desktop-habitat-index-sync.patch` | Bounded lock wait. An unbounded `flock` hung a run until systemd's 10-minute timeout. It now waits at most 120 s, then exits 75 with `LOCK_BUSY`. Backup: `.bak-20261007-flock`. |
| Desktop | `/mnt/storage-10tb/omarchy-rust-implementation/tools/curator/curator.py` | `desktop-curator-evidence.patch` | Adds `evidence` to `EXCLUDED`, because generated test receipts (about 4,560 files) pushed candidates past `MAX_FILES`. The bound itself is unchanged. Backup: `.bak-20261007-evidence`. |
| Desktop | `~/.local/bin/habitat-index-sync` | `desktop-habitat-index-sync-v3fence.patch` | **v3 fence** (Luke, 2026-10-07: v3 "only if indicated for historical context", no earlier-version artifacts may colonise v4). v3 had supplied 24 of 40 index notes and 76% of the 5,775 links, while the v4 and herdr.habitat vaults were absent. The v3 vault is now out of `VAULTS`, and v4 and herdr.habitat are in. A `V3_FENCE` check fails the run if any v3 note is indexed or any v3 link resolves; this replaces the v3-only 22-stem count. `HABITAT_INDEX_DB` allows testing on a copy. Result: 18 notes and 1,448 links, none from v3. 16 links into v3 are kept unresolved, as historical references. Planted control: v3 re-added gives `V3_FENCE notes=24`, rc=1, nothing written. Backup: `.bak-20261007-v3fence`. |
| Desktop | `~/.local/bin/jev-axi` | (no diff: a restore) | Restored after a quoting accident on 2026-10-06 clobbered it. It is byte-identical to `~/.local/share/mise/installs/node/26.8.2/bin/jev-axi` (sha256 prefix `f72c711a42fd59a6`), and selftest gives 24/24. The clobbered 45-byte file is kept as `jev-axi.clobbered-20261006T2014`. |

Not done, needs Luke (sudo): in `/etc/security/pam_env.conf`, put `@{HOME}/.local/bin` first in `PATH DEFAULT` so SSH
commands resolve habitat wrappers before the mise shims.
