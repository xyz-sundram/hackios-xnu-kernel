/* Bounded regressions for CoreAnimation's pipeline labels and command-buffer
 * scheduling. GPU work is confined to private test textures. This does not
 * test scanout or the kernel's IOSurface fence publication. */
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <dispatch/dispatch.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdatomic.h>
#include <string.h>
#include <unistd.h>

/* backboardd supplies this Metal SPI queue; exercise the same delivery path. */
@interface NSObject (HakiosSchedulingCheck)
- (void)setCompletionQueue:(dispatch_queue_t)queue;
@end

static void watchdog(int signal_number)
{
    static const char message[] = "ca-scheduling: FAIL 45-second diagnostic watchdog\n";
    (void)signal_number;
    (void)write(STDOUT_FILENO, message, sizeof(message) - 1);
    _exit(1);
}

static void require(BOOL ok, const char *name, const char *what)
{
    if (!ok) {
        printf("ca-scheduling: FAIL %s: %s\n", name, what);
        /* A failing callback/wait must not leave launchd blocked in cleanup. */
        _exit(1);
    }
}

static void await(dispatch_semaphore_t semaphore, const char *name, const char *what)
{
    require(dispatch_semaphore_wait(semaphore,
            dispatch_time(DISPATCH_TIME_NOW, 5 * NSEC_PER_SEC)) == 0, name, what);
}

struct observations {
    _Atomic(unsigned) next, scheduled, completed, wrong_order;
    dispatch_semaphore_t scheduled_done, completed_done, waiter_started, waiter_done, completion_waiter_done;
};

