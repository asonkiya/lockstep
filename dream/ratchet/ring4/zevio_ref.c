// SPDX-License-Identifier: GPL-2.0
/* The C reference — gpio-zevio's register logic verbatim (seam adapted to take
 * `regs` directly and use the mmio_r/mmio_w recording accessor; the spinlock is
 * dropped since the differential probe is single-threaded and Ring 4 is about
 * the register PROGRAM, not the locking — that was Rings 0/2/M4). */
#include "zevio.h"

static u32 zevio_port_get(void *regs, unsigned int pin, unsigned int port_offset)
{
	unsigned int section_offset = ((pin >> 3) & 3) * ZEVIO_GPIO_SECTION_SIZE;

	return mmio_r(regs, section_offset + port_offset);
}

static void zevio_port_set(void *regs, unsigned int pin, unsigned int port_offset, u32 val)
{
	unsigned int section_offset = ((pin >> 3) & 3) * ZEVIO_GPIO_SECTION_SIZE;

	mmio_w(regs, section_offset + port_offset, val);
}

int zevio_gpio_get_ref(void *regs, unsigned int pin)
{
	u32 val, dir;

	dir = zevio_port_get(regs, pin, ZEVIO_GPIO_DIRECTION);
	if (dir & BIT(ZEVIO_GPIO_BIT(pin)))
		val = zevio_port_get(regs, pin, ZEVIO_GPIO_INPUT);
	else
		val = zevio_port_get(regs, pin, ZEVIO_GPIO_OUTPUT);

	return (val >> ZEVIO_GPIO_BIT(pin)) & 0x1;
}

int zevio_gpio_set_ref(void *regs, unsigned int pin, int value)
{
	u32 val;

	val = zevio_port_get(regs, pin, ZEVIO_GPIO_OUTPUT);
	if (value)
		val |= BIT(ZEVIO_GPIO_BIT(pin));
	else
		val &= ~BIT(ZEVIO_GPIO_BIT(pin));
	zevio_port_set(regs, pin, ZEVIO_GPIO_OUTPUT, val);

	return 0;
}

int zevio_gpio_dir_in_ref(void *regs, unsigned int pin)
{
	u32 val;

	val = zevio_port_get(regs, pin, ZEVIO_GPIO_DIRECTION);
	val |= BIT(ZEVIO_GPIO_BIT(pin));
	zevio_port_set(regs, pin, ZEVIO_GPIO_DIRECTION, val);

	return 0;
}

int zevio_gpio_dir_out_ref(void *regs, unsigned int pin, int value)
{
	u32 val;

	val = zevio_port_get(regs, pin, ZEVIO_GPIO_OUTPUT);
	if (value)
		val |= BIT(ZEVIO_GPIO_BIT(pin));
	else
		val &= ~BIT(ZEVIO_GPIO_BIT(pin));
	zevio_port_set(regs, pin, ZEVIO_GPIO_OUTPUT, val);

	val = zevio_port_get(regs, pin, ZEVIO_GPIO_DIRECTION);
	val &= ~BIT(ZEVIO_GPIO_BIT(pin));
	zevio_port_set(regs, pin, ZEVIO_GPIO_DIRECTION, val);

	return 0;
}
