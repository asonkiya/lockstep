// SPDX-License-Identifier: GPL-2.0
/* Ring 8 depth probe: verify the Tier-B table-walk transplant (which reads the
 * ClkDivTable struct through a #[repr(C)] mirror) against the C original. The
 * table maps val->div non-identically (val != div) so a mirror that swaps the
 * two fields is caught, not masked.
 *
 * Console: CLKDIV_PROBE: n=N bad=B firstbad=X verdict=DIFF_PASS|DIFF_FAIL
 */
#include <linux/init.h>
#include <linux/kernel.h>
#include <linux/types.h>

struct clk_div_table {
	unsigned int val;
	unsigned int div;
};

/* C reference + Rust candidate (Rust takes *const ClkDivTable — same ABI) */
unsigned int clkdiv_get_table_div_ref(const struct clk_div_table *table, unsigned int val);
unsigned int clkdiv_get_table_val_ref(const struct clk_div_table *table, unsigned int div);
unsigned int cgir_get_table_div(const struct clk_div_table *table, unsigned int val);
unsigned int cgir_get_table_val(const struct clk_div_table *table, unsigned int div);

/* non-identity mapping (val != div) + div==0 sentinel */
static const struct clk_div_table T[] = {
	{ 0, 1 }, { 1, 2 }, { 2, 4 }, { 3, 8 }, { 4, 16 },
	{ 5, 32 }, { 6, 64 }, { 7, 128 }, { 0, 0 },
};

static int __init clkdiv_probe_init(void)
{
	unsigned long n = 0, bad = 0;
	long fb = -1;
	unsigned int x;

	for (x = 0; x <= 40; x++) {
		n++;
		if (cgir_get_table_div(T, x) != clkdiv_get_table_div_ref(T, x)) {
			bad++;
			if (fb < 0)
				fb = x;
		}
		n++;
		if (cgir_get_table_val(T, x) != clkdiv_get_table_val_ref(T, x)) {
			bad++;
			if (fb < 0)
				fb = 1000 + x;
		}
	}
	pr_emerg("CLKDIV_PROBE: n=%lu bad=%lu firstbad=%ld verdict=%s\n",
		 n, bad, fb, bad == 0 ? "DIFF_PASS" : "DIFF_FAIL");
	return 0;
}
late_initcall(clkdiv_probe_init);
