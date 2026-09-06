/* Read-only persistent wlr-screencopy helper. No input protocols are bound.
 * Requests: OUTPUT X Y WIDTH HEIGHT DAMAGE_WAIT_MS\n
 * Replies: JSON metadata line, followed by width*height*3 RGB bytes.
 * Only untransformed scale-1 outputs are supported. Python falls back to grim.
 */
#define _GNU_SOURCE
#include <errno.h>
#include <poll.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>
#include <wayland-client.h>
#include "wlr-screencopy-client.h"

struct output {
    uint32_t id;
    struct wl_output *object;
    char name[128];
    int scale, transform;
};
static struct wl_display *display;
static struct wl_shm *shm;
static struct zwlr_screencopy_manager_v1 *manager;
static struct output outputs[32];
static struct wl_buffer *buffer;
static uint8_t *pixels;
static size_t bytes;
static uint32_t bw, bh, stride, format;
static bool done, failed, inverted, damage_mode, has_damage;
static int dx1, dy1, dx2, dy2;
static uint64_t presentation;
static struct zwlr_screencopy_frame_v1 *frame;

static int64_t now_ms(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (int64_t)t.tv_sec*1000+t.tv_nsec/1000000;
}

static bool dispatch_until(int64_t end) {
    while (!done && !failed) {
        while (wl_display_prepare_read(display) != 0) {
            if (wl_display_dispatch_pending(display) < 0) return false;
            if (done || failed) return !failed;
        }
        if (wl_display_flush(display) < 0 && errno != EAGAIN) {
            wl_display_cancel_read(display);
            return false;
        }
        int64_t remaining = end-now_ms();
        struct pollfd fd = {wl_display_get_fd(display), POLLIN, 0};
        int ready = poll(&fd, 1, remaining > 0 ? (int)remaining : 0);
        if (ready <= 0) {
            wl_display_cancel_read(display);
            if (ready < 0 && errno == EINTR) continue;
            return false;
        }
        if (!(fd.revents & POLLIN)) {
            wl_display_cancel_read(display);
            return false;
        }
        if (wl_display_read_events(display) < 0 || wl_display_dispatch_pending(display) < 0) return false;
    }
    return !failed;
}

static void geometry(void *data, struct wl_output *o, int32_t x, int32_t y,
                     int32_t pw, int32_t ph, int32_t subpixel, const char *make,
                     const char *model, int32_t transform) {
    (void)o; (void)x; (void)y; (void)pw; (void)ph; (void)subpixel; (void)make; (void)model;
    ((struct output *)data)->transform = transform;
}
static void mode(void *d, struct wl_output *o, uint32_t f, int32_t w, int32_t h, int32_t r) {
    (void)d; (void)o; (void)f; (void)w; (void)h; (void)r;
}
static void output_done(void *d, struct wl_output *o) {(void)d; (void)o;}
static void scale(void *d, struct wl_output *o, int32_t s) {(void)o; ((struct output *)d)->scale=s;}
static void name(void *d, struct wl_output *o, const char *n) {
    (void)o; snprintf(((struct output *)d)->name, 128, "%s", n);
}
static void description(void *d, struct wl_output *o, const char *s) {(void)d; (void)o; (void)s;}
static const struct wl_output_listener output_listener = {geometry, mode, output_done, scale, name, description};

static void global(void *d, struct wl_registry *r, uint32_t id, const char *interface, uint32_t version) {
    (void)d;
    if (!strcmp(interface, "wl_shm")) shm=wl_registry_bind(r,id,&wl_shm_interface,1);
    else if (!strcmp(interface,"zwlr_screencopy_manager_v1") && version>=3)
        manager=wl_registry_bind(r,id,&zwlr_screencopy_manager_v1_interface,3);
    else if (!strcmp(interface,"wl_output") && version>=4) {
        for (int i=0;i<32;i++) if (!outputs[i].object) {
            outputs[i]=(struct output){.id=id,.scale=1};
            outputs[i].object=wl_registry_bind(r,id,&wl_output_interface,4);
            wl_output_add_listener(outputs[i].object,&output_listener,&outputs[i]);
            break;
        }
    }
}
static void removed(void *d, struct wl_registry *r, uint32_t id) {
    (void)d; (void)r;
    for (int i=0;i<32;i++) if (outputs[i].object && outputs[i].id==id) {
        wl_output_destroy(outputs[i].object);
        outputs[i].object=NULL;
        failed=true;
    }
}
static const struct wl_registry_listener registry_listener={global,removed};

