# One container or one per drive

Choose between one container for all drives (simple) and one small worker container per drive (faster with several drives).

Every `makemkvcon` checks every drive it can see while it opens its disc, so with several drives in one container the drives slow each other down. There are two ways to run ripshelf:

| | Setup | Four discs scanned (four drives) |
|---|---|---|
| **Single container** (default) | [compose.yaml](../compose.yaml). Drives open their discs one at a time (at most 45 s each), then scan side by side. | 116 s |
| **One worker per drive** | [compose.per-drive.yaml](../compose.per-drive.yaml). A controller, plus one small worker container per drive that sees only that drive. | 77 to 83 s |

The gain is largest for DVDs. A DVD drive that took 51 s to scan alongside three others took 11 to 18 s on its own.

Both use the same image and the same data folders. Switching between them needs no re-import, because each drive keeps its id, its history, its settings and its MakeMKV data (`data/makemkv/<drive>`). The id comes from the drive's vendor, model and serial number, not the port or `/dev/srN`. If the numbers change (a drive moved to another port, or a controller added), only the `devices:` lines need updating. The `srN`/`sgN` numbers can change between boots when drives are added or moved, so check them (`ls /sys/class/block/sr*/device/scsi_generic`) after hardware changes.

## Which to use

- **One or two drives.** The single container is fine.
- **Several drives.** Use **one worker per drive**, so the drives stop slowing each other down. The cost is one line pair per drive in the compose file.

## Switching to one worker per drive

1. Stop the stack.
2. Replace its compose.yaml with [compose.per-drive.yaml](../compose.per-drive.yaml). Keep the same paths (`./data`, `./scratch`, the movies and TV folders, `./backups`). Change the marked lines, which are the user and group ids and one `drive-srN` block per drive with its `srN`/`sgN` pair.
3. Start it.
   - The controller writes a worker token into `data/makemkv/.worker-token` on first start, and the workers read it from there. There's nothing to type.
   - The drives come back as before, shown "on sr0", "on sr1" and so on.

To go back, use compose.yaml again. Nothing else changes.

## How it fits together

- **Shared folders.** Each worker mounts the controller's `data/makemkv` (so private data, firmware dumps and the key are where its MakeMKV looks for them) and `scratch` (so rips need no upload).
- **No published ports.** Workers talk to the controller over the compose network.
- **Everything works the same through workers.** That includes Settings → Import (it writes into `data/makemkv/<drive>`), disabling a drive, Eject and Close tray, Scan again, the Test buttons and the job logs.

