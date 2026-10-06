# External changes (not in any git repository)

Patch records for edits made outside version control on 2026-10-07, during production hardening. Each
live file keeps a `.bak-*` sibling holding its previous version. Revert with `patch -R` or by restoring that backup.

| Host | File | Record | Why |
|---|---|---|---|
| Laptop | `~/.local/bin/ssh-vault-sync` | `laptop-ssh-vault-sync.patch` | Falls back to native SSH (`desktop-native-tail`, port 2222) when Tailscale SSH asks for re-authentication, so vault sync does not stall unattended. Backup: `.bak-20261007-fallback`. |
| Desktop | `~/.local/bin/habitat-index-sync` | `desktop-habitat-index-sync.patch` | Bounded lock wait. An unbounded `flock` hung a run until systemd's 10-minute timeout. It now waits at most 120 s, then exits 75 with `LOCK_BUSY`. Backup: `.bak-20261007-flock`. |
| Desktop | `/mnt/storage-10tb/omarchy-rust-implementation/tools/curator/curator.py` | `desktop-curator-evidence.patch` | Adds `evidence` to `EXCLUDED`, because generated test receipts (about 4,560 files) pushed candidates past `MAX_FILES`. The bound itself is unchanged. Backup: `.bak-20261007-evidence`. |
| Desktop | `~/.local/bin/jev-axi` | (no diff: a restore) | Restored after a quoting accident on 2026-10-06 clobbered it. It is byte-identical to `~/.local/share/mise/installs/node/26.8.2/bin/jev-axi` (sha256 prefix `f72c711a42fd59a6`), and selftest gives 24/24. The clobbered 45-byte file is kept as `jev-axi.clobbered-20261006T2014`. |

Not done, needs Luke (sudo): in `/etc/security/pam_env.conf`, put `@{HOME}/.local/bin` first in `PATH DEFAULT` so SSH
commands resolve habitat wrappers before the mise shims.
