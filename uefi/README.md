# UEFI ports: redwood and spacewar

A port of [Project Silicium / Mu-Silicium](https://github.com/Project-Silicium/Mu-Silicium) to the POCO X5 Pro 5G
(redwood). The device is not upstream; the port follows `lisa` (the same SoC package, `KodiakPkg`).

You do not need to rebuild the UEFI to run HackiOS -- a ready image is in `prebuilt/uefi`, and `mkxnuimg.py`
takes it by itself.

What is here:

- `BASE` -- the Mu-Silicium commit the port is made on;
- `mu-silicium.patch` -- changes to existing files:
  `BootShim/BootShim.S` (copies our "XNUP" container, which follows the UEFI image, to 0xF0000000),
  `BootManagerLib` (starts the XNU loader from the container instead of the boot menu);
- `files/` -- platform packages and resources. `Platforms/Xiaomi/redwoodPkg` and `Binaries/redwood` are the
  tested POCO X5 Pro port. `Platforms/Nothing/spacewarPkg` and `Binaries/spacewar` are the initial Nothing Phone (1)
  adaptation, using its `xbl_a` extraction and live device tree. The Spacewar driver list and memory/config maps
  are adapted from its own firmware; the port still needs a Mu-Silicium build and on-device validation.
- `Silicon/.../BootManagerLib/XnuLoader.[ch]` and `mu-silicium.patch` implement the XNUP handoff for both targets:
  the shim copies the payload to `PAYLOAD_BASE`, then BootManager starts the XNU loader instead of the menu.

## Building

Packages as in Mu-Silicium's `setup_env.sh` (on Arch: `git base-devel lld nasm clang llvm aarch64-linux-gnu-gcc
python python-pip python-virtualenv`).

```sh
uefi/apply.sh                                   # clones Mu-Silicium (with submodules, ~3 GB) and applies the port
python3 -m venv venv && venv/bin/pip install -r Mu-Silicium/pip-requirements.txt
cd Mu-Silicium
source ../venv/bin/activate
python3 build_uefi.py -d redwood -r RELEASE     # -> Build/redwoodPkg/RELEASE_CLANGPDB/FV/SILICIUM_UEFI.fd
python3 build_uefi.py -d spacewar -r RELEASE    # -> Build/spacewarPkg/RELEASE_CLANGPDB/FV/SILICIUM_UEFI.fd
```

A pitfall: the `mu_nasm` dependency is installed through NuGet and needs Mono. To do without Mono, put the
system nasm in its place:

```sh
D=Mu_Basecore/BaseTools/Bin/mu_nasm_extdep
mkdir -p $D/Linux-x86-64
echo 'version: 20016.1.1' > $D/extdep_state.yaml
ln -s /usr/bin/nasm /usr/bin/ndisasm $D/Linux-x86-64/
```

After a build, `mkxnuimg.py` (called by `./xl enter` and `common/build.sh`) takes the UEFI from `Mu-Silicium/`,
and `tools/update-prebuilt.sh` refreshes `prebuilt/uefi`.

`uefi/export.sh` is the reverse: save the changes made in `Mu-Silicium/` back here.

The Spacewar Release UEFI has now been built successfully in the local WSL checkout. The initial image, built
with unpatched Spacewar XBL modules, reset to Android during RAM boot. The patched rebuild has not yet been
tested on-device. For current status and limitations, see `docs/PORTING.md`.
