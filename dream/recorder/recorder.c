// SPDX-License-Identifier: GPL-2.0
/* The driver-MMIO effect recorder — the piece that makes the driver mass (the
 * ~73%) soundly verifiable.
 *
 * A driver's meaning is its register program, not its return value; and a return-
 * value differential over-credits it (the wide run passed __refrigerator/
 * probe_irq_mask by reproducing only the return). Ring 3/4 fixed the oracle but
 * needed a LIVE device model present while running BOTH the C and the Rust. The
 * recorder removes that: it captures the C driver's exact register trace ONCE
 * (the ordered readl/writel accesses + the values the device returned), then
 * REPLAYS that frozen recording to the Rust transplant with NO device present.
 * The Rust is accepted iff it issues the identical access sequence (same offsets,
 * order, and written values) and returns the same result.
 *
 * That decoupling is the whole unlock: you record a real driver's MMIO once (on
 * real hardware, or a model, or from a captured trace) and verify every
 * transplant candidate against the recording forever, without the device. Here
 * the reg_read/reg_write seam stands in for readl/writel; in-kernel it becomes an
 * interceptor on the real accessors (Ring 3/4 proved that seam boots).
 *
 * modes: RECORD hits the device model and logs; REPLAY feeds the log back and
 * checks the candidate against it.
 */
#include <stdio.h>
#include <stdint.h>

#define REG_DATA	0x00u
#define REG_CMD		0x04u
#define REG_STATUS	0x08u
#define CMD_START	0x1u
#define STATUS_BUSY	0x1u
#define BUSY_CYCLES	3

enum { RECORD, REPLAY };
static int mode;

struct acc { char kind; uint32_t off; uint32_t val; };
static struct acc trace[4096];
static int tn, tpos;
static int diverged, div_pos;

/* device model — used ONLY during RECORD (the recording replaces it thereafter) */
static uint32_t dev_data, dev_result;
static int dev_busy;
static uint32_t devfn(uint32_t x) { return ((x * 2654435761u) >> 15) ^ (x + 0x9e3779b9u); }

uint32_t reg_read(uint32_t off)
{
	if (mode == RECORD) {
		uint32_t v = 0;
		if (off == REG_STATUS) {
			v = dev_busy > 0 ? STATUS_BUSY : 0;
			if (dev_busy > 0)
				dev_busy--;
		} else if (off == REG_DATA) {
			v = dev_result;
		}
		trace[tn++] = (struct acc){ 'R', off, v };
		return v;
	}
	/* REPLAY: the next recorded access must be this exact read */
	if (tpos >= tn || trace[tpos].kind != 'R' || trace[tpos].off != off) {
		if (!diverged) { diverged = 1; div_pos = tpos; }
		return 0;
	}
	return trace[tpos++].val;
}

void reg_write(uint32_t off, uint32_t val)
{
	if (mode == RECORD) {
		if (off == REG_DATA)
			dev_data = val;
		else if (off == REG_CMD && val == CMD_START) {
			dev_result = devfn(dev_data);
			dev_busy = BUSY_CYCLES;
		}
		trace[tn++] = (struct acc){ 'W', off, val };
		return;
	}
	/* REPLAY: the next recorded access must be this exact write (off + value) */
	if (tpos >= tn || trace[tpos].kind != 'W' || trace[tpos].off != off || trace[tpos].val != val) {
		if (!diverged) { diverged = 1; div_pos = tpos; }
		return;
	}
	tpos++;
}

/* the C reference driver (the recording source) */
uint32_t xfer_ref(uint32_t input)
{
	reg_write(REG_DATA, input);
	reg_write(REG_CMD, CMD_START);
	while (reg_read(REG_STATUS) & STATUS_BUSY)
		;
	return reg_read(REG_DATA);
}

/* the Rust transplant under test */
extern uint32_t cgir_xfer(uint32_t input);

int main(void)
{
	unsigned cases = 512, bad = 0;
	long firstbad = -1;
	int worst_divpos = -1;

	for (unsigned i = 0; i < cases; i++) {
		uint32_t input = i * 2246822519u + 1;

		/* RECORD: run the C driver against the device model, capture the trace */
		mode = RECORD;
		tn = 0; dev_busy = 0; dev_data = dev_result = 0;
		uint32_t ref = xfer_ref(input);
		int ref_len = tn;

		/* REPLAY: run the Rust candidate against the frozen recording only */
		mode = REPLAY;
		tpos = 0; diverged = 0; div_pos = -1;
		uint32_t cand = cgir_xfer(input);
		int consumed_all = (tpos == ref_len);

		if (diverged || !consumed_all || cand != ref) {
			bad++;
			if (firstbad < 0) {
				firstbad = i;
				worst_divpos = diverged ? div_pos : (consumed_all ? -2 : tpos);
			}
		}
	}
	printf("RECORDER: cases=%u bad=%u firstbad=%ld first_divergence_at_trace=%d verdict=%s\n",
	       cases, bad, firstbad, worst_divpos, bad ? "DIVERGE" : "MATCH");
	return bad ? 1 : 0;
}
