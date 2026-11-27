/* Clean-room palette expansion for the owned WA 3.8.1 executable.
 * The original 005a2020 expands indexed rows through a 256-dword palette.
 * Keep its guest calling convention and scratch writes; unusual/aliased
 * layouts retain the original implementation. No scheduler yield is allowed
 * while the validated guest-memory pointers are borrowed.
 */
#include "../../../kit/platform/os.h"
#include "../../../kit/runtime/x86.h"
#include "pop_mod_api.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

POP_MOD_DECLARE_ABI();
static int verify;
static int failed;

static uint32_t load32(const void *p) {
  uint32_t v;
  memcpy(&v, p, sizeof v);
  return v;
}
static void store32(void *p, uint32_t v) { memcpy(p, &v, sizeof v); }
static int overlaps(uint32_t a, uint32_t n, uint32_t b, uint32_t m) {
  return (uint64_t)a < (uint64_t)b + m && (uint64_t)b < (uint64_t)a + n;
}

/* Union the dword stores that actually intersect each tracked lock. A lock
 * may start/end inside this row; reporting the whole row in that case would
 * enlarge its dirty span beyond what the original stores reported.
 */
static void written_row(uint32_t row, uint32_t bytes) {
  for (uint32_t i = 0; i < g_dirty_count; ++i) {
    RecompDirty *d = &g_dirty[i];
    uint64_t lo = row > d->base ? row : d->base;
    uint64_t end = (uint64_t)row + bytes,
             range_end = (uint64_t)d->base + d->len;
    uint64_t hi = end < range_end ? end : range_end;
    if (lo >= hi)
      continue;
    uint32_t first = row + (uint32_t)(lo - row) / 4 * 4;
    uint32_t last = row + ((uint32_t)(hi - row) + 3) / 4 * 4;
    if (first < d->lo)
      d->lo = first;
    if (last > d->hi)
      d->hi = last;
  }
}

/* Register inputs: EAX=height, ECX=source. Stack dwords after the return
 * address: width, source pitch, destination, destination pitch, palette.
 * Callee-saved registers and untouched flag fields must remain unchanged.
 */