static void scheduling_case(id<MTLDevice> device, BOOL supplied_queue, BOOL held,
                            BOOL late_scheduled, unsigned iteration)
{
    char name[96];
    snprintf(name, sizeof(name), "%s/%s/%s/%u", supplied_queue ? "supplied-queue" : "default-queue",
             held ? "held" : "straight", late_scheduled ? "late-handler-extension" : "precommit-handlers", iteration);
    id<MTLCommandQueue> queue = [device newCommandQueue];
    require(queue != nil, name, "command queue");
    dispatch_queue_t delivery = NULL;
    if (supplied_queue) {
        require([(id)queue respondsToSelector:@selector(setCompletionQueue:)], name, "completion queue SPI");
        delivery = dispatch_queue_create("hakios.metal.scheduling-check", DISPATCH_QUEUE_SERIAL);
        [(id)queue setCompletionQueue:delivery];
    }
    MTLTextureDescriptor *desc = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm
                                            width:32 height:32 mipmapped:NO];
    desc.usage = MTLTextureUsageRenderTarget;
    desc.storageMode = MTLStorageModeShared;
    id<MTLTexture> texture = [device newTextureWithDescriptor:desc];
    id<MTLSharedEvent> event = held ? [device newSharedEvent] : nil;
    id<MTLCommandBuffer> cb = [[queue commandBuffer] retain];
    require(texture != nil && cb != nil && (!held || event != nil), name, "texture/buffer/event");

    struct observations observed = {0};
    struct observations *at = &observed;
    observed.scheduled_done = dispatch_semaphore_create(0);
    observed.completed_done = dispatch_semaphore_create(0);
    observed.waiter_started = dispatch_semaphore_create(0);
    observed.waiter_done = dispatch_semaphore_create(0);
    observed.completion_waiter_done = dispatch_semaphore_create(0);
    MTLCommandBufferHandler scheduled = ^(__unused id<MTLCommandBuffer> buffer) {
        if (atomic_fetch_add(&at->next, 1) != 0) atomic_fetch_add(&at->wrong_order, 1);
        /* Exercise the promise that waitUntilScheduled includes handler execution. */
        usleep(10000);
        atomic_fetch_add(&at->scheduled, 1);
        dispatch_semaphore_signal(at->scheduled_done);
    };
    if (!late_scheduled) [cb addScheduledHandler:scheduled];
    [cb addCompletedHandler:^(__unused id<MTLCommandBuffer> buffer) {
        if (atomic_fetch_add(&at->next, 1) != 1 || atomic_load(&at->scheduled) != 1)
            atomic_fetch_add(&at->wrong_order, 1);
        usleep(10000);
        atomic_fetch_add(&at->completed, 1);
        dispatch_semaphore_signal(at->completed_done);
    }];
    if (held) [cb encodeWaitForEvent:event value:1];
    MTLRenderPassDescriptor *pass = [MTLRenderPassDescriptor renderPassDescriptor];
    pass.colorAttachments[0].texture = texture;
    pass.colorAttachments[0].loadAction = MTLLoadActionClear;
    pass.colorAttachments[0].storeAction = MTLStoreActionStore;
    BOOL green = (iteration & 1) != 0;
    pass.colorAttachments[0].clearColor = MTLClearColorMake(green ? 0 : 1, green ? 1 : 0, 0, 1);
    id<MTLRenderCommandEncoder> encoder = [cb renderCommandEncoderWithDescriptor:pass];
    require(encoder != nil, name, "clear encoder");
    [encoder endEncoding];
    [cb commit];
    /* Registration after commit is a supported plug-in extension, tested only
     * while its event still prevents scheduling. Normal cases register first. */
    if (late_scheduled) [cb addScheduledHandler:scheduled];
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_DEFAULT, 0), ^{
        @autoreleasepool {
            dispatch_semaphore_signal(at->waiter_started);
            [cb waitUntilScheduled];
            dispatch_semaphore_signal(at->waiter_done);
        }
    });
    await(observed.waiter_started, name, "scheduling waiter started");
    if (held) {
        require(dispatch_semaphore_wait(observed.waiter_done,
                dispatch_time(DISPATCH_TIME_NOW, 50 * NSEC_PER_MSEC)) != 0,
                name, "waitUntilScheduled returned before event release");
        if (delivery != NULL) dispatch_sync(delivery, ^{});
        require(atomic_load(&observed.scheduled) == 0 && atomic_load(&observed.completed) == 0,
                name, "handler ran before event release");
        event.signaledValue = 1;
    }
    await(observed.waiter_done, name, "waitUntilScheduled returned");
    /* Do not query cb.status here: that API itself drains deferred recording
     * and could hide a missing waitUntilScheduled barrier. */
    require(atomic_load(&observed.scheduled) == 1, name, "scheduled handler finished before wait returned");
    await(observed.scheduled_done, name, "scheduled callback");
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_DEFAULT, 0), ^{
        @autoreleasepool {
            [cb waitUntilCompleted];
            dispatch_semaphore_signal(at->completion_waiter_done);
        }
    });
    await(observed.completion_waiter_done, name, "waitUntilCompleted returned");
    require(atomic_load(&observed.completed) == 1, name, "completed handler finished before wait returned");
    await(observed.completed_done, name, "completed callback");
    if (delivery != NULL) dispatch_sync(delivery, ^{});
    require(cb.status == MTLCommandBufferStatusCompleted && cb.error == nil, name, "successful GPU completion");
    require(atomic_load(&observed.scheduled) == 1 && atomic_load(&observed.completed) == 1 &&
            atomic_load(&observed.next) == 2 && atomic_load(&observed.wrong_order) == 0,
            name, "exactly-once scheduled-before-completed callbacks");
    unsigned char pixels[32 * 32 * 4];
    memset(pixels, 0xa5, sizeof(pixels));
    [texture getBytes:pixels bytesPerRow:32 * 4 fromRegion:MTLRegionMake2D(0, 0, 32, 32) mipmapLevel:0];
    for (unsigned pixel = 0; pixel < 32 * 32; pixel++) {
        require(pixels[4 * pixel] == 0 && pixels[4 * pixel + 1] == (green ? 255 : 0) &&
                pixels[4 * pixel + 2] == (green ? 0 : 255) && pixels[4 * pixel + 3] == 255,
                name, "1024-pixel GPU clear readback");
    }
    printf("ca-scheduling: PASS %s callbacks, waits, 1024-pixel clear\n", name);
    [cb release]; [event release]; [texture release]; [queue release];
    if (delivery != NULL) dispatch_release(delivery);
    dispatch_release(observed.scheduled_done);
    dispatch_release(observed.completed_done);
    dispatch_release(observed.waiter_started);
    dispatch_release(observed.waiter_done);
    dispatch_release(observed.completion_waiter_done);
}

