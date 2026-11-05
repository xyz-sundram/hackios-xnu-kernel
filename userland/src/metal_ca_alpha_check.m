/* Actual CoreAnimation icon/mask shaders, GPU draws and alpha readback.
 * This tests rendering into private diagnostic textures, not panel scanout.
 * --batch 1 serializes frames; --batch 8 queues independent frames legally.
 * --fresh recreates source textures; otherwise each slot is updated only
 * after its preceding command buffer completed. No in-flight CPU mutation. */
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

enum { SIDE = 64, IMAGE = 32, MAX_BATCH = 8, CASES = 9, STATES = 8 };
struct vertex { float position[4], uv0[2], uv1[2]; uint16_t color[4]; };
struct frame {
    id<MTLTexture> source, target[2];
    id<MTLCommandBuffer> cb;
    unsigned number;
};
static const char *names[CASES] = {
    "opaque-color0", "premultiplied-color0", "premultiplied-color1",
    "alpha-splat-color1", "color1-to-framebuffer-fetch-color0",
    "vertex-color-opacity-unity-mask", "vertex-color-opacity-drawn-mask",
    "feedback-overlap-two-draws", "feedback-overlap-one-draw"
};

static void watchdog(int signo)
{
    static const char message[] = "ca-alpha: FAIL 90-second diagnostic watchdog\n";
    (void)signo;
    (void)write(STDOUT_FILENO, message, sizeof(message) - 1);
    _exit(1);
}

static void require(BOOL ok, const char *reason)
{
    if (!ok) { printf("ca-alpha: FAIL %s\n", reason); _exit(1); }
}

static id<MTLTexture> texture(id<MTLDevice> device, unsigned side, BOOL target)
{
    MTLTextureDescriptor *d = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm
                                                                          width:side height:side mipmapped:NO];
    d.storageMode = MTLStorageModeShared;
    d.usage = MTLTextureUsageShaderRead | (target ? MTLTextureUsageRenderTarget : 0);
    id<MTLTexture> t = [device newTextureWithDescriptor:d];
    require(t != nil, "texture allocation");
    return t;
}

static id<MTLRenderPipelineState> pipeline(id<MTLDevice> device, id<MTLLibrary> library,
                                         NSString *label, unsigned output, BOOL blend)
{
    id<MTLFunction> vs = [library newFunctionWithName:@"fixed_vert_lph_spc"];
    id<MTLFunction> fs = [library newFunctionWithName:@"fixed_frag_lph_cph"];
    require(vs != nil && fs != nil, "production shader functions");
    MTLRenderPipelineDescriptor *d = [[MTLRenderPipelineDescriptor alloc] init];
    d.label = label; d.vertexFunction = vs; d.fragmentFunction = fs;
    /* The plug-in selects the real stage pair using the production label.
     * VfxU100Xh: attributes 0=float4 position, 1=float2 UV, 3=half4 color;
     * vertex buffer2=MVP, buffer3=texture scale/bias. VfxU110Xh also uses
     * attribute2 and the second float4 at buffer3+16 (not buffer4). */
    MTLVertexDescriptor *v = [MTLVertexDescriptor vertexDescriptor];
    v.attributes[0].format = MTLVertexFormatFloat4;
    v.attributes[0].offset = 0; v.attributes[0].bufferIndex = 0;
    v.attributes[1].format = MTLVertexFormatFloat2;
    v.attributes[1].offset = 16; v.attributes[1].bufferIndex = 0;
    v.attributes[2].format = MTLVertexFormatFloat2;
    v.attributes[2].offset = 24; v.attributes[2].bufferIndex = 0;
    v.attributes[3].format = MTLVertexFormatHalf4;
    v.attributes[3].offset = 32; v.attributes[3].bufferIndex = 0;
    v.layouts[0].stride = sizeof(struct vertex);
    v.layouts[0].stepFunction = MTLVertexStepFunctionPerVertex;
    d.vertexDescriptor = v;
    for (unsigned i = 0; i < 2; i++) {
        MTLRenderPipelineColorAttachmentDescriptor *a = d.colorAttachments[i];
        a.pixelFormat = MTLPixelFormatBGRA8Unorm;
        a.blendingEnabled = i == output && blend;
        a.sourceRGBBlendFactor = MTLBlendFactorOne;
        a.destinationRGBBlendFactor = MTLBlendFactorOneMinusSourceAlpha;
        a.sourceAlphaBlendFactor = MTLBlendFactorOne;
        a.destinationAlphaBlendFactor = MTLBlendFactorOneMinusSourceAlpha;
        a.rgbBlendOperation = a.alphaBlendOperation = MTLBlendOperationAdd;
    }
    NSError *error = nil;
    id<MTLRenderPipelineState> p = [device newRenderPipelineStateWithDescriptor:d error:&error];
    if (p == nil) printf("ca-alpha: FAIL pipeline %s: %s\n", label.UTF8String, error.description.UTF8String);
    require(p != nil, "pipeline compilation");
    [d release]; [vs release]; [fs release];
    return p;
}

