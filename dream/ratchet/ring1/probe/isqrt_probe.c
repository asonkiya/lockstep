// SPDX-License-Identifier: GPL-2.0
/* Differential oracle for the int_sqrt leaf: drive many inputs through both the
 * Rust candidate (cgir_int_sqrt) and the C original (int_sqrt_ref) and assert
 * bit-identical results. Pure function -> the oracle is exhaustive-ish and
 * trivially deterministic (no state, no clock).
 *
 * Console: ISQRT_PROBE: n=N mismatches=M firstbad=X verdict=DIFF_PASS|DIFF_FAIL
 */
#include <linux/init.h>
#include <linux/kernel.h>

unsigned long cgir_int_sqrt(unsigned long x);   /* the Rust candidate */
unsigned long int_sqrt_ref(unsigned long x);    /* the C original */

static int __init isqrt_probe_init(void)
{
	unsigned long x, cases = 0, bad = 0;
	long firstbad = -1;
	int i;

	/* dense small range: every value 0..20000 */
	for (x = 0; x <= 20000; x++) {
		cases++;
		if (cgir_int_sqrt(x) != int_sqrt_ref(x)) {
			bad++;
			if (firstbad < 0)
				firstbad = x;
		}
	}
	/* powers of two and their neighbours, up to the top of the range */
	for (i = 0; i < 63; i++) {
		unsigned long p = 1UL << i;
		unsigned long probe[3] = { p - 1, p, p + 1 };
		int j;

		for (j = 0; j < 3; j++) {
			x = probe[j];
			cases++;
			if (cgir_int_sqrt(x) != int_sqrt_ref(x)) {
				bad++;
				if (firstbad < 0)
					firstbad = x;
			}
		}
	}
	/* a spread of large values incl. ULONG_MAX */
	{
		unsigned long big[] = { 0xdeadbeefUL, 0x100000000UL, 0xffffffffUL,
					0x123456789abcdefUL, ~0UL, ~0UL - 1, 0x8000000000000000UL };
		int j;

		for (j = 0; j < (int)ARRAY_SIZE(big); j++) {
			x = big[j];
			cases++;
			if (cgir_int_sqrt(x) != int_sqrt_ref(x)) {
				bad++;
				if (firstbad < 0)
					firstbad = x;
			}
		}
	}

	pr_emerg("ISQRT_PROBE: n=%lu mismatches=%lu firstbad=%ld verdict=%s\n",
		 cases, bad, firstbad, bad == 0 ? "DIFF_PASS" : "DIFF_FAIL");
	if (bad)
		pr_emerg("ISQRT_PROBE: x=%ld cand=%lu ref=%lu\n",
			 firstbad, cgir_int_sqrt(firstbad), int_sqrt_ref(firstbad));
	return 0;
}
late_initcall(isqrt_probe_init);
