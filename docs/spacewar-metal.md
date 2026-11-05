# Native Metal on Nothing Phone (1)

Use the frozen working U-Boot and the resident XNU loader. The direct
[hakios-metal](https://github.com/oosexgang/hakios-metal) core is pinned to
`d7495f43485176785a03a77c4fb82cfff0204043`. This work preserves the successful
brightness framebuffer, seven XNU CPUs, physical touch coordination and the
installed HFS data layout. Redwood's generic Metal runner remains unsuitable
for Spacewar.

The core passed 261 host tests with GCC and 261 with Clang/AddressSanitizer.
Those test command packets, compiler interfaces and memory ownership against a
simulated GPU. On the Spacewar itself, the stage6 probe passed 43 checks and
the complete stage9 probe passed 282 checks, with zero failures. Physical GPU
readbacks verified compute, texture sampling, drawing, blending, multisampling
and IOSurface access. AIR shaders compiled and linked on the phone also passed.
The probes reported no GPU faults. Private logs and their SHA-256 checksums are
under `build/spacewar-metal-20261105/phone/`. The v2 UI test compiled and drew
CoreAnimation pipelines, with one stable backboardd process and no plug-in
refusals. The user confirmed that brightness still worked but reported visual
glitches. A live framebuffer capture showed the verbose text console drawing
over the UI. The v3 UI profiles omit `-v`, selecting the loader's existing
graphics-console mode while retaining live USB logging. This removed the text
band, but a white status-bar gradient remained. The otherwise identical GLES
control did not have the white bar; disabling Metal's adaptive GMEM policy did
not remove it.

The v4 profile also rebuilds `libhakios.sb.dylib`: the reused brightness root's
older binary lacked the source's existing Metal-only legibility adaptation.
This removes `darkenSourceOver` from that gradient and draws it as ordinary
source-over. The user confirmed the white bar disappeared. This is a scoped
visual workaround; the underlying filter shader has not been corrected.

App interaction then exposed a backboardd restart on pipeline
`PBGRAXm_TcimA2Xhf_IsrcM1`: its fragment shader read VAR1 with a vertex shader
that did not write it. The exact iOS16.5 pair passed fresh and cached compilation
in a standalone on-phone diagnostic, including a VS output at VAR1. The v6
plug-in normalizes bare CoreAnimation draw specs and gives the pipeline's known
pair precedence over individual function labels. Its diagnostic root checks
fully qualified, bare and conflicting-function-label descriptors through the
actual Metal framework before launching the UI. UI stability and combined
battery/Metal validation remain pending.

Battery percentage and brightness were confirmed working together by the user
in the separate `battery-brightness` RAM revision. Enabling the ADSP battery
transport disconnects the resident firmware's USB interface. This does not
establish the cause of that USB loss. Consequently the GPU diagnostics use a
DSP-idle profile; the combined `metal-services` profile enables measured battery
reporting again. Do not present a diagnostic profile as the normal final build.

## Build inputs

`tools/build-spacewar-metal.py` builds a local checkout with its AIR compiler
against the existing Mesa 25.0.7 cross build. It builds `libair.a`, which the GLES
build does not normally need. It uses checked-in shader reference fixtures
rather than regenerating them through the upstream author's host-only Mesa
paths. The host tests still execute the actual production core.

The two local upstream patches are under `tools/metal/`:

- Clang 14 cannot evaluate `__builtin_signbit` in the C++17 constexpr encoder.
  Its branch uses `__builtin_bit_cast` to obtain the sign bit, preserving
  negative zero. The existing float encoder tests pass under Clang/ASan.
- `--max-stage 1..9` bounds the probe at existing stage boundaries. Packet and
  shader code are unchanged. The default retains the upstream full probe.

The CA plug-in compiles the optional Metal-on-Vulkan branch only when linked
with `HAKIOS_METAL_M2V`; native builds therefore have no unresolved imports from
that optional runtime. The M2V build recipe supplies the define when requested.

The kernel build adds the current GPU user-memory import implementation to the
previous compiled baseline. Its framebuffer is byte-identical to the successful
brightness revision. The loader accepts `metal=1` with
`ca_metallib=/redwood/ca.metallib` only beside the established GPU/CPU profile;
`metal_fps=1` is optional. Unknown paths, unsupported values, duplicates and
incomplete combinations are rejected by the actual loader policy tests.

## Compositor shaders

The existing iOS16.5 image contains QuartzCore's pipeline variants but lacks its
`default.metallib`. A newer QuartzCore library supplies helper functions while
`tools/ca-metallib.py` retains the 16.5 primary compositor variants. No newer
framework executable or OS is substituted.

The required file was extracted on the Linux/Windows PC directly from Apple's
iPadOS17.0 restore image, build `21A329`. Bounded HTTP range reads and the local
read-only UDIF/APFS readers obtained the file without downloading the full
7.68 GB archive. The original QuartzCore library is 8,288,448 bytes, SHA-256
`a830c03eb30942b109e2f48e70e9674d937d5a0685dec7c646d4603e02b186eb`.
The local converted library has 167 functions. These files stay in the ignored
build directory; the source URL, archive member, extracted path and checksum
are in `apple-ipados17/source.json` there.

The plug-in opens `/redwood/share/ca-share/QuartzCore-default.metallib`
directly for pipeline-library mirroring and helper compilation. The RAM root
must contain that canonical file as well as the short `/redwood/ca.metallib`
alias used in the boot arguments. The first UI test omitted the canonical
path: Metal opened its pipeline cache but backboardd aborted with
`MTLLibraryErrorDomain Code=6 "library not found"`. The corrected v2 package
installs the canonical file and uses a relative symlink for the short alias;
APFS checking and independent readback verify both.

The `kolinko/effort` file suggested during preparation was inspected separately:
its 79 functions are compute kernels, with no compositor vertex/fragment
functions. It is not used in the CA build.

## RAM test bundle

The isolated bundle is `build/spacewar-metal-20261105/debug-bundle`, also copied
to `C:\platform-tools\hakios-metal-20261105\debug-bundle` on the USB host.
`tools/spacewar-metal-package.py` reconstructs it from explicit audited inputs;
the corrected package is `debug-bundle-v3` beside the original bundle.
Each APFS root was checked by apfsck and read back independently; XNUP entries,
the HKUB container, the Android boot wrapper and argument budgets were checked.
The boot image retains the known U-Boot and DTB byte for byte. No partition has
been flashed by this workflow.

| Profile | Purpose | Battery |
|---|---|---|
| `probe` | Direct native copy/fill/fences, compute and AIR compiler through stage6; then existing GLES UI | DSP idle for live USB |
| `render-probe` | Direct native stages through off-screen rendering; then existing GLES UI | DSP idle for live USB |
| `metal-debug` | CoreAnimation through native Metal, with live USB logging | DSP idle for live USB |
| `metal-services` | Combined Metal UI with native brightness and measured battery reporting | Enabled |

Boot `loader-debug.img` from stock fastboot once, select HakiOS, then use
`spacewar-debug.py run PROFILE/PROFILE-debug.bin --out NEW_CAPTURE_DIRECTORY`.
The runner verifies idle/returned loader state before staging; stop a running
kernel orderly before the next payload. A completed capture leaves HakiOS
running. Hardware success must be recorded separately from build/audit success.
Use `--until 'probe: 0 failures'` for the complete GPU probe to finish capture
as soon as its final success marker arrives, without stopping the kernel.
