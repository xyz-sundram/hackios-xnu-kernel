# Upstream review: 4 November 2026

The Phone (1) fork integrates the 29 kernel commits through
[`87bf485`](https://github.com/oosexgang/hakios-xnu-kernel/commit/87bf4851bcc9cbb24fd79997820aa3661abe4ea5).
They principally update optional graphics, tooling and documentation. They do
not contain a Wi-Fi driver fix. Phone (1)'s firmware, seven-core CPU profile,
550 MHz GPU setting, touch and installed UFS/HFS+ layout remain device-specific.

The merge preserves the tested GLES library placement. The generic Metal
runner refuses Spacewar, and framebuffer scan-counter timing defaults off on
Spacewar. Optional M2V dependencies are built before linking its plug-in; DRM
sync-object waits propagate errors instead of claiming completion. Host checks
pass. Imported graphics still require a Phone (1) RAM test before deployment;
upstream Redwood performance measurements are not Phone (1) results.

[`hakios-metal` at `7025778`](https://github.com/oosexgang/hakios-metal/commit/7025778b8c0bbb1a99793f1be06474782d50cdaf)
imports the same window-server changes under different paths. Those changes
are integrated once through the kernel merge. Its native core additionally
contains compiler-keyed caches and deferred recording; an optional Metal build
must use a matching rebuilt core and plug-in. This review does not enable Metal
in the tested GLES image. Native Metal needs the AIR compiler archive; the
separate M2V route needs its pinned Rust/LLVM/Turnip prerequisites, described in
[the Metal reference](reference/metal.md).

The Redwood device pin advances to
[`af52dfb`](https://github.com/oosexgang/hakios-sm7325/commit/af52dfba33f91340bde68dbbe238ae583f9fd472),
whose sole new change is GPU/Metal status documentation. No newer Wi-Fi device
code was found. Its separate U-Boot branch has menu/slot-return changes; this
fork retains the already tested Nothing dual-boot implementation.

Wi-Fi implementation and phone-test results are tracked separately in
[Phone (1) Wi-Fi bring-up](spacewar-wifi.md).
