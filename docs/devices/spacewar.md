<!-- Made by tools/docs-gen.py from devices/spacewar/device.conf. Do not edit: change that, then run tools/docs-gen.py. -->


# Nothing Phone (1) (`spacewar`)

| | |
|---|---|
| Vendor | Nothing |
| SoC | `sc7280` |
| Configuration | `make spacewar_defconfig` |
| Maintainer | sundramxd |
| Repository | [`devices/spacewar/`](https://github.com/xyz-sundram/hackios-xnu-kernel/tree/staging/devices/spacewar) |

## What works

| Part | State | |
|---|---|---|
| display | works | quiet graphical home screen and wallpaper at1080x2400; user-confirmed smoother UI with hardware graphics |
| cpu | works | seven XNU cores7-1 with CPU0 USB; bounded EPSS requests1900/1862MHz for big/prime and request restoration exercised; extended stability untested |
| thermal | works | all 27 sensors polled with changing 39.5-43.0 C readings and user-space sysctls; 105 C stop remains untested |
| buttons | works | physical power/volume press and release plus graphical UI response confirmed with touch and GPU enabled |
| touch | works | physical single-contact taps/swipes/releases in graphical userland with live USB; M146 firmware ECC/runtime verified; multitouch and extended calibration untested |
| gpu | works | Adreno642L Mesa/OpenGL ES;315/450/550MHz driver levels;450/550MHz drawing/readback tests passed and550MHz quiet UI confirmed |

The state is what has been seen on this device, written down in `devices/spacewar/device.conf`; a part the table does not name has not been looked at.
