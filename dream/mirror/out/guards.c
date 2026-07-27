// SPDX-License-Identifier: GPL-2.0
#include <linux/build_bug.h>
#include <linux/kernel.h>
#include <linux/stddef.h>
#include <linux/clk-provider.h>
#include <linux/timecounter.h>

static void __maybe_unused mirror_layout_guards(void)
{
	BUILD_BUG_ON(sizeof(struct clk_div_table) != 8);
	BUILD_BUG_ON(offsetof(struct clk_div_table, val) != 0);
	BUILD_BUG_ON(offsetof(struct clk_div_table, div) != 4);
	BUILD_BUG_ON(sizeof(struct clk_duty) != 8);
	BUILD_BUG_ON(offsetof(struct clk_duty, num) != 0);
	BUILD_BUG_ON(offsetof(struct clk_duty, den) != 4);
	BUILD_BUG_ON(sizeof(struct cyclecounter) != 24);
	BUILD_BUG_ON(offsetof(struct cyclecounter, read) != 0);
	BUILD_BUG_ON(offsetof(struct cyclecounter, mask) != 8);
	BUILD_BUG_ON(offsetof(struct cyclecounter, mult) != 16);
	BUILD_BUG_ON(offsetof(struct cyclecounter, shift) != 20);
	BUILD_BUG_ON(sizeof(struct timecounter) != 40);
	BUILD_BUG_ON(offsetof(struct timecounter, cc) != 0);
	BUILD_BUG_ON(offsetof(struct timecounter, cycle_last) != 8);
	BUILD_BUG_ON(offsetof(struct timecounter, nsec) != 16);
	BUILD_BUG_ON(offsetof(struct timecounter, mask) != 24);
	BUILD_BUG_ON(offsetof(struct timecounter, frac) != 32);
}
