// Compare known HDR10 chart pixels before and after Windows desktop duplication.
// Only the 24 centers of this program's chart are copied to CPU-readable memory.
#define NOMINMAX
#include <windows.h>
#include <d3d11.h>
#include <dxgi1_6.h>
#include <wrl/client.h>
#include <array>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <stdexcept>
#include <vector>

using Microsoft::WRL::ComPtr;
using Clock = std::chrono::steady_clock;
using Pixel = std::array<unsigned, 3>;
using Patches = std::array<Pixel, 24>;
constexpr unsigned cols = 6, rows = 4, tile = 36, width = cols * tile, height = rows * tile;
// HueSync.exe 1.13.1.83, DuplicateOutput1 call at RVA 0x1b8d19.
constexpr DXGI_FORMAT formats[] = {DXGI_FORMAT_R10G10B10A2_UNORM, DXGI_FORMAT_R8G8B8A8_UNORM,
    DXGI_FORMAT_R8G8B8A8_UNORM_SRGB, DXGI_FORMAT_B8G8R8A8_UNORM, DXGI_FORMAT_B8G8R8A8_UNORM_SRGB};

static void check(HRESULT hr, const char *operation) {
    if (FAILED(hr)) {
        std::fprintf(stderr, "%s failed (0x%08lx)\n", operation, hr);
        throw std::runtime_error(operation);
    }
}

static unsigned pq(double nits) {
    double p = std::pow(nits / 10000., 2610./16384.);
    return static_cast<unsigned>(std::lround(1023. * std::pow((3424./4096. + 2413./128.*p) /
                                                              (1. + 2392./128.*p), 2523./32.)));
}

static Patches chart() {
    Patches result{};
    const double gray[] = {0,203,0,203,203,0, 0,.01,.1,1,5,10,18,50,100,203,400,1000};
    for (unsigned i = 0; i < 18; ++i) result[i].fill(pq(gray[i]));
    const unsigned colors[] = {1,2,4,6,5,3};
    for (unsigned i = 0; i < 6; ++i)
        for (unsigned c = 0; c < 3; ++c) result[18+i][c] = pq((colors[i] & (1u<<c)) ? 203. : 0.);
    return result;
}

static uint32_t pack(const Pixel& rgb, DXGI_FORMAT format) {
    if (format == formats[0]) return rgb[0] | rgb[1]<<10 | rgb[2]<<20 | 3u<<30;
    unsigned r = rgb[0]*255/1023, g = rgb[1]*255/1023, b = rgb[2]*255/1023;
    bool bgra = format == formats[3] || format == formats[4];
    return (bgra ? b : r) | g<<8 | (bgra ? r : b)<<16 | 255u<<24;
}

static Pixel unpack(uint32_t pixel, DXGI_FORMAT format) {
    if (format == formats[0]) return {pixel & 1023, (pixel>>10) & 1023, (pixel>>20) & 1023};
    switch (format) {
    case DXGI_FORMAT_R8G8B8A8_UNORM: case DXGI_FORMAT_R8G8B8A8_UNORM_SRGB:
        return {pixel & 255, (pixel>>8) & 255, (pixel>>16) & 255};
    case DXGI_FORMAT_B8G8R8A8_UNORM: case DXGI_FORMAT_B8G8R8A8_UNORM_SRGB:
        return {(pixel>>16) & 255, (pixel>>8) & 255, pixel & 255};
    default: throw std::runtime_error("Unexpected duplication format");
    }
}

static ComPtr<ID3D11Texture2D> texture(ID3D11Device *dev, unsigned w, unsigned h,
                                       DXGI_FORMAT format, bool staging) {
    D3D11_TEXTURE2D_DESC desc{};
    desc.Width=w; desc.Height=h; desc.Format=format; desc.ArraySize=desc.MipLevels=desc.SampleDesc.Count=1;
    desc.Usage = staging ? D3D11_USAGE_STAGING : D3D11_USAGE_DEFAULT;
    desc.CPUAccessFlags = staging ? D3D11_CPU_ACCESS_READ : 0;
    ComPtr<ID3D11Texture2D> result;
    check(dev->CreateTexture2D(&desc, nullptr, &result), "Create chart texture");
    return result;
}

