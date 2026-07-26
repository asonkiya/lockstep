/* SPDX-License-Identifier: GPL-2.0 */
/* Ring 3 — the register-programming seam.
 *
 * reg_read/reg_write stand in for a driver's MMIO accessors (readl/writel on an
 * ioremap'd base). A real driver's meaning IS its register programming — the
 * ORDER of writes, the poll on a status bit, which register the result is read
 * from. This seam lets the differential oracle intercept and record every
 * access, so the transplant is judged on the register trace it produces, not
 * just its return value. (Same shape a real recorded-MMIO harness would take,
 * with a software device model standing in for hardware QEMU doesn't have.)
 */
#ifndef MOCKDEV_H
#define MOCKDEV_H

#include <linux/types.h>

#define REG_DATA	0x00	/* w: operand; r: result after command */
#define REG_CMD		0x04	/* w: command */
#define REG_STATUS	0x08	/* r: busy/ready */

#define CMD_START	0x1u
#define STATUS_BUSY	0x1u

struct regmodel;

/* the MMIO seam the driver programs through */
u32 reg_read(struct regmodel *m, u32 off);
void reg_write(struct regmodel *m, u32 off, u32 val);

#endif