/* All patterns are reflection-symmetric, so source row orientation cannot
 * hide alpha corruption. Bytes are BGRA; premultiplied source RGB <= alpha. */
static unsigned alpha_at(unsigned x, unsigned y, BOOL opaque)
{
    if (opaque) return 255;
    if (x < 4 || x >= 28 || y < 4 || y >= 28) return 0;
    if (x < 8 || x >= 24 || y < 8 || y >= 24) return 128;
    return 255;
}

static void background(unsigned frame, unsigned char bg[4])
{
    /* Period 17 exceeds the largest batch: a reused slot always changes. */
    unsigned phase = frame % 17;
    bg[0] = 16 + phase * 7;
    bg[1] = 32 + phase * 5;
    bg[2] = 144 - phase * 6;
    bg[3] = 255;
}

static void source_pixel(unsigned frame, unsigned alpha, unsigned char p[4])
{
    unsigned phase = frame % 17;
    const unsigned foreground[3] = {32 + phase * 8, 64 + phase * 6, 192 - phase * 9};
    for (unsigned c = 0; c < 3; c++) p[c] = (foreground[c] * alpha + 127) / 255;
    p[3] = alpha;
}

/* Exact half encodings: premultiplied tint RGBA=(.5,.75,1,1)*opacity.
 * Odd period 17 makes opacity change within reused batch8 slots as well. */
static unsigned opacity_index(unsigned frame)
{
    unsigned phase = frame % 17;
    return (phase + phase / 4) & 3;
}

static void tint(struct vertex vertices[4], unsigned frame)
{
    static const uint16_t rgba[4][4] = {
        {0,0,0,0}, {0x3000,0x3200,0x3400,0x3400},
        {0x3400,0x3600,0x3800,0x3800}, {0x3800,0x3a00,0x3c00,0x3c00}
    };
    for (unsigned v = 0; v < 4; v++) memcpy(vertices[v].color, rgba[opacity_index(frame)], sizeof(vertices[v].color));
}