static void draw(ID3D11DeviceContext *context, ID3D11Texture2D *target, DXGI_FORMAT format) {
    const Patches codes = chart();
    std::vector<uint32_t> pixels(width * height);
    for (unsigned y=0; y<height; ++y)
        for (unsigned x=0; x<width; ++x) pixels[y*width+x] = pack(codes[(y/tile)*cols+x/tile], format);
    context->UpdateSubresource(target, 0, nullptr, pixels.data(), 4*width, 0);
}

static Patches sample(ID3D11Device *device, ID3D11DeviceContext *ctx, ID3D11Texture2D *frame,
                      unsigned x, unsigned y, DXGI_FORMAT format) {
    D3D11_TEXTURE2D_DESC desc{};
    frame->GetDesc(&desc);
    if (desc.Width < width || x > desc.Width-width || desc.Height < height || y > desc.Height-height)
        throw std::runtime_error("Chart is outside the capture surface");
    auto readback = texture(device, cols, rows, format, true);
    for (unsigned i=0; i<cols*rows; ++i) {
        unsigned px=x+(i%cols)*tile+tile/2, py=y+(i/cols)*tile+tile/2;
        D3D11_BOX box{px, py, 0, px+1, py+1, 1};
        ctx->CopySubresourceRegion(readback.Get(),0,i%cols,i/cols,0,frame,0,&box);
    }
    D3D11_MAPPED_SUBRESOURCE map{};
    check(ctx->Map(readback.Get(), 0, D3D11_MAP_READ, 0, &map), "Read chart centers");
    Patches result{};
    for (unsigned i=0; i<result.size(); ++i) {
        auto line = static_cast<const uint8_t *>(map.pData) + (i/cols)*map.RowPitch;
        result[i] = unpack(reinterpret_cast<const uint32_t *>(line)[i%cols], format);
    }
    ctx->Unmap(readback.Get(), 0);
    return result;
}

static void self_test() {
    ComPtr<ID3D11Device> device;
    ComPtr<ID3D11DeviceContext> ctx;
    check(D3D11CreateDevice(nullptr, D3D_DRIVER_TYPE_WARP, nullptr, 0, nullptr, 0, D3D11_SDK_VERSION,
                            &device, nullptr, &ctx), "Create offscreen WARP device");
    for (auto format : formats) {
        auto frame = texture(device.Get(), width, height, format, false);
        draw(ctx.Get(), frame.Get(), format);
        auto actual = sample(device.Get(), ctx.Get(), frame.Get(), 0, 0, format);
        auto expected = chart();
        if (format != formats[0])
            for (auto& rgb : expected) for (auto& c : rgb) c = c*255/1023;
        if (actual != expected) throw std::runtime_error("Unexpected patch values in offscreen self-test");
    }
    std::puts("24 chart centers matched in all five Hue capture formats on offscreen D3D11 textures.");
}

struct Window {
    HWND handle;
    ~Window() { if (handle) DestroyWindow(handle); }
};

static bool visible_centers(HWND window, POINT origin) {
    for (unsigned i=0; i<cols*rows; ++i) {
        POINT p{origin.x+LONG((i%cols)*tile+tile/2), origin.y+LONG((i/cols)*tile+tile/2)};
        if (WindowFromPoint(p) != window) return false;
    }
    return true;
}

