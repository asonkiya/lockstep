// SPDX-License-Identifier: GPL-2.0
/* Ring 3 — recorded-I/O (MMIO-trace) differential oracle.
 *
 * The research pass found the driver mass (~73% of the kernel) has no functional
 * oracle: the only gate is "boots + no KCSAN" = didn't-crash, not correct. A
 * driver that programs its registers in the wrong order returns plausible values
 * and never crashes — the weak gate passes it. This oracle catches it: it records
 * the FULL register-access trace the C original produces, replays the identical
 * device responses to the Rust transplant, and asserts the two traces are
 * bit-identical. The transplant is judged on what it does to the device, not just
 * what it returns.
 *
 * The device is a software register model (hardware QEMU doesn't have). It is
 * deterministic: both drivers see identical responses, so a correct transplant
 * produces an identical trace, and any divergence in register program — wrong
 * order, missing poll, wrong register — is caught at the first differing access.
 *
 * Console: MOCKDEV_PROBE: cases=N ref_hash=.. cand_hash=.. firstdiff=X verdict=DIFF_PASS|DIFF_FAIL
 */
#include <linux/init.h>
#include <linux/kernel.h>
#include <linux/types.h>
#include <linux/string.h>

#include "mockdev.h"

/* the transplant seam: C reference and Rust candidate */
u32 mockdev_xfer_ref(struct regmodel *m, u32 input);
u32 cgir_mockdev_xfer(struct regmodel *m, u32 input);

#define BUSY_CYCLES	3
#define TRACE_MAX	4096

struct regmodel {
	u32 data;		/* staged operand / result */
	u32 result;
	int busy;		/* poll countdown after a command */
	/* access trace: each entry = (kind<<40 | off<<8 | (val & 0xff-ish)) */
	u64 trace[TRACE_MAX];
	int n;
};

/* what the device computes from the operand — an input-dependent transform, so
 * reading the wrong register or skipping the command yields a wrong result. */
static u32 devfn(u32 x)
{
	return ((x * 2654435761u) >> 15) ^ (x + 0x9e3779b9u);
}

static void trace_push(struct regmodel *m, char kind, u32 off, u32 val)
{
	if (m->n < TRACE_MAX)
		m->trace[m->n++] = ((u64)kind << 40) | ((u64)off << 8) | (val & 0xffffffffULL);
}

void reg_write(struct regmodel *m, u32 off, u32 val)
{
	trace_push(m, 'W', off, val);
	if (off == REG_DATA) {
		m->data = val;
	} else if (off == REG_CMD && val == CMD_START) {
		m->result = devfn(m->data);
		m->busy = BUSY_CYCLES;
	}
}

u32 reg_read(struct regmodel *m, u32 off)
{
	u32 v = 0;

	if (off == REG_STATUS) {
		v = m->busy > 0 ? STATUS_BUSY : 0;
		if (m->busy > 0)
			m->busy--;
	} else if (off == REG_DATA) {
		v = m->result;
	}
	trace_push(m, 'R', off, v);
	return v;
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

#define NCASES 256
static struct regmodel rm;

static int run(bool ref, u64 *trace_out, int *len_out, u32 *ret_acc)
{
	int i;

	rm.n = 0;
	*ret_acc = 0;
	for (i = 0; i < NCASES; i++) {
		u32 input = (u32)(i * 2246822519u + 1);

		rm.data = rm.result = 0;
		rm.busy = 0;
		/* record the return into the trace too, so a wrong result that
		 * somehow produced the right accesses is still caught */
		u32 r = ref ? mockdev_xfer_ref(&rm, input)
			    : cgir_mockdev_xfer(&rm, input);
		trace_push(&rm, 'V', 0, r);
		*ret_acc ^= r;
	}
	memcpy(trace_out, rm.trace, rm.n * sizeof(u64));
	*len_out = rm.n;
	return rm.n;
}

static u64 ref_trace[TRACE_MAX], cand_trace[TRACE_MAX];

static int __init mockdev_probe_init(void)
{
	int rn, cn, i, firstdiff = -1;
	u32 rret, cret;
	u64 rh, ch;

	run(true, ref_trace, &rn, &rret);
	run(false, cand_trace, &cn, &cret);

	for (i = 0; i < rn && i < cn; i++) {
		if (ref_trace[i] != cand_trace[i]) {
			firstdiff = i;
			break;
		}
	}
	if (firstdiff < 0 && rn != cn)
		firstdiff = (rn < cn ? rn : cn);
	rh = fnv1a(ref_trace, rn);
	ch = fnv1a(cand_trace, cn);

	pr_emerg("MOCKDEV_PROBE: cases=%d ref_len=%d cand_len=%d ref_hash=0x%llx cand_hash=0x%llx firstdiff=%d verdict=%s\n",
		 NCASES, rn, cn, rh, ch, firstdiff,
		 (firstdiff < 0 && rn == cn) ? "DIFF_PASS" : "DIFF_FAIL");
	if (firstdiff >= 0)
		pr_emerg("MOCKDEV_PROBE: at trace[%d] ref=0x%llx cand=0x%llx (kind/off/val)\n",
			 firstdiff, ref_trace[firstdiff], cand_trace[firstdiff]);
	return 0;
}
late_initcall(mockdev_probe_init);