static void expand(const PopModApi *api, pop_cpu_v1 *cpu,
                   PopHookInvocation *inv, void *user) {
  void *stack_ptr, *src_ptr, *dst_ptr, *pal_ptr;
  uint32_t h = cpu->eax, w, sp, dp, dst, pal, sn, dn;
  uint64_t source_bytes, destination_bytes;
  pop_cpu_v1 entry, reference;
  uint8_t before_stack[40], reference_stack[40];
  uint8_t *reference_pixels = NULL;
  RecompDirty before_dirty[RECOMP_DIRTY_SLOTS],
      reference_dirty[RECOMP_DIRTY_SLOTS];
  uint32_t before_dirty_count = 0, reference_dirty_count = 0;
  uint64_t native_begin = 0;
  double native_ms = 0;
  (void)inv;
  (void)user;
  if (failed || g_watch_len || !h || h > 16384 || cpu->esp < 16 ||
      api->guest_ptr(api, cpu->esp - 16, 40, &stack_ptr) != POP_OK)
    goto original;
  uint8_t *stack = (uint8_t *)stack_ptr + 16;
  w = load32(stack + 4);
  sp = load32(stack + 8);
  dst = load32(stack + 12);
  dp = load32(stack + 16);
  pal = load32(stack + 20);
  if (!w || w > 16384 || sp < w || dp < w * 4 || sp > INT32_MAX ||
      dp > INT32_MAX)
    goto original;
  source_bytes = (uint64_t)(h - 1) * sp + w;
  destination_bytes = (uint64_t)(h - 1) * dp + w * 4;
  if (source_bytes > 64 * 1024 * 1024 || destination_bytes > 64 * 1024 * 1024 ||
      (uint64_t)cpu->ecx + source_bytes > UINT32_MAX ||
      (uint64_t)dst + destination_bytes > UINT32_MAX ||
      (uint64_t)pal + 1024 > UINT32_MAX)
    goto original;
  sn = (uint32_t)source_bytes;
  dn = (uint32_t)destination_bytes;
  if (overlaps(cpu->ecx, sn, dst, dn) || overlaps(pal, 1024, dst, dn) ||
      overlaps(cpu->esp - 16, 40, cpu->ecx, sn) ||
      overlaps(cpu->esp - 16, 40, dst, dn) ||
      overlaps(cpu->esp - 16, 40, pal, 1024) ||
      api->guest_ptr(api, cpu->ecx, sn, &src_ptr) != POP_OK ||
      api->guest_ptr(api, dst, dn, &dst_ptr) != POP_OK ||
      api->guest_ptr(api, pal, 1024, &pal_ptr) != POP_OK)
    goto original;

  /* Diagnostic comparison checks destination padding, stack and CPU too.
   * Allocate before borrowing again: malloc can grow Wasm memory.
   */
  if (verify) {
    entry = *cpu;
    reference_pixels = malloc(dn);
    if (!reference_pixels)
      goto original;
    if (api->guest_ptr(api, entry.esp - 16, 40, &stack_ptr) != POP_OK ||
        api->guest_ptr(api, entry.ecx, sn, &src_ptr) != POP_OK ||
        api->guest_ptr(api, dst, dn, &dst_ptr) != POP_OK ||
        api->guest_ptr(api, pal, 1024, &pal_ptr) != POP_OK) {
      free(reference_pixels);
      goto original;
    }
    stack = (uint8_t *)stack_ptr + 16;
    memcpy(before_stack, stack - 16, sizeof before_stack);
    before_dirty_count = g_dirty_count;
    memcpy(before_dirty, g_dirty, sizeof before_dirty);
    native_begin = os_monotonic_ns();
  }
  for (uint32_t y = 0; y < h; ++y) {
    const uint8_t *source = (const uint8_t *)src_ptr + (size_t)y * sp;
    uint8_t *destination = (uint8_t *)dst_ptr + (size_t)y * dp;
    for (uint32_t x = 0; x < w; ++x)
      store32(destination + x * 4, load32((uint8_t *)pal_ptr + source[x] * 4));
    /* Raw palette stores must publish the same dirty spans as guest wr32.
     * DirectDraw Unlock uses these spans to discover and upload changed pixels.
     * An armed watchpoint takes the original path for per-store diagnostics.
     */
    written_row(dst + y * dp, w * 4);
  }
  /* Preserve the original pushes and its consumed fifth argument. Only
   * CF/ZF are materialized by this routine's translated flag liveness.
   */
  wr32(cpu->esp - 4, cpu->ebx);
  wr32(cpu->esp - 8, cpu->esi);
  wr32(cpu->esp - 12, cpu->edi);
  wr32(cpu->esp - 16, cpu->ebp);
  wr32(cpu->esp + 20, 0);
  cpu->eax = dst + (h - 1) * dp + w * 4;
  cpu->ecx += (h - 1) * sp + w;
  cpu->edx = w;
  cpu->cf = 0;
  cpu->zf = 1;
  if (reference_pixels) {
    native_ms = (os_monotonic_ns() - native_begin) / 1000000.0;
    /* Model RET locally; hook_return and call_original are mutually
     * exclusive in one invocation. Then restore the entry and delegate.
     * The original overwrites every output pixel, leaving its result live.
     */
    cpu->eip = load32(stack);
    cpu->esp += 24;
    reference = *cpu;
    memcpy(reference_pixels, dst_ptr, dn);
    memcpy(reference_stack, stack - 16, sizeof reference_stack);
    reference_dirty_count = g_dirty_count;
    memcpy(reference_dirty, g_dirty, sizeof reference_dirty);
    memcpy(stack - 16, before_stack, sizeof before_stack);
    g_dirty_count = before_dirty_count;
    memcpy(g_dirty, before_dirty, sizeof before_dirty);
    *cpu = entry;
    uint64_t original_begin = os_monotonic_ns();
    api->call_original(api, cpu->target, cpu);
    double original_ms = (os_monotonic_ns() - original_begin) / 1000000.0;
    /* The original's backedges may yield; borrow fresh pointers. */
    int mismatch =
        api->guest_ptr(api, entry.esp - 16, 40, &stack_ptr) != POP_OK ||
        api->guest_ptr(api, dst, dn, &dst_ptr) != POP_OK;
    if (!mismatch)
      mismatch =
          memcmp(dst_ptr, reference_pixels, dn) ||
          memcmp(stack_ptr, reference_stack, sizeof reference_stack) ||
          memcmp(cpu, &reference, sizeof reference) ||
          g_dirty_count != reference_dirty_count ||
          memcmp(g_dirty, reference_dirty, g_dirty_count * sizeof *g_dirty);
    free(reference_pixels);
    if (mismatch) {
      failed = 1;
      api->log(api, "wa.palette: verification FAILED; retaining original");
    } else {
      static uint32_t last_w, last_h, count;
      static double native_total, original_total;
      if (w != last_w || h != last_h) {
        last_w = w;
        last_h = h;
        count = 0;
        native_total = original_total = 0;
      }
      ++count;
      native_total += native_ms;
      original_total += original_ms;
      if (count == 1 || count == 120) {
        char text[192];
        snprintf(text, sizeof text,
                 "wa.palette: verified %ux%u calls=%u native=%.3fms "
                 "original=%.3fms (pixels, stack, CPU, dirty spans)",
                 w, h, count, native_total / count, original_total / count);
        api->log(api, text);
      }
    }
  } else
    api->hook_return(api, cpu, cpu->eax, 20);
  return;
original:
  api->call_original(api, cpu->target, cpu);
}

PopModStatus pop_mod_init(const PopModApi *api) {
  uint32_t hook;
  const char *enabled = getenv("RECOMP_WA_PALETTE");
  const char *check = getenv("RECOMP_WA_PALETTE_VERIFY");
  if (enabled && !strcmp(enabled, "0"))
    return POP_OK;
  verify = check && !strcmp(check, "1");
  PopModStatus status =
      api->hook_install_ex(api, 0x005a2020, 0, expand, POP_HOOK_REPLACE,
                           POP_HOOK_NO_GAME_VIEW, NULL, &hook);
  if (status == POP_OK)
    api->log(api, verify ? "wa.palette: comparison enabled"
                         : "wa.palette: fast expansion enabled");
  return status;
}
