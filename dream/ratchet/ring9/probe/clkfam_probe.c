// SPDX-License-Identifier: GPL-2.0
/* Ring 9 — sweep-verify the clk-divider math family (6 fns) against the C
 * originals, one boot. Drives a non-identity table + the divider flags + widths.
 * Widths bounded to 1..5 so every 1<<mask stays < 32 (well-defined in both C and
 * Rust; the point is the family logic, not shift-overflow UB).
 *
 * Console: CLKFAM: <fn> bad=B verdict=DIFF_PASS|DIFF_FAIL ; then CLKFAM: total ...
 */
#include <linux/init.h>
#include <linux/kernel.h>
#include <linux/types.h>

struct clk_div_table { unsigned int val; unsigned int div; };

unsigned int clkfam_get_table_div_ref(const struct clk_div_table *, unsigned int);
unsigned int clkfam_get_table_val_ref(const struct clk_div_table *, unsigned int);
unsigned int clkfam_get_table_maxdiv_ref(const struct clk_div_table *, u8);
unsigned int clkfam_get_maxdiv_ref(const struct clk_div_table *, u8, unsigned long);
unsigned int clkfam_get_div_ref(const struct clk_div_table *, unsigned int, unsigned long, u8);
unsigned int clkfam_get_val_ref(const struct clk_div_table *, unsigned int, unsigned long, u8);

unsigned int cgir_get_table_div(const struct clk_div_table *, unsigned int);
unsigned int cgir_get_table_val(const struct clk_div_table *, unsigned int);
unsigned int cgir_get_table_maxdiv(const struct clk_div_table *, u8);
unsigned int cgir_get_maxdiv(const struct clk_div_table *, u8, unsigned long);
unsigned int cgir_get_div(const struct clk_div_table *, unsigned int, unsigned long, u8);
unsigned int cgir_get_val(const struct clk_div_table *, unsigned int, unsigned long, u8);

static const struct clk_div_table T[] = {
	{ 0, 1 }, { 1, 2 }, { 2, 4 }, { 3, 8 }, { 4, 16 }, { 5, 3 }, { 0, 0 },
};
static const unsigned long FLAGS[] = { 0, 1u << 0, 1u << 1, 1u << 6, 1u << 8 };

static unsigned long g_total_bad;

static int __init clkfam_probe_init(void)
{
	unsigned int v, w, fi;
	unsigned long bad;

	bad = 0;
	for (v = 0; v <= 40; v++)
		if (cgir_get_table_div(T, v) != clkfam_get_table_div_ref(T, v)) bad++;
	g_total_bad += bad;
	pr_emerg("CLKFAM: get_table_div bad=%lu verdict=%s\n", bad, bad ? "DIFF_FAIL" : "DIFF_PASS");

	bad = 0;
	for (v = 0; v <= 40; v++)
		if (cgir_get_table_val(T, v) != clkfam_get_table_val_ref(T, v)) bad++;
	g_total_bad += bad;
	pr_emerg("CLKFAM: get_table_val bad=%lu verdict=%s\n", bad, bad ? "DIFF_FAIL" : "DIFF_PASS");

	bad = 0;
	for (w = 1; w <= 5; w++)
		if (cgir_get_table_maxdiv(T, w) != clkfam_get_table_maxdiv_ref(T, w)) bad++;
	g_total_bad += bad;
	pr_emerg("CLKFAM: get_table_maxdiv bad=%lu verdict=%s\n", bad, bad ? "DIFF_FAIL" : "DIFF_PASS");

	bad = 0;
	for (w = 1; w <= 5; w++)
		for (fi = 0; fi < ARRAY_SIZE(FLAGS); fi++)
			if (cgir_get_maxdiv(T, w, FLAGS[fi]) != clkfam_get_maxdiv_ref(T, w, FLAGS[fi])) bad++;
	g_total_bad += bad;
	pr_emerg("CLKFAM: get_maxdiv bad=%lu verdict=%s\n", bad, bad ? "DIFF_FAIL" : "DIFF_PASS");

	bad = 0;
	for (v = 0; v <= 18; v++)
		for (w = 1; w <= 5; w++)
			for (fi = 0; fi < ARRAY_SIZE(FLAGS); fi++)
				if (cgir_get_div(T, v, FLAGS[fi], w) != clkfam_get_div_ref(T, v, FLAGS[fi], w)) bad++;
	g_total_bad += bad;
	pr_emerg("CLKFAM: get_div bad=%lu verdict=%s\n", bad, bad ? "DIFF_FAIL" : "DIFF_PASS");

	bad = 0;
	for (v = 1; v <= 18; v++)
		for (w = 1; w <= 5; w++)
			for (fi = 0; fi < ARRAY_SIZE(FLAGS); fi++)
				if (cgir_get_val(T, v, FLAGS[fi], w) != clkfam_get_val_ref(T, v, FLAGS[fi], w)) bad++;
	g_total_bad += bad;
	pr_emerg("CLKFAM: get_val bad=%lu verdict=%s\n", bad, bad ? "DIFF_FAIL" : "DIFF_PASS");

	pr_emerg("CLKFAM: total bad=%lu verdict=%s\n", g_total_bad, g_total_bad ? "DIFF_FAIL" : "DIFF_PASS");
	return 0;
}
late_initcall(clkfam_probe_init);
