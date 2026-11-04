# Spacewar UEFI overlay

Apply to the existing Mu-Silicium tree with:

```sh
cp -r devices/spacewar/uefi/files/. Mu-Silicium/
```

`files/Platforms/Nothing/spacewarPkg/` carries two changes to Mu's Spacewar port:

- `Include/APRIORI.inc`: run RAM discovery after Env/CPU/timer/SMEM initialization,
  before the bulk of vendor drivers exhaust the 49 MiB initial DXE heap.
- `Library/MemoryMapLib/`: reserve HakiOS's 64 MiB XNUP region and all fixed memory
  carveouts from the supplied current-ROM DTB. Secure/no-map additions have
  resource HOBs without CPU mappings. Preserve the vendor CPU vector page.

The boot failure motivating the driver order change was repeated
`AllocatePoolPages` failures followed by missing architectural protocols and
`ASSERT [DxeCore] DxeMain.c(581)` with `Status = Not Found`.

See `docs/spacewar-bringup.md` in the common checkout for image-building commands,
test results and the earlier hardware checks. Spacewar's source overlay is now
kept in the common repository; the vendor modules and built FD remain ignored.
