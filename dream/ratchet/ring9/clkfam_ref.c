// SPDX-License-Identifier: GPL-2.0
/* C reference — the clk-divider divider-math family, verbatim from
 * drivers/clk/clk-divider.c (renamed _ref, internal calls rewired to _ref).
 * A real subsystem cluster: six functions all operating on struct clk_div_table
 * + scalars + flags — reachable only because ksdk mirrors clk_div_table. */
#include <linux/types.h>

struct clk_div_table { unsigned int val; unsigned int div; };

#define clk_div_mask(width)		((1u << (width)) - 1)
#define CLK_DIVIDER_ONE_BASED		(1u << 0)
#define CLK_DIVIDER_POWER_OF_TWO	(1u << 1)
#define CLK_DIVIDER_MAX_AT_ZERO		(1u << 6)
#define CLK_DIVIDER_EVEN_INTEGERS	(1u << 8)

unsigned int clkfam_get_table_div_ref(const struct clk_div_table *table, unsigned int val)
{
	const struct clk_div_table *clkt;
	for (clkt = table; clkt->div; clkt++)
		if (clkt->val == val)
			return clkt->div;
	return 0;
}

unsigned int clkfam_get_table_val_ref(const struct clk_div_table *table, unsigned int div)
{
	const struct clk_div_table *clkt;
	for (clkt = table; clkt->div; clkt++)
		if (clkt->div == div)
			return clkt->val;
	return 0;
}

unsigned int clkfam_get_table_maxdiv_ref(const struct clk_div_table *table, u8 width)
{
	unsigned int maxdiv = 0, mask = clk_div_mask(width);
	const struct clk_div_table *clkt;
	for (clkt = table; clkt->div; clkt++)
		if (clkt->div > maxdiv && clkt->val <= mask)
			maxdiv = clkt->div;
	return maxdiv;
}

unsigned int clkfam_get_maxdiv_ref(const struct clk_div_table *table, u8 width, unsigned long flags)
{
	if (flags & CLK_DIVIDER_ONE_BASED)
		return clk_div_mask(width);
	if (flags & CLK_DIVIDER_POWER_OF_TWO)
		return 1 << clk_div_mask(width);
	if (flags & CLK_DIVIDER_EVEN_INTEGERS)
		return 2 * (clk_div_mask(width) + 1);
	if (table)
		return clkfam_get_table_maxdiv_ref(table, width);
	return clk_div_mask(width) + 1;
}

unsigned int clkfam_get_div_ref(const struct clk_div_table *table, unsigned int val,
				unsigned long flags, u8 width)
{
	if (flags & CLK_DIVIDER_ONE_BASED)
		return val;
	if (flags & CLK_DIVIDER_POWER_OF_TWO)
		return 1 << val;
	if (flags & CLK_DIVIDER_MAX_AT_ZERO)
		return val ? val : clk_div_mask(width) + 1;
	if (flags & CLK_DIVIDER_EVEN_INTEGERS)
		return 2 * (val + 1);
	if (table)
		return clkfam_get_table_div_ref(table, val);
	return val + 1;
}

unsigned int clkfam_get_val_ref(const struct clk_div_table *table, unsigned int div,
				unsigned long flags, u8 width)
{
	if (flags & CLK_DIVIDER_ONE_BASED)
		return div;
	if (flags & CLK_DIVIDER_POWER_OF_TWO)
		return __builtin_ctz(div);
	if (flags & CLK_DIVIDER_MAX_AT_ZERO)
		return (div == clk_div_mask(width) + 1) ? 0 : div;
	if (flags & CLK_DIVIDER_EVEN_INTEGERS)
		return (div >> 1) - 1;
	if (table)
		return clkfam_get_table_val_ref(table, div);
	return div - 1;
}
