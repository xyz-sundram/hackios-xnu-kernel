# Nothing Phone (1) on Windows / WSL2

Build in WSL2's Linux filesystem, then run Windows platform-tools against the phone.
Keep the IPSW extraction in Linux: some extracted filenames cannot be represented on NTFS.

1. Install Google platform-tools in `C:/platform-tools`.
2. Use WSL2 Ubuntu with Git, Python 3, make, clang/lld, 7-Zip 23+, lz4 and the
   Theos cross-toolchain/SDK. `make doctor` reports missing build tools.
3. Follow the [Phone (1) build recipe](../README.md#build-for-nothing-phone-1-spacewar).
4. Package with `python3 tools/spacewar-package.py --gpu-mhz 550 --out build/spacewar --copy-system`.
5. Copy the output to Windows platform-tools, put the phone into stock fastboot,
   and use [the PowerShell launcher](getting-started/phone.md#start).

The tested local bundle is `C:/platform-tools/final`; private firmware is separately
saved in `C:/platform-tools/private-firmware`. The PowerShell scripts handle
fastboot's normal stderr status lines without PowerShell 5.1 NativeCommandError.
Live logs are saved beside the images. Quiet mode retains debugging code; the
launcher has no scheduled cutoff. Ctrl+C stops collection, while `fastboot oem stop`
requests kernel return.

WSL does not normally own the phone's USB connection. Use Windows `fastboot.exe`
from Windows PowerShell; there is no need to attach the phone to WSL for this path.
The RAM boot does not install the system. See [dual boot](dual-boot.md) for the
separate installation port and its current validation status.

Upstream [Metal/Vulkan documentation](reference/metal.md) and
[Redwood measurements](reference/metal-measurements.md) are reference material.
Those graphics paths are untested on Phone (1); the generic Metal runner does not
replace the Spacewar launcher or installed dual-boot menu.