int main(void)
{
    setbuf(stdout, NULL);
    signal(SIGALRM, watchdog);
    alarm(45);
    /* The native plug-in exposes its device only to the window server.
     * This standalone diagnostic uses that same initialization path. */
    setprogname("backboardd");
    @autoreleasepool {
        id<MTLDevice> device = MTLCreateSystemDefaultDevice();
        NSError *error = nil;
        id<MTLLibrary> library = [device newLibraryWithURL:[NSURL fileURLWithPath:
            @"/redwood/share/ca-share/QuartzCore-default.metallib"] error:&error];
        if (device == nil || library == nil) {
            printf("ca-pipeline: FAIL initialization: %s\n", [[error description] UTF8String] ?: "no native device");
            return 1;
        }
        unsigned failures = 0;
        NSArray *pipeline_labels = @[
            @"com.apple.coreanimation.draw.PBGRAXm_TcimA2Xhf_IsrcM1",
            @"PBGRAXm_TcimA2Xhf_IsrcM1",
            @"PBGRAXm_TcimA2Xhf_IsrcM1",
            /* Runtime fragments absent from the packaged library must take
             * upstream's nearest compatible vertex/fragment pair. Exercise
             * full and bare descriptor labels with a contradictory FS label. */
            @"com.apple.coreanimation.draw.PBGRAXm_TimgA2S1Xhf_Ixrg",
            @"PBGRAXm_TimgA2S1Xhf_Ixrg",
            @"com.apple.coreanimation.draw.PBGRAXm_TimgBdsoA2Xhfn_Isrc",
            @"PBGRAXm_TimgBdsoA2Xhfn_Isrc"
        ];
        for (unsigned test = 0; test < [pipeline_labels count]; test++) {
            id<MTLFunction> vs = [library newFunctionWithName:@"fixed_vert_lph_spc"];
            id<MTLFunction> fs = [library newFunctionWithName:@"fixed_frag_lph_cph"];
            /* Functions may be shared/relabelled across descriptors; the
             * descriptor's known pair must decide both stage variants. */
            [vs setLabel:test == 2 ? @"com.apple.coreanimation.vert.VfxXh" : nil];
            [fs setLabel:@"com.apple.coreanimation.frag.TcimA2Xhf_IsrcM1"];
            MTLRenderPipelineDescriptor *desc = [[MTLRenderPipelineDescriptor alloc] init];
            desc.label = pipeline_labels[test];
            desc.vertexFunction = vs;
            desc.fragmentFunction = fs;
            desc.colorAttachments[0].pixelFormat = MTLPixelFormatBGRA8Unorm;
            error = nil;
            id<MTLRenderPipelineState> state = [device newRenderPipelineStateWithDescriptor:desc error:&error];
            printf("ca-pipeline: %s case=%u label=%s reason=%s\n", state ? "PASS" : "FAIL", test,
                   [desc.label UTF8String], error ? [[error description] UTF8String] : "none");
            failures += state == nil;
            [state release]; [desc release]; [vs release]; [fs release];
        }
        printf("ca-pipeline: complete failures=%u\n", failures);
        for (unsigned supplied_queue = 0; supplied_queue < 2; supplied_queue++) {
            for (unsigned iteration = 0; iteration < 4; iteration++) {
                scheduling_case(device, supplied_queue, NO, NO, iteration);
                scheduling_case(device, supplied_queue, YES, NO, iteration);
            }
            scheduling_case(device, supplied_queue, YES, YES, 0);
        }
        puts("ca-scheduling: PASS 18 clear/readback cases; scanout fence publication not tested");
        [library release]; [device release];
        alarm(0);
        return failures ? 1 : 0;
    }
}
