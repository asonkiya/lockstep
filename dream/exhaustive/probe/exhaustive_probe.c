// SPDX-License-Identifier: GPL-2.0
/* Exhaustive bounded verification — the soundness upgrade the prior art flagged.
 *
 * A differential over sampled inputs can miss a divergence at an untested input
 * (RustAssure caught 11 such bugs a fuzzer missed). For a function whose input
 * domain is small enough, we close that gap completely: iterate the ENTIRE
 * domain and compare the transplant to the kernel C at every point. That is not
 * a test, it is a proof of equivalence over the whole domain — the sound,
 * model-checker-free version of VERT's bounded verification, feasible here
 * because __sw_hweight8/16 have 2^8 / 2^16 inputs.
 *
 * Console: EXHAUSTIVE: <fn> domain=N bad=B verdict=PROVEN|COUNTEREXAMPLE
 */
#include <linux/init.h>
#include <linux/kernel.h>
#include <linux/types.h>

unsigned int __sw_hweight8(unsigned int);
unsigned int __sw_hweight16(unsigned int);
unsigned int cgir___sw_hweight8(unsigned int);
unsigned int cgir___sw_hweight16(unsigned int);

static int __init exhaustive_init(void)
{
	unsigned long bad, n;
	unsigned int w;

	/* __sw_hweight8: entire 8-bit domain (0..255) */
	bad = 0;
	for (w = 0; w <= 0xff; w++)
		if (cgir___sw_hweight8(w) != __sw_hweight8(w))
			bad++;
	pr_emerg("EXHAUSTIVE: __sw_hweight8 domain=256 bad=%lu verdict=%s\n",
		 bad, bad ? "COUNTEREXAMPLE" : "PROVEN");

	/* __sw_hweight16: entire 16-bit domain (0..65535) */
	bad = 0; n = 0;
	for (w = 0; w <= 0xffff; w++) {
		n++;
		if (cgir___sw_hweight16(w) != __sw_hweight16(w))
			bad++;
	}
	pr_emerg("EXHAUSTIVE: __sw_hweight16 domain=%lu bad=%lu verdict=%s\n",
		 n, bad, bad ? "COUNTEREXAMPLE" : "PROVEN");

	pr_emerg("EXHAUSTIVE: done\n");
	return 0;
}
late_initcall(exhaustive_init);