static void encode(struct frame *f, id<MTLCommandQueue> queue, unsigned which,
                   id<MTLRenderPipelineState> states[STATES], id<MTLSamplerState> sampler)
{
    unsigned char source[IMAGE * IMAGE * 4], bg[4];
    background(f->number, bg);
    for (unsigned y = 0; y < IMAGE; y++) for (unsigned x = 0; x < IMAGE; x++)
        source_pixel(f->number, alpha_at(x, y, which == 0), source + 4 * (y * IMAGE + x));
    [f->source replaceRegion:MTLRegionMake2D(0, 0, IMAGE, IMAGE) mipmapLevel:0
                   withBytes:source bytesPerRow:IMAGE * 4];
    f->cb = [[queue commandBuffer] retain];
    require(f->cb != nil, "command buffer");
    MTLRenderPassDescriptor *pass = [MTLRenderPassDescriptor renderPassDescriptor];
    for (unsigned i = 0; i < 2; i++) {
        pass.colorAttachments[i].texture = f->target[i];
        pass.colorAttachments[i].loadAction = MTLLoadActionClear;
        pass.colorAttachments[i].storeAction = MTLStoreActionStore;
        pass.colorAttachments[i].clearColor = MTLClearColorMake(bg[2] / 255.0, bg[1] / 255.0, bg[0] / 255.0, 1);
        if (which == 5 && i == 1) pass.colorAttachments[i].clearColor = MTLClearColorMake(1, 1, 1, 1);
    }
    id<MTLRenderCommandEncoder> e = [f->cb renderCommandEncoderWithDescriptor:pass];
    require(e != nil, "render encoder");
    struct vertex vertices[4] = {
        {{-.5f, -.5f, 0, 1}, {0, 0}, {0, 0}, {0x3c00, 0x3c00, 0x3c00, 0x3c00}},
        {{ .5f, -.5f, 0, 1}, {1, 0}, {1, 0}, {0x3c00, 0x3c00, 0x3c00, 0x3c00}},
        {{-.5f,  .5f, 0, 1}, {0, 1}, {0, 1}, {0x3c00, 0x3c00, 0x3c00, 0x3c00}},
        {{ .5f,  .5f, 0, 1}, {1, 1}, {1, 1}, {0x3c00, 0x3c00, 0x3c00, 0x3c00}}
    };
    const float mvp[16] = {1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1};
    const float transform[8] = {1,1,0,0, 1,1,0,0}, lod = 0;
    const unsigned char uniforms[176] = {0};
    [e setVertexBytes:vertices length:sizeof(vertices) atIndex:0];
    [e setVertexBytes:mvp length:sizeof(mvp) atIndex:2];
    [e setVertexBytes:transform length:sizeof(transform) atIndex:3];
    [e setFragmentBytes:uniforms length:sizeof(uniforms) atIndex:1];
    [e setFragmentBytes:&lod length:sizeof(lod) atIndex:2];
    [e setFragmentTexture:f->source atIndex:3];
    [e setFragmentSamplerState:sampler atIndex:0];
    if (which >= 7) {
        /* Exact Idst shader: COLOR0 = framebuffer COLOR0 * vertex color.
         * Each quad is two nonoverlapping triangles. The second identical
         * quad overlaps the first, with RGB tint .5 and alpha1 throughout. */
        struct vertex overlap[12];
        static const unsigned order[6] = {0,1,2, 2,1,3};
        for (unsigned i = 0; i < 12; i++) {
            overlap[i] = vertices[order[i % 6]];
            overlap[i].color[0] = overlap[i].color[1] = overlap[i].color[2] = 0x3800;
        }
        [e setVertexBytes:overlap length:sizeof(overlap) atIndex:0];
        [e setRenderPipelineState:states[7]];
        if (which == 7) {
            [e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:6];
            [e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:6 vertexCount:6];
        } else {
            [e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:12];
        }
    } else {
    if (which == 5) {
        tint(vertices, f->number);
        [e setVertexBytes:vertices length:sizeof(vertices) atIndex:0];
    }
    [e setRenderPipelineState:states[which == 5 ? 6 : which >= 4 ? 4 : which]];
    [e drawPrimitives:MTLPrimitiveTypeTriangleStrip vertexStart:0 vertexCount:4];
    if (which == 4 || which == 6) {
        /* First draw writes source to MRT1. The exact regression pipeline
         * reads MRT1 by framebuffer fetch and writes source*vertexColor to0. */
        if (which == 6) {
            tint(vertices, f->number);
            [e setVertexBytes:vertices length:sizeof(vertices) atIndex:0];
        }
        [e setRenderPipelineState:states[which == 6 ? 6 : 5]];
        [e drawPrimitives:MTLPrimitiveTypeTriangleStrip vertexStart:0 vertexCount:4];
    }
    }
    [e endEncoding];
    [f->cb commit];
}