static void free_buffer(void) {
    if (buffer) wl_buffer_destroy(buffer);
    if (pixels) munmap(pixels,bytes);
    buffer=NULL; pixels=NULL; bytes=0;
}
static void frame_buffer(void *d, struct zwlr_screencopy_frame_v1 *f,
                         uint32_t fmt, uint32_t w, uint32_t h, uint32_t pitch) {
    (void)d; (void)f;
    if (!w || !h || w>16384 || h>16384 || (uint64_t)w*h>16000000 || pitch<w*4 || pitch>w*4+4096 ||
        (fmt!=WL_SHM_FORMAT_XRGB8888 && fmt!=WL_SHM_FORMAT_ARGB8888 &&
         fmt!=WL_SHM_FORMAT_XBGR8888 && fmt!=WL_SHM_FORMAT_ABGR8888)) {failed=true; return;}
    if (buffer && bw==w && bh==h && stride==pitch && format==fmt) return;
    free_buffer(); bw=w; bh=h; stride=pitch; format=fmt; bytes=(size_t)pitch*h;
    int fd=memfd_create("wayland-cu-frame",MFD_CLOEXEC);
    if (fd<0) {failed=true; return;}
    if (ftruncate(fd,(off_t)bytes)<0) {close(fd); failed=true; return;}
    void *map=mmap(NULL,bytes,PROT_READ|PROT_WRITE,MAP_SHARED,fd,0);
    if (map==MAP_FAILED) {close(fd); failed=true; return;}
    pixels=map;
    struct wl_shm_pool *pool=wl_shm_create_pool(shm,fd,(int32_t)bytes);
    buffer=wl_shm_pool_create_buffer(pool,0,(int32_t)w,(int32_t)h,(int32_t)pitch,fmt);
    wl_shm_pool_destroy(pool); close(fd);
}
static void flags(void *d, struct zwlr_screencopy_frame_v1 *f, uint32_t v) {
    (void)d; (void)f; inverted=v & ZWLR_SCREENCOPY_FRAME_V1_FLAGS_Y_INVERT;
}
static void ready(void *d, struct zwlr_screencopy_frame_v1 *f, uint32_t hi, uint32_t lo, uint32_t ns) {
    (void)d; (void)f; presentation=(((uint64_t)hi<<32)|lo)*1000000000+ns; done=true;
}
static void failure(void *d, struct zwlr_screencopy_frame_v1 *f) {(void)d; (void)f; failed=true;}
static void damage(void *d, struct zwlr_screencopy_frame_v1 *f, uint32_t x, uint32_t y, uint32_t w, uint32_t h) {
    (void)d; (void)f;
    if (x>bw || y>bh || w>bw-x || h>bh-y) {failed=true; return;}
    if (!has_damage) {dx1=(int)x;dy1=(int)y;dx2=(int)(x+w);dy2=(int)(y+h);has_damage=true;}
    else {
        if ((int)x<dx1) dx1=(int)x;
        if ((int)y<dy1) dy1=(int)y;
        if ((int)(x+w)>dx2) dx2=(int)(x+w);
        if ((int)(y+h)>dy2) dy2=(int)(y+h);
    }
}
static void dmabuf(void *d, struct zwlr_screencopy_frame_v1 *f,uint32_t fmt,uint32_t w,uint32_t h) {
    (void)d;(void)f;(void)fmt;(void)w;(void)h;
}
static void buffer_done(void *d, struct zwlr_screencopy_frame_v1 *f) {
    (void)d;
    if (failed || !buffer) {failed=true;return;}
    if (damage_mode) zwlr_screencopy_frame_v1_copy_with_damage(f,buffer);
    else zwlr_screencopy_frame_v1_copy(f,buffer);
}
static const struct zwlr_screencopy_frame_v1_listener frame_listener={frame_buffer,flags,ready,failure,damage,dmabuf,buffer_done};