static void measure(IDXGIFactory2 *factory, IDXGIAdapter1 *adapter, IDXGIOutput6 *output, const DXGI_OUTPUT_DESC1& desc) {
    ComPtr<ID3D11Device> device;
    ComPtr<ID3D11DeviceContext> ctx;
    check(D3D11CreateDevice(adapter, D3D_DRIVER_TYPE_UNKNOWN, nullptr, 0, nullptr, 0, D3D11_SDK_VERSION,
                            &device, nullptr, &ctx), "Create HDR device");
    ComPtr<IDXGIOutputDuplication> duplication;
    check(output->DuplicateOutput1(device.Get(), 0, 5, formats, &duplication), "DuplicateOutput1");
    Window win{CreateWindowExW(WS_EX_NOACTIVATE|WS_EX_TOOLWINDOW|WS_EX_TOPMOST, L"STATIC", L"HDR reference chart",
                               WS_POPUP, desc.DesktopCoordinates.left+64, desc.DesktopCoordinates.top+64,
                               width,height,nullptr,nullptr,GetModuleHandleW(nullptr),nullptr)};
    if (!win.handle) throw std::runtime_error("Could not create the HDR chart window");
    DXGI_SWAP_CHAIN_DESC1 sc{};
    sc.Width=width; sc.Height=height; sc.Format=formats[0]; sc.SampleDesc.Count=1;
    sc.BufferUsage=DXGI_USAGE_RENDER_TARGET_OUTPUT; sc.BufferCount=2; sc.SwapEffect=DXGI_SWAP_EFFECT_FLIP_DISCARD;
    ComPtr<IDXGISwapChain1> chain;
    check(factory->CreateSwapChainForHwnd(device.Get(), win.handle, &sc, nullptr, nullptr, &chain), "Create HDR swap chain");
    ComPtr<IDXGISwapChain4> hdr;
    check(chain.As(&hdr), "HDR swap chain interface");
    UINT support=0;
    check(hdr->CheckColorSpaceSupport(DXGI_COLOR_SPACE_RGB_FULL_G2084_NONE_P2020,&support), "HDR presentation support");
    if (!(support & DXGI_SWAP_CHAIN_COLOR_SPACE_SUPPORT_FLAG_PRESENT))
        throw std::runtime_error("HDR10 presentation unavailable on this output");
    check(hdr->SetColorSpace1(DXGI_COLOR_SPACE_RGB_FULL_G2084_NONE_P2020), "Set chart to BT.2020/PQ");
    // Match the HDR10 metadata of the HEVC chart used on the TV.
    DXGI_HDR_METADATA_HDR10 metadata{{35400,14600}, {8500,39850}, {6550,2300}, {15635,16450},
                                     1000, 1, 1000, 126};
    check(hdr->SetHDRMetaData(DXGI_HDR_METADATA_TYPE_HDR10, sizeof(metadata), &metadata), "Set HDR10 mastering data");
    auto source = texture(device.Get(), width, height, formats[0], false);
    draw(ctx.Get(), source.Get(), formats[0]);
    ShowWindow(win.handle, SW_SHOWNOACTIVATE);
    POINT origin{};
    if (!ClientToScreen(win.handle, &origin)) throw std::runtime_error("Could not locate chart");
    auto deadline = Clock::now()+std::chrono::seconds(3);
    unsigned stable=0;
    Patches previous{};
    DXGI_FORMAT previous_format = DXGI_FORMAT_UNKNOWN;
    unsigned presentation = 0;
    while (Clock::now() < deadline) {
        MSG msg{};
        while (PeekMessageW(&msg,nullptr,0,0,PM_REMOVE)) DispatchMessageW(&msg);
        ComPtr<ID3D11Texture2D> back;
        check(chain->GetBuffer(0, IID_PPV_ARGS(&back)), "Chart back buffer");
        // Dirty a corner outside the probe points so a static composition can
        // still yield several distinct desktop frames.
        D3D11_BOX corner{0,0,0,1,1,1};
        uint32_t corner_pixel = pack(Pixel{unsigned(++presentation%2)*20,0,0}, formats[0]);
        ctx->UpdateSubresource(source.Get(),0,&corner,&corner_pixel,sizeof(corner_pixel),0);
        ctx->CopyResource(back.Get(), source.Get());
        check(chain->Present(1,0), "Present the HDR chart");
        DXGI_OUTDUPL_FRAME_INFO info{};
        ComPtr<IDXGIResource> resource;
        HRESULT status = duplication->AcquireNextFrame(100, &info, &resource);
        if (status == DXGI_ERROR_WAIT_TIMEOUT) continue;
        check(status, "Acquire chart frame");
        if (!info.LastPresentTime.QuadPart || !info.AccumulatedFrames) {
            check(duplication->ReleaseFrame(), "Release cursor-only frame");
            continue;
        }
        ComPtr<ID3D11Texture2D> frame;
        check(resource.As(&frame), "Read duplicated format");
        D3D11_TEXTURE2D_DESC fd{}; frame->GetDesc(&fd);
        if (!visible_centers(win.handle,origin)) throw std::runtime_error("Chart is obscured");
        auto current = sample(device.Get(),ctx.Get(),frame.Get(),origin.x-desc.DesktopCoordinates.left,
                              origin.y-desc.DesktopCoordinates.top,fd.Format);
        check(duplication->ReleaseFrame(), "Release chart frame");
        // Require the asymmetric neutral marker before retaining a reading.
        bool marker = true;
        for (unsigned c=0; c<3; ++c) {
            const auto same_code = [&](unsigned i,unsigned ref) { return std::abs(int(current[i][c])-int(current[ref][c])) <= 2; };
            marker = marker && current[1][c] > current[0][c]+20 && same_code(2,0) && same_code(5,0) && same_code(6,0) &&
                     same_code(3,1) && same_code(4,1) && same_code(15,1);
        }
        if (!marker || !visible_centers(win.handle,origin)) { stable=0; continue; }
        stable = previous == current && previous_format == fd.Format ? stable+1 : 1;
        previous=current; previous_format=fd.Format;
        if (stable < 3) continue;
        std::printf("{\"chart\":\"c9-hdr10-v1\",\"source\":\"desktop-duplication\",\"dxgiFormat\":%u,"
                    "\"displayColorSpace\":%u,\"bitsPerColor\":%u,\"codeMax\":%u,\"frames\":%u,\"patches\":[",
                    fd.Format,desc.ColorSpace,desc.BitsPerColor,fd.Format==formats[0]?1023:255,stable);
        for (unsigned i=0; i<current.size(); ++i)
            std::printf("%s[%u,%u,%u]",i?",":"",current[i][0],current[i][1],current[i][2]);
        std::puts("]}");
        return;
    }
    throw std::runtime_error("Three stable HDR chart frames were not observed");
}

