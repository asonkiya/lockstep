/* SPDX-License-Identifier: GPL-2.0 */
/* Ring 4 — a REAL in-tree driver's register logic: drivers/gpio/gpio-zevio.c.
 *
 * The register-programming logic (section-offset math, RMW bit ops, the
 * get/set/direction sequences) is the driver's own, verbatim. Only the seam is
 * adapted: the functions take the register base directly instead of recovering
 * it via gpiochip_get_data()/container_of(), and MMIO goes through mmio_r/mmio_w
 * (a recording readl/writel) so the differential oracle can trace it — exactly
 * how a real recorded-MMIO harness would wrap a live driver. Register constants
 * are the driver's real values.
 */
#ifndef ZEVIO_H
#define ZEVIO_H

#include <linux/types.h>
#include <linux/bits.h>

#define ZEVIO_GPIO_SECTION_SIZE	0x40
#define ZEVIO_GPIO_DIRECTION	0x10
#define ZEVIO_GPIO_OUTPUT	0x14
#define ZEVIO_GPIO_INPUT	0x18
#define ZEVIO_GPIO_BIT(pin)	((pin) & 7)

/* recorded MMIO seam (stands in for readl/writel on the ioremap'd base) */
u32 mmio_r(void *base, u32 off);
void mmio_w(void *base, u32 off, u32 val);

/* the reference driver ops (prototypes so -Werror=missing-prototypes is happy) */
int zevio_gpio_get_ref(void *regs, unsigned int pin);
int zevio_gpio_set_ref(void *regs, unsigned int pin, int value);
int zevio_gpio_dir_in_ref(void *regs, unsigned int pin);
int zevio_gpio_dir_out_ref(void *regs, unsigned int pin, int value);

#endif