static void check(struct frame *f, unsigned which, unsigned delay)
{
    [f->cb waitUntilCompleted];
    if (f->cb.status != MTLCommandBufferStatusCompleted || f->cb.error != nil)
        printf("ca-alpha: GPU error case=%s frame=%u: %s\n", names[which], f->number, f->cb.error.description.UTF8String);
    require(f->cb.status == MTLCommandBufferStatusCompleted && f->cb.error == nil, "GPU completion");
    if (delay) usleep(delay);
    unsigned char pixels[SIDE * SIDE * 4], bg[4];
    background(f->number, bg);
    for (unsigned target = 0; target < 2; target++) {
        memset(pixels, 0xa5, sizeof(pixels));
        [f->target[target] getBytes:pixels bytesPerRow:SIDE * 4
                        fromRegion:MTLRegionMake2D(0, 0, SIDE, SIDE) mipmapLevel:0];
        for (unsigned y = 0; y < SIDE; y++) for (unsigned x = 0; x < SIDE; x++) {
            unsigned char want[4]; memcpy(want, bg, 4);
            if (which == 5 && target == 1) memset(want, 255, 4);
            BOOL in_quad = x >= 16 && x < 48 && y >= 16 && y < 48;
            BOOL drawn = which == 4 || which == 6 || (which == 5 ? target == 0 : target == (which >= 2 ? 1 : 0));
            if (which >= 7) {
                if (in_quad && target == 0)
                    for (unsigned c = 0; c < 3; c++) want[c] = (bg[c] + 2) / 4;
            } else if (in_quad && drawn) {
                unsigned alpha = alpha_at(x - 16, y - 16, which == 0);
                source_pixel(f->number, alpha, want);
                BOOL blend = which == 1 || which == 2 || (which == 4 && target == 0);
                if (which == 3) memset(want, alpha, 4);
                if (blend) for (unsigned c = 0; c < 4; c++) {
                    unsigned v = want[c] + (bg[c] * (255 - alpha) + 127) / 255;
                    want[c] = v > 255 ? 255 : v;
                }
                if (which >= 5 && target == 0) {
                    static const double opacity[4] = {0, .25, .5, 1};
                    const double o = opacity[opacity_index(f->number)];
                    const double tint_bgra[4] = {o, .75 * o, .5 * o, o};
                    const double mask = which == 6 ? alpha / 255.0 : 1.0;
                    const double output_alpha = (alpha / 255.0) * o * mask;
                    /* Keep shading and blending in floating point until the
                     * destination UNORM conversion, matching actual GPU work. */
                    for (unsigned c = 0; c < 4; c++) {
                        double v = want[c] * tint_bgra[c] * mask + bg[c] * (1 - output_alpha);
                        want[c] = (unsigned char)(v + .5);
                    }
                }
            }
            for (unsigned c = 0; c < 4; c++) {
                int got = pixels[4 * (y * SIDE + x) + c];
                if (abs(got - want[c]) > 1) {
                    printf("ca-alpha: FAIL case=%s frame=%u target=%u pixel=%u,%u channel=%u got=%u want=%u\n",
                           names[which], f->number, target, x, y, c, got, want[c]);
                    _exit(1);
                }
            }
        }
    }
    [f->cb release]; f->cb = nil;
}