int main(int argc, char **argv) {
    try {
        if (argc==2 && !std::strcmp(argv[1],"--self-test")) { self_test(); return 0; }
        if (argc!=2 || std::strcmp(argv[1],"--measure")) {
            std::fputs("Use --self-test (offscreen) or --measure (briefly displays an HDR10 chart).\n",stderr);
            return 2;
        }
        if (!SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2))
            throw std::runtime_error("Physical monitor coordinates unavailable");
        ComPtr<IDXGIFactory2> factory;
        check(CreateDXGIFactory1(IID_PPV_ARGS(&factory)), "Enumerate displays");
        for (UINT a=0;;++a) {
            ComPtr<IDXGIAdapter1> adapter;
            HRESULT hr=factory->EnumAdapters1(a,&adapter);
            if (hr==DXGI_ERROR_NOT_FOUND) break;
            check(hr,"Enumerate adapter");
            for (UINT n=0;;++n) {
                ComPtr<IDXGIOutput> base;
                hr=adapter->EnumOutputs(n,&base);
                if (hr==DXGI_ERROR_NOT_FOUND) break;
                check(hr,"Enumerate output");
                ComPtr<IDXGIOutput6> output;
                DXGI_OUTPUT_DESC1 desc{};
                if (FAILED(base.As(&output)) || FAILED(output->GetDesc1(&desc))) continue;
                if (desc.AttachedToDesktop && desc.Rotation==DXGI_MODE_ROTATION_IDENTITY &&
                        desc.ColorSpace==DXGI_COLOR_SPACE_RGB_FULL_G2084_NONE_P2020) {
                    measure(factory.Get(), adapter.Get(), output.Get(), desc);
                    return 0;
                }
            }
        }
        throw std::runtime_error("No active unrotated BT.2020/PQ desktop output");
    } catch (const std::exception& e) { std::fprintf(stderr,"%s\n",e.what()); return 1; }
}
