# Spacewar scope: port Redwood's working features

Local evidence paths below refer to the original session; ignored `build/`,
`logs/`, `xnu-src/` and ROM inputs are not distributed with this source.


Touch hardware has passed; graphical input is the next validation. The active hardware queue contains **seven remaining targets**: GPU, loader-backed storage, audio, haptics, cameras, Wi-Fi and battery readout. Work on features that Redwood does not report working is deferred.

The upstream [Redwood status file](https://github.com/oosexgang/hakios-sm7325/blob/04fbfff933f52faf203a55aca9dd5680092e6ec5/device.conf) explicitly marks ten areas working: display, touch, buttons, CPU, GPU, storage, audio, haptics, cameras and Wi-Fi. Its current `main` status was checked on 2026-11-03 and agrees with the cached reference. The common [README's working-features table](https://github.com/oosexgang/hakios-xnu-kernel/blob/main/README.md#what-works) also explicitly includes battery-level reporting. Source paths, revisions and SHA-256 hashes are recorded in `build/spacewar-port-inventory/redwood-scope.json`.

Spacewar already has basic display, buttons, CPU and single-contact touch baselines. This leaves six new core areas from the ten-area list, plus bounded battery readout as the seventh. This count describes the port queue, not full parity in every subfeature: seven XNU cores are verified here, while frequency control and sustained load remain additional CPU work. Temperature readings are an already-working Spacewar bonus; the thermal shutdown path remains unverified.

| Priority | Included target | Existing Redwood behavior to reproduce | Spacewar work |
|---|---|---|---|
| GUI validation | Touch | Multitouch input | Single-contact kernel input passed; test GUI consumption next, with multitouch/edge calibration still pending. |
| 1 | GPU | Adreno OpenGL ES rendering | Pin the current ROM's firmware and port its power/clock/SMMU profile. Metal rendering is excluded. |
| 2 | Loader-backed storage | UEFI-mediated reads and retained data | Establish the matching loader's Block I/O/partition handoff first. Persistence follows a reviewed storage plan; no new phone write or partition operation is authorized by this document. Native UFS takeover is excluded. |
| 3 | Wi-Fi | Wireless association and networking | Port the WCN6750 board/firmware profile; networking is part of this target, not another remaining area. |
| 4 | Audio | Playback and microphone capture | Correct the ADSP40MiB bound and implement the Nothing NXP amplifier/codec routes. |
| 5 | Battery readout | Displayed battery level and available status readings | Carry alongside ADSP; validate Nothing's property mapping and units. Charging control is excluded. |
| 6 | Haptics | Motor feedback | Implement the actual PM8350B backend; the Redwood Awinic backend is incompatible. |
| 7 | Cameras | Camera application capture | Add Nothing sensor, power and PHY profiles. |

The priority order after touch is a proposal; independent offline firmware/profile work can proceed concurrently. These are existing reference capabilities that still need Spacewar verification, not working-hardware claims for this phone. The [full hardware inventory](spacewar-port-inventory.md) preserves the concrete DT/ROM differences.

| Deferred feature | Reference evidence | Scope decision |
|---|---|---|
| Bluetooth | Main README says unavailable; device status has only a pin survey. | No Bluetooth stack or dedicated bring-up work. |
| Cellular registration/data | Partial modem initialization is reported; network registration is absent. | No modem service, SIM or IPA milestone in the active queue. |
| Charging under XNU | Explicitly unavailable. | Battery reporting only; no charger-control port. |
| Metal rendering | Objects/buffers exist, drawing is unfinished. | OpenGL ES GPU target only. |
| Torch | Device status is partial; broad working table does not claim it. | Defer flashlight functionality. |
| Native XNU USB | Reference does not establish native-controller support; working fastboot belongs to the UEFI loader. | Preserve loader USB, defer a new native USB driver. |
| General motion/light/proximity sensors | No successful reference feature claim found. | Unknown, not declared broken; defer sensor bring-up. |
| Native UFS driver | Source exists, but reference's supported storage claim names loader-backed operation. | Use the established loader-backed target; do not add native ownership work. |
| Apple online services | Explicitly unsupported in the common README. | Outside this port queue. |

The earlier 14-area inventory was a map of all unverified planning categories. It is superseded as a work count. Its broad BT/torch/native-storage survey proposal is also superseded: any future batched probe must directly serve one of the seven remaining hardware targets or the working touch integration, preserve the working baseline and avoid introducing a deferred feature.