int main(int argc, char **argv)
{
    unsigned frames = 16, batch = 1, delay = 0, chosen = CASES;
    BOOL fresh = NO;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--fresh")) fresh = YES;
        else if (i + 1 < argc && !strcmp(argv[i], "--frames")) frames = (unsigned)strtoul(argv[++i], NULL, 10);
        else if (i + 1 < argc && !strcmp(argv[i], "--batch")) batch = (unsigned)strtoul(argv[++i], NULL, 10);
        else if (i + 1 < argc && !strcmp(argv[i], "--delay-us")) delay = (unsigned)strtoul(argv[++i], NULL, 10);
        else if (i + 1 < argc && !strcmp(argv[i], "--case")) chosen = (unsigned)strtoul(argv[++i], NULL, 10);
        else { puts("usage: metal-ca-alpha-check [--frames 1..64] [--batch 1..8] [--fresh] [--delay-us 0..100000] [--case 0..8]"); return 2; }
    }
    if (frames < 1 || frames > 64 || batch < 1 || batch > MAX_BATCH || delay > 100000 || chosen > CASES) return 2;
    setbuf(stdout, NULL); signal(SIGALRM, watchdog); alarm(90);
    setprogname("backboardd");
    @autoreleasepool {
        id<MTLDevice> device = MTLCreateSystemDefaultDevice();
        NSError *error = nil;
        id<MTLLibrary> library = [device newLibraryWithURL:[NSURL fileURLWithPath:@"/redwood/share/ca-share/QuartzCore-default.metallib"] error:&error];
        require(device != nil && library != nil, "native device and canonical CA library");
        id<MTLCommandQueue> queue = [device newCommandQueue];
        require(queue != nil, "command queue");
        MTLSamplerDescriptor *sd = [[MTLSamplerDescriptor alloc] init];
        sd.minFilter = sd.magFilter = MTLSamplerMinMagFilterNearest;
        sd.sAddressMode = sd.tAddressMode = MTLSamplerAddressModeClampToEdge;
        id<MTLSamplerState> sampler = [device newSamplerStateWithDescriptor:sd];
        require(sampler != nil, "nearest sampler"); [sd release];
        id<MTLRenderPipelineState> states[STATES];
        states[0] = pipeline(device, library, @"PBGRABsovXm_TimgA2Xhf_Isrc", 0, NO);
        states[1] = pipeline(device, library, @"PBGRABsovXm_TimgA2Xhf_Isrc", 0, YES);
        states[2] = pipeline(device, library, @"PBGRABsovXm_TimgA2S1Xhf_Isrc", 1, YES);
        states[3] = pipeline(device, library, @"PBGRABsovXm_TimgA2S1Xhf_Ialp", 1, NO);
        states[4] = pipeline(device, library, @"PBGRABsovXm_TimgA2S1Xhf_Isrc", 1, NO);
        states[5] = pipeline(device, library, @"PBGRAXm_TcimA2Xhf_IsrcM1", 0, YES);
        states[6] = pipeline(device, library, @"PBGRABsovXm_TmuaA2Xhf_Isrc_IsrcM1", 0, YES);
        states[7] = pipeline(device, library, @"PBGRAXm_TcimA2Xhf_Idst", 0, NO);
        struct frame slots[MAX_BATCH] = {0};
        for (unsigned s = 0; s < batch; s++) {
            slots[s].source = texture(device, IMAGE, NO);
            for (unsigned t = 0; t < 2; t++) slots[s].target[t] = texture(device, SIDE, YES);
        }
        printf("ca-alpha: begin frames=%u batch=%u fresh=%u delay-us=%u; two MRTs, texture3, sampler0\n", frames, batch, fresh, delay);
        for (unsigned which = 0; which < CASES; which++) {
            if (chosen != CASES && chosen != which) continue;
            for (unsigned first = 0; first < frames; first += batch) {
                unsigned count = frames - first < batch ? frames - first : batch;
                @autoreleasepool {
                    for (unsigned s = 0; s < count; s++) {
                        if (fresh) { [slots[s].source release]; slots[s].source = texture(device, IMAGE, NO); }
                        slots[s].number = first + s;
                        encode(&slots[s], queue, which, states, sampler);
                    }
                    for (unsigned s = 0; s < count; s++) check(&slots[s], which, delay);
                }
            }
            printf("ca-alpha: PASS case=%s frames=%u pixels=%u\n", names[which], frames, frames * SIDE * SIDE * 2);
        }
        for (unsigned s = 0; s < batch; s++) {
            [slots[s].source release]; [slots[s].target[0] release]; [slots[s].target[1] release];
        }
        for (unsigned i = 0; i < STATES; i++) [states[i] release];
        [sampler release]; [queue release]; [library release]; [device release];
        alarm(0);
        puts("ca-alpha: complete PASS; panel scanout and compositor resource reuse not tested");
        return 0;
    }
}
