# Phone (1) RAM audio debugging and log recovery

These two host build targets produce separate images for `fastboot boot`:

| Target | Purpose |
|---|---|
| `make spacewar-audio-debug` | Package tone, bounded app playback or explicit continuous playback, retaining working Wi-Fi and native battery reporting |
| `make spacewar-log-recovery` | Package a tiny static PID1 that exports the newest or one exact saved audio log over loader USB |

The build targets do not contact a phone. They refuse existing output files and
leave the installed menu, boot partitions, normal userland images and Android
userdata alone. This requires the already installed Spacewar system/data layout
and its matching private install map. It is not a new installation method.

## Build the two images

Supply audited local inputs; proprietary firmware, images and install maps stay
outside source control. The kernel must have the exact 19-driver audio profile,
with its matching version file and successful build manifest. The normal audio
RAM disk must include the bounded `audio_health` capture job.

The default `AUDIO_DEBUG_MODE=tone` uses `sound=3`. To test the Darwin app-audio
path, set `AUDIO_DEBUG_MODE=playback` and provide a kernel whose manifest includes
`"spacewar_playback_diagnostic_max_seconds": 3`. That kernel must implement the
one-shot amplifier lease: at most three seconds from its first successful
enable, with no restart after silence or volume changes. This is a short
diagnostic, not continuous music playback. An older manifest is refused.

`AUDIO_DEBUG_MODE=continuous` adds the explicit `sound_cont=1` flag to `sound=4`.
It removes the three-second output deadline while retaining native fault checks,
the fixed `/32` PCM ceiling, high-pass/power guard, mute handling and power-down
after three seconds of silence. Playback can resume when new audio arrives.
The matching kernel manifest must additionally attest
`"spacewar_continuous_playback_flag": 1`. This low-output path does not establish
stock SpeakerBoost calibration or feedback protection. Battery and Wi-Fi stay
enabled. With `sound=4` and no flag, the kernel keeps the bounded playback policy.
Rebuild both loader and kernel with the matching shared service policy. Packaging
executes the production loader argument check on the full command line and
refuses a continuous-playback loader without its `sound_cont` policy token or
a kernel built against a different service-policy header.

```sh
make spacewar-audio-debug \
  AUDIO_DEBUG_OUT=build/audio-debug-new \
  AUDIO_DEBUG_KERNEL=/private/kernel \
  AUDIO_DEBUG_KERNEL_VERSION_FILE=/private/kernel.ver \
  AUDIO_DEBUG_KERNEL_MANIFEST=/private/manifest.json \
  AUDIO_DEBUG_INSTALL_MAP=/private/install-persist.map \
  AUDIO_DEBUG_LOADER=/private/loader.efi \
  AUDIO_DEBUG_UBOOT=/private/u-boot.bin \
  AUDIO_DEBUG_DTB=/private/spacewar.dtb \
  AUDIO_DEBUG_RAMDISK=/private/audio-root.apfs

make spacewar-log-recovery \
  LOG_RECOVERY_OUT=build/log-recovery-new \
  LOG_RECOVERY_GPU_FW=/private/fw-gpu \
  LOG_RECOVERY_KERNEL=/private/kernel \
  LOG_RECOVERY_KERNEL_VERSION_FILE=/private/kernel.ver \
  LOG_RECOVERY_KERNEL_MANIFEST=/private/manifest.json \
  LOG_RECOVERY_INSTALL_MAP=/private/install-persist.map \
  LOG_RECOVERY_LOADER=/private/loader.efi \
  LOG_RECOVERY_UBOOT=/private/u-boot.bin \
  LOG_RECOVERY_DTB=/private/spacewar.dtb
```

`/private/...` above represents host paths supplied by the builder. Recovery
copies only the matching GPU firmware needed by the existing approved profile:
`a660_gmu.bin`, `a660_sqe.fw`, `a660_zap.mdt`, and `a660_zap.b02`. Its runtime
arguments disable ADSP, Wi-Fi, sound, haptics and the battery manager. Existing
CPU0 loader ownership, seven-core XNU, touch and installed-storage gates remain.

