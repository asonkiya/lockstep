// SPDX-License-Identifier: GPL-2.0
/* Ring 4 — differentially verify a REAL driver's register logic (gpio-zevio)
 * against its Rust transplant, over a recorded register-access trace.
 *
 * The register block is a software array (hardware QEMU doesn't have); readl/
 * writel hit it, which faithfully models this passive GPIO controller (OUTPUT
 * and DIRECTION registers hold bits you write and read back; INPUT is seeded
 * with a fixed pattern). mmio_r/mmio_w do the access AND record it, so the two
 * implementations are compared on the exact register program they run — offsets,
 * values, order — not just the get() return values.
 *
 * Console: ZEVIO_PROBE: ops=N ref_hash=.. cand_hash=.. firstdiff=X verdict=DIFF_PASS|DIFF_FAIL
 */
#include <linux/init.h>
#include <linux/kernel.h>
#include <linux/types.h>
#include <linux/string.h>

#include "zevio.h"

/* C reference + Rust candidate seams */
int zevio_gpio_get_ref(void *regs, unsigned int pin);
int zevio_gpio_set_ref(void *regs, unsigned int pin, int value);
int zevio_gpio_dir_in_ref(void *regs, unsigned int pin);
int zevio_gpio_dir_out_ref(void *regs, unsigned int pin, int value);
int cgir_zevio_gpio_get(void *regs, unsigned int pin);
int cgir_zevio_gpio_set(void *regs, unsigned int pin, int value);
int cgir_zevio_gpio_dir_in(void *regs, unsigned int pin);
int cgir_zevio_gpio_dir_out(void *regs, unsigned int pin, int value);

#define BLK_U32	64		/* 4 sections x 0x40 bytes */
#define TRACE_MAX 8192

static u32 blk[BLK_U32];
static u64 trace[TRACE_MAX];
static int tn;

static void trace_push(char kind, u32 off, u32 val)
{
	if (tn < TRACE_MAX)
		trace[tn++] = ((u64)kind << 40) | ((u64)off << 8) | (val & 0xffffffffULL);
}

u32 mmio_r(void *base, u32 off)
{
	u32 v = ((u32 *)base)[off / 4];

	trace_push('R', off, v);
	return v;
}

void mmio_w(void *base, u32 off, u32 val)
{
	trace_push('W', off, val);
	((u32 *)base)[off / 4] = val;
}

static void seed_block(void)
{
	int i;

	/* deterministic INPUT pattern + zeroed OUTPUT/DIRECTION; both runs start
	 * identical so any divergence is the transplant's register program. */
	for (i = 0; i < BLK_U32; i++)
		blk[i] = 0x11111111u * (u32)(i + 1);
}

enum { OP_DIROUT, OP_SET, OP_GET, OP_DIRIN };

/* fixed op script across all 32 pins */
static int run(bool ref, u64 *out, int *len, u32 *ret_acc)
{
	unsigned int pin;

	tn = 0;
	*ret_acc = 0;
	seed_block();
	for (pin = 0; pin < 32; pin++) {
		int v1 = (pin * 7 + 1) & 1;
		int v2 = (pin * 3) & 1;
		int r;

		if (ref)
			zevio_gpio_dir_out_ref(blk, pin, v1);
		else
			cgir_zevio_gpio_dir_out(blk, pin, v1);

		if (ref)
			zevio_gpio_set_ref(blk, pin, v2);
		else
			cgir_zevio_gpio_set(blk, pin, v2);

		r = ref ? zevio_gpio_get_ref(blk, pin) : cgir_zevio_gpio_get(blk, pin);
		trace_push('V', pin, (u32)r);
		*ret_acc ^= (u32)r << (pin & 31);

		if (ref)
			zevio_gpio_dir_in_ref(blk, pin);
		else
			cgir_zevio_gpio_dir_in(blk, pin);

		r = ref ? zevio_gpio_get_ref(blk, pin) : cgir_zevio_gpio_get(blk, pin);
		trace_push('V', pin, (u32)r);
		*ret_acc ^= (u32)r << ((pin + 16) & 31);
	}
	memcpy(out, trace, tn * sizeof(u64));
	*len = tn;
	return tn;
}

static u64 fnv1a(const u64 *v, int n)
{
	u64 h = 0xcbf29ce484222325ULL;
	int i;

	for (i = 0; i < n; i++) {
		h ^= v[i];
		h *= 0x100000001b3ULL;
	}
	return h;
}

static u64 rt[TRACE_MAX], ct[TRACE_MAX];

static int __init zevio_probe_init(void)
{
	int rn, cn, i, firstdiff = -1;
	u32 rret, cret;

	run(true, rt, &rn, &rret);
	run(false, ct, &cn, &cret);

	for (i = 0; i < rn && i < cn; i++)
		if (rt[i] != ct[i]) {
			firstdiff = i;
			break;
		}
	if (firstdiff < 0 && rn != cn)
		firstdiff = rn < cn ? rn : cn;

	pr_emerg("ZEVIO_PROBE: ops=%d ref_len=%d cand_len=%d ref_ret=0x%x cand_ret=0x%x ref_hash=0x%llx cand_hash=0x%llx firstdiff=%d verdict=%s\n",
		 32, rn, cn, rret, cret, fnv1a(rt, rn), fnv1a(ct, cn), firstdiff,
		 (firstdiff < 0 && rn == cn) ? "DIFF_PASS" : "DIFF_FAIL");
	if (firstdiff >= 0)
		pr_emerg("ZEVIO_PROBE: at trace[%d] ref=0x%llx cand=0x%llx\n",
			 firstdiff, rt[firstdiff], ct[firstdiff]);
	return 0;
}
late_initcall(zevio_probe_init);
