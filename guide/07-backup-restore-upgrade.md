# Backup, restore and upgrade

Set `$KB` / `KB` and `$BACKUPS` / `BACKUPS` first ([4.2](04-first-steps.md#42-name-your-knowledge-base-once-per-window)).

## 7.1 Back up — weekly, and before anything big

```
rkb backup --data-root "$KB" --target "$BACKUPS"
```

This creates a new folder `backups/rkb-backup-<date-time>`, whose path is printed as `backup_dir`. **Verify it at once:**

```
rkb verify-backup --backup <BACKUP-FOLDER>
```

`"valid":true` means the backup can be restored.

**Copy your backups to an external drive or network storage as well.** A backup on the same disk is lost with the disk.

## 7.2 Restore

A restore always goes into a **new** folder; your current KB is never overwritten.

```
rkb restore --backup <BACKUP-FOLDER> --data-root <NEW-KB-FOLDER>
rkb doctor --data-root <NEW-KB-FOLDER> --full
```

When the restored KB looks right, set `$KB` / `KB` to it and keep working.

## 7.3 Update SciVetra to a new version

1. **Back up** every KB (7.1).
2. **Get the new version:**
   - **git:** run `git pull` inside `scivetra-kb`.
   - **ZIP:** rename the old `scivetra-kb` folder to `scivetra-kb-old`, then unzip the new version as `scivetra-kb` ([step 3.2](03-download-and-install.md#32-put-it-in-the-right-place)).
3. **Run the installer again:** 🪟 `1-INSTALL.cmd`, 🍎🐧 `bash 1-install.sh`.
4. **Upgrade any KB that needs it.** If a command reports **`KB_UPGRADE_REQUIRED`**, run:

   ```
   rkb upgrade --data-root "$KB" --backup-target "$BACKUPS"
   rkb doctor --data-root "$KB" --full
   ```

   The upgrade **backs up first**, upgrades a copy, verifies that every earlier record is byte-identical, and only then swaps the copy in.

## 7.4 Go back after an upgrade

The upgrade keeps the original database as `<KB>/db/kb.sqlite.pre-<number>-<date-time>`. The safest way back:
1. restore the backup the upgrade created into a new folder (7.2);
2. use the previous SciVetra version (`scivetra-kb-old`) with that folder.

## 7.5 A simple routine that works

| When | What |
|---|---|
| Start of each session | `rkb doctor --data-root "$KB"` |
| End of each week | `rkb backup …`, then `rkb verify-backup …`, then copy the backup to an external drive |
| Once a month | `rkb doctor --data-root "$KB" --full`, and a practice restore into a new folder |
| Before an upgrade, a bulk import or a rights change | `rkb backup …` |