static bool capture(struct output *o,int x,int y,int w,int h,int wait_ms) {
    done=failed=inverted=has_damage=false;
    damage_mode=wait_ms>0;
    frame=zwlr_screencopy_manager_v1_capture_output_region(manager,0,o->object,x,y,w,h);
    zwlr_screencopy_frame_v1_add_listener(frame,&frame_listener,NULL);
    bool ok=dispatch_until(now_ms()+(wait_ms>0?wait_ms:2000));
    zwlr_screencopy_frame_v1_destroy(frame);frame=NULL;
    // A damage wait can time out on a static UI. Force a new copy for freshness.
    if (!ok && !failed && damage_mode) {
        free_buffer();
        return capture(o,x,y,w,h,0);
    }
    return ok && bw==(uint32_t)w && bh==(uint32_t)h;
}

int main(void) {
    display=wl_display_connect(NULL);
    if (!display) return 1;
    struct wl_registry *registry=wl_display_get_registry(display);
    wl_registry_add_listener(registry,&registry_listener,NULL);
    if (wl_display_roundtrip(display)<0 || wl_display_roundtrip(display)<0 || !manager || !shm) return 2;
    char line[512], output_name[128], extra;
    while (fgets(line,sizeof(line),stdin)) {
        int x,y,w,h,wait_ms;
        if (sscanf(line,"%127s %d %d %d %d %d %c",output_name,&x,&y,&w,&h,&wait_ms,&extra)!=6 ||
            x<0 || y<0 || w<1 || h<1 || w>16384 || h>16384 ||
            (int64_t)w*h>16000000 || wait_ms<0 || wait_ms>1000) return 3;
        if (wl_display_roundtrip(display)<0) return 4;
        struct output *o=NULL;
        for (int i=0;i<32;i++) if (outputs[i].object && !strcmp(outputs[i].name,output_name)) o=&outputs[i];
        if (!o || o->scale!=1 || o->transform!=WL_OUTPUT_TRANSFORM_NORMAL || !capture(o,x,y,w,h,wait_ms)) {
            puts("{\"error\":\"unsupported output or capture failure\"}");fflush(stdout);continue;
        }
        uint8_t *rgb=malloc((size_t)w*h*3);
        if (!rgb) return 5;
        for (int row=0;row<h;row++) for (int col=0;col<w;col++) {
            uint32_t p; memcpy(&p,pixels+(size_t)(inverted?h-1-row:row)*stride+col*4,4);
            size_t at=((size_t)row*w+col)*3;
            bool bgr=format==WL_SHM_FORMAT_XBGR8888 || format==WL_SHM_FORMAT_ABGR8888;
            rgb[at]=(p>>(bgr?0:16))&255;rgb[at+1]=(p>>8)&255;rgb[at+2]=(p>>(bgr?16:0))&255;
        }
        if (has_damage && inverted) {int top=dy1;dy1=h-dy2;dy2=h-top;}
        printf("{\"width\":%d,\"height\":%d,\"presentation_ns\":%llu,\"damage\":",w,h,(unsigned long long)presentation);
        if (has_damage) printf("[%d,%d,%d,%d]",dx1,dy1,dx2,dy2);else printf("null");
        puts("}");
        bool written=fwrite(rgb,3,(size_t)w*h,stdout)==(size_t)w*h && fflush(stdout)==0;
        free(rgb);if (!written) break;
    }
    free_buffer();
    zwlr_screencopy_manager_v1_destroy(manager);wl_shm_destroy(shm);
    for (int i=0;i<32;i++) if (outputs[i].object) wl_output_destroy(outputs[i].object);
    wl_registry_destroy(registry);wl_display_disconnect(display);
    return 0;
}