The recovery root bypasses normal init and launchd. It mounts `/private/var`
read-only and opens only a bounded regular `log/hakios-audio-XXXXXX` file. It
does not create files, restore a writable skeleton or clean temporary folders.

Set `LOG_RECOVERY_NAME=hakios-audio-Ab12cD` to select an exact capture basename
recorded by the diagnostic run. The six characters shown here are an example.
An absent exact name fails; it never falls back to another file. Without that
option, the helper chooses the most recently modified eligible capture.

## Boot and collect

Copy the generated images to the host that owns USB. With the phone in stock
fastboot, start the audio diagnostic:

```sh
fastboot boot build/audio-debug-new/spacewar-audio-debug.img
```

Select `HakiOS` in the RAM boot menu. Do the same after booting the recovery
image; its static helper then shows diagnostic text instead of starting iOS.

Record the `audio_health` basename from the early USB log. After the diagnostic,
return the phone to stock fastboot using the hardware keys, then boot recovery:

```sh
fastboot boot build/log-recovery-new/spacewar-log-recovery.img
python3 tools/log-recovery.py capture --fastboot fastboot \
  --name hakios-audio-Ab12cD --out build/audio-log-recovered-private
```

Use the actual expected name in `--name`; this also prevents accepting an older
capture from a stale catalog. `--fastboot` accepts the path to Windows
`fastboot.exe`, and `--serial` can select a transport explicitly. Run the
collector with Python and output paths native to the USB host; a Windows USB
host can run this standalone script with Windows Python after a WSL build. The collector
reads `oem log`/`get_staged` snapshots only. It neither boots nor stops the phone.
After successful collection, the operator can end recovery with the existing
`fastboot oem stop` command.

The helper repeatedly sends paced 768-byte chunks with sequence numbers,
offsets and CRC32, plus a total length and whole-file CRC. The collector retains
unique frames across overlapping snapshots, so a file larger than the loader's
ring does not require one complete snapshot. It exports a file only after all
checks pass. Files are limited to 2 MiB; host output uses private permissions and
includes a SHA-256 manifest. Logs and recovered files stay private.

Existing snapshots can also be decoded without a device:

```sh
python3 tools/log-recovery.py decode --out build/audio-decoded-private \
  build/audio-log-recovered-private/frames.private.log
```

## Dirty journal

Darwin's HFS driver refuses a read-only mount when its journal is dirty. The
default recovery image reports that refusal explicitly. A catalog that lacks
the requested exact capture is also an explicit failure.

If journal replay is needed, rebuild into a new directory with
`LOG_RECOVERY_REPLAY=1`, keeping the other inputs above. This separate variant
uses the ordinary HFS mount/replay path for the installed data-only volume and
immediately remounts it read-only before reading a capture. It can write journal
and mount metadata on that data volume. It performs no formatting, partition
changes, application startup or diagnostic-file writes. The manifest records the permitted mount policy; the frame header reports
whether a replay actually occurred.

## Validation limits

The exporter and collector have host tests, including malformed directory
records, exact-name/size/symlink guards, mount ordering, corruption rejection and
a 2 MiB transfer reconstructed across simulated fastboot snapshots. Packaging
checks the audited kernel, private map, boot-argument budget, recovery-root
manifest and XNUP/HKUB/Android image contents.

On Phone (1), the read-only image reached its static PID1 with stable loader
USB and explicitly refused a dirty HFS journal. The separate replay variant
then replayed the data-volume journal, remounted read-only and exported the
expected 130,887-byte audio capture. Sequence, chunk CRC and whole-file CRC
checks passed, and the recovered SHA-256 was recorded, with zero malformed frames. This hardware test fetched
the log over USB without booting Android or copying the full data partition.
The 2 MiB maximum remains covered by the host transfer test.

```sh
python3 tools/log-recovery-test.py
```
