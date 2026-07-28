/* SPDX-License-Identifier: GPL-2.0 */
/* Generic MMIO record/replay engine — the reusable core behind the per-driver
 * harness generator. Driver-agnostic: the RECORD phase runs the C reference
 * against a RAM register model and logs every access (kind/offset/value/order);
 * the REPLAY phase feeds that frozen trace to a candidate and accepts iff the
 * candidate issues the identical access sequence. The register model being RAM
 * (writes persist, reads return last-written) makes read-modify-write faithful
 * and is what BOTH C and candidate see — so the differential is exactly
 * "same register program", the driver's real correctness.
 *
 * A driver's <fn>_ref.c and the candidate both #include this and call
 * reg_read/reg_write; the generated probe drives the two phases. Same seam
 * Ring 3/4 booted (reg_read/reg_write == an interceptor on readl/writel).
 */
#ifndef MMIOGEN_RECORD_ENGINE_H
#define MMIOGEN_RECORD_ENGINE_H
#include <stdint.h>

enum { RECORD, REPLAY };
static int mode;

struct acc { char kind; uint32_t off; uint32_t val; };
static struct acc trace[8192];
static int tn, tpos, diverged, div_pos;
static uint32_t regmodel[1024];   /* RAM register model, indexed by off>>2 */

static void model_seed(void)
{
	/* deterministic non-trivial initial register state so a plain read isn't
	 * always 0 (a get() reading an input register sees a real pattern) */
	for (int i = 0; i < 1024; i++)
		regmodel[i] = (uint32_t)(i * 2654435761u) ^ 0xA5A5A5A5u;
}

uint32_t reg_read(uint32_t off)
{
	if (mode == RECORD) {
		uint32_t v = regmodel[(off >> 2) & 1023];
		trace[tn++] = (struct acc){ 'R', off, v };
		return v;
	}
	if (tpos >= tn || trace[tpos].kind != 'R' || trace[tpos].off != off) {
		if (!diverged) { diverged = 1; div_pos = tpos; }
		return 0;
	}
	return trace[tpos++].val;
}

void reg_write(uint32_t off, uint32_t val)
{
	if (mode == RECORD) {
		regmodel[(off >> 2) & 1023] = val;
		trace[tn++] = (struct acc){ 'W', off, val };
		return;
	}
	if (tpos >= tn || trace[tpos].kind != 'W' ||
	    trace[tpos].off != off || trace[tpos].val != val) {
		if (!diverged) { diverged = 1; div_pos = tpos; }
		return;
	}
	tpos++;
}

#endif /* MMIOGEN_RECORD_ENGINE_H */
