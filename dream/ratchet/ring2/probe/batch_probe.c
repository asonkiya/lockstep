// SPDX-License-Identifier: GPL-2.0
/* Ring 2 batch differential oracle — verify FOUR leaf transplants in ONE boot
 * (the cost-model's key lever: amortize the expensive boot over a batch).
 *
 * The reference needs no duplication: at gate time these functions are still
 * the kernel's own C (we gate BEFORE weaving), so we compare each Rust candidate
 * cgir_* against the live exported kernel symbol over wide input ranges.
 *
 * Console: BATCH_PROBE: <name> n=N bad=B firstbad=X verdict=DIFF_PASS|DIFF_FAIL
 */
#include <linux/init.h>
#include <linux/kernel.h>
#include <linux/types.h>

/* kernel originals (references) */
extern u64 int_pow(u64 base, unsigned int exp);
extern unsigned int __sw_hweight32(unsigned int w);
extern unsigned long __sw_hweight64(__u64 w);
extern unsigned long gcd(unsigned long a, unsigned long b);
/* Rust candidates */
u64 cgir_int_pow(u64 base, unsigned int exp);
unsigned int cgir_sw_hweight32(unsigned int w);
unsigned long cgir_sw_hweight64(__u64 w);
unsigned long cgir_gcd(unsigned long a, unsigned long b);

static void report(const char *name, unsigned long n, unsigned long bad, long fb)
{
	pr_emerg("BATCH_PROBE: %s n=%lu bad=%lu firstbad=%ld verdict=%s\n",
		 name, n, bad, fb, bad == 0 ? "DIFF_PASS" : "DIFF_FAIL");
}

static int __init batch_init(void)
{
	unsigned long n, bad, i, j;
	long fb;

	/* int_pow — base 0..80 x exp 0..14; large products exercise u64 wrap */
	n = bad = 0; fb = -1;
	for (i = 0; i <= 80; i++)
		for (j = 0; j <= 14; j++) {
			n++;
			if (cgir_int_pow(i, j) != int_pow(i, j)) {
				bad++;
				if (fb < 0)
					fb = (long)(i * 100 + j);
			}
		}
	report("int_pow", n, bad, fb);

	/* __sw_hweight32 — dense 0..200000 + all-ones + walking patterns */
	n = bad = 0; fb = -1;
	for (i = 0; i <= 200000; i++) {
		unsigned int w = (unsigned int)i;

		n++;
		if (cgir_sw_hweight32(w) != __sw_hweight32(w)) {
			bad++;
			if (fb < 0)
				fb = i;
		}
	}
	for (i = 0; i < 32; i++) {
		unsigned int w = 0xffffffffu >> i;

		n++;
		if (cgir_sw_hweight32(w) != __sw_hweight32(w)) {
			bad++;
			if (fb < 0)
				fb = -2;
		}
	}
	report("hweight32", n, bad, fb);

	/* __sw_hweight64 — spread of 64-bit patterns */
	n = bad = 0; fb = -1;
	{
		__u64 vals[] = { 0, 1, ~0ULL, ~0ULL - 1, 0x5555555555555555ULL,
				 0xaaaaaaaaaaaaaaaaULL, 0xdeadbeefcafef00dULL,
				 0x0123456789abcdefULL, 0x8000000000000000ULL,
				 0xffffffffULL, 0x100000000ULL };
		for (i = 0; i < ARRAY_SIZE(vals); i++) {
			n++;
			if (cgir_sw_hweight64(vals[i]) != __sw_hweight64(vals[i])) {
				bad++;
				if (fb < 0)
					fb = i;
			}
		}
		/* plus every single-bit value */
		for (i = 0; i < 64; i++) {
			__u64 w = 1ULL << i;

			n++;
			if (cgir_sw_hweight64(w) != __sw_hweight64(w)) {
				bad++;
				if (fb < 0)
					fb = -3;
			}
		}
	}
	report("hweight64", n, bad, fb);

	/* gcd — a,b over 0..300 (incl zeros), plus some large pairs */
	n = bad = 0; fb = -1;
	for (i = 0; i <= 300; i++)
		for (j = 0; j <= 300; j++) {
			n++;
			if (cgir_gcd(i, j) != gcd(i, j)) {
				bad++;
				if (fb < 0)
					fb = (long)(i * 1000 + j);
			}
		}
	{
		unsigned long pa[] = { 1000000007UL, 123456UL, 0xdeadbeefUL, 48UL, 0UL };
		unsigned long pb[] = { 998244353UL, 789012UL, 0xcafeUL, 36UL, 77UL };

		for (i = 0; i < ARRAY_SIZE(pa); i++) {
			n++;
			if (cgir_gcd(pa[i], pb[i]) != gcd(pa[i], pb[i])) {
				bad++;
				if (fb < 0)
					fb = -4;
			}
		}
	}
	report("gcd", n, bad, fb);

	pr_emerg("BATCH_PROBE: done\n");
	return 0;
}
late_initcall(batch_init);
