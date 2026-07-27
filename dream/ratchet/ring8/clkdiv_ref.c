// SPDX-License-Identifier: GPL-2.0
/* C reference — clk-divider's table-walk helpers, verbatim from
 * drivers/clk/clk-divider.c (renamed _ref to coexist with the transplant).
 * These are Tier-B: they iterate a `struct clk_div_table *` array and read its
 * fields — struct context that pure-scalar synth cannot express. */
#include <linux/types.h>

struct clk_div_table {
	unsigned int val;
	unsigned int div;
};

unsigned int clkdiv_get_table_div_ref(const struct clk_div_table *table, unsigned int val)
{
	const struct clk_div_table *clkt;

	for (clkt = table; clkt->div; clkt++)
		if (clkt->val == val)
			return clkt->div;
	return 0;
}

unsigned int clkdiv_get_table_val_ref(const struct clk_div_table *table, unsigned int div)
{
	const struct clk_div_table *clkt;

	for (clkt = table; clkt->div; clkt++)
		if (clkt->div == div)
			return clkt->val;
	return 0;
}
