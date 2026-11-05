// Exercise the production queue worker's ownership, argument copies and order.
// The same program runs against the host fake GPU and the phone's real GPU.
#include "hakios-metal/hakios_metal.h"
#include <array>
#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <cstring>

static void require(bool ok, const char *what) {
    if (!ok) {
        std::fprintf(stderr, "deferred-check: FAIL %s: %s\n", what, hakios_metal_error());
        std::exit(1);
    }
}
struct Completion { std::atomic<unsigned> *next; unsigned expected; };
static void completed(hakios_metal_cb *cb, void *opaque) {
    auto *c = static_cast<Completion *>(opaque);
    require(hakios_metal_cb_status(cb) == HAKIOS_METAL_COMPLETED, "callback status");
    require(c->next->fetch_add(1) == c->expected, "callback order");
}
int main() {
    setbuf(stdout, nullptr);
#ifdef __APPLE__
    auto *d = hakios_metal_open();
#else
    auto *d = hakios_metal_open_fake();
#endif
    require(d != nullptr, "device");
    auto *q = hakios_metal_queue_new(d);
    require(q != nullptr, "queue");
    hakios_metal_queue_set_deferred(q, 1);
    auto *target = hakios_metal_texture_new(d, 2, 70, 64, 64, 4);
    auto *temporary = hakios_metal_texture_new(d, 2, 70, 64, 64, 4);
    require(target && temporary, "targets");
    std::array<hakios_metal_cb *, 24> buffers{};
    std::array<Completion, 24> completions{};
    std::atomic<unsigned> next{0};
    for (unsigned i = 0; i < buffers.size(); i++) {
        hakios_metal_render_pass pass{};
        pass.color0 = target;
        pass.load_action = 2;
        pass.store_action = 1;
        pass.clear_color[i % 2 ? 1 : 0] = 1;
        pass.clear_color[3] = 1;
        pass.more[0].texture = temporary;
        pass.more[0].load_action = 2;
        pass.more[0].store_action = 1;
        auto *cb = buffers[i] = hakios_metal_cb_new(q);
        require(cb != nullptr, "command buffer");
        require(hakios_metal_cb_clear(cb, &pass) == 0, "post clear");
        // The queued call must not depend on this stack object afterwards.
        std::memset(&pass, 0, sizeof(pass));
        completions[i] = Completion{&next, i};
        hakios_metal_cb_commit(cb, completed, &completions[i]);
    }
    // The worker and GPU still need this resource after its public handle dies.
    hakios_metal_texture_free(temporary);
    hakios_metal_cb_wait(buffers.back());
    require(next.load() == buffers.size(), "all callbacks");
    for (auto *cb : buffers) {
        require(hakios_metal_cb_status(cb) == HAKIOS_METAL_COMPLETED, "completion");
        hakios_metal_cb_free(cb);
    }
    std::array<unsigned char, 64 * 64 * 4> pixels{};
    require(hakios_metal_texture_read(target, 0, 0, 64, 64, pixels.data(), 64 * 4) == 0, "readback");
    for (unsigned i = 0; i < 64 * 64; i++) {
        require(pixels[i*4] == 0 && pixels[i*4+1] == 255 && pixels[i*4+2] == 0 && pixels[i*4+3] == 255,
                "last clear wins at every pixel");
    }
    hakios_metal_texture_free(target);
    hakios_metal_queue_free(q);
    hakios_metal_close(d);
    std::puts("deferred-check: PASS 24 ordered callbacks, copied arguments, retained targets, 4096 matching pixels");
    return 0;
}
