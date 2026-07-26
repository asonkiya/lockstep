// SPDX-License-Identifier: GPL-2.0
/* The C original of int_sqrt as the reference oracle (renamed _ref so it can be
 * linked alongside the transplant). Body verbatim from lib/math/int_sqrt.c. */
#include <linux/bitops.h>
#include <linux/kernel.h>

unsigned long int_sqrt_ref(unsigned long x)
{
	unsigned long b, m, y = 0;

	if (x <= 1)
		return x;

	m = 1UL << (__fls(x) & ~1UL);
	while (m != 0) {
		b = y + m;
		y >>= 1;
		if (x >= b) {
			x -= b;
			y += m;
		}
		m >>= 2;
	}
	return y;
}
