<!-- Made by tools/docs-gen.py from devices/*/device.conf and socs/*/soc.conf. Do not edit: change that, then run tools/docs-gen.py. -->


# Devices

Every device with a repository, and how far each part of it works. A device of yours: [Port a device](../porting/index.md).

| Device | SoC | display | touch | buttons | cpu | gpu | storage | audio | network | thermal |
|---|---|---|---|---|---|---|---|---|---|---|
| [QEMU virt (aarch64)](qemu.md) | `none` | yes | yes | yes |  | no | yes | yes | yes |  |
| [Nothing Phone (1)](spacewar.md) | `sc7280` | yes | yes | yes | yes | yes |  |  |  | yes |

An empty cell is a part nobody has looked at on that device yet.

## SoC families

| SoC | What | |
|---|---|---|
| `sc7280` | sc7280 | carried by spacewar (`devices/spacewar/soc/`) |
