// SPDX-License-Identifier: GPL-2.0
/* The C reference driver — the canonical device-transfer pattern: stage the
 * operand, issue the command, poll the status bit until ready, read the result.
 * This is the shape of thousands of real driver hot paths. Its register program
 * (this exact sequence) is the spec the transplant must reproduce.
 */
#include "mockdev.h"

u32 mockdev_xfer_ref(struct regmodel *m, u32 input)
{
	reg_write(m, REG_DATA, input);
	reg_write(m, REG_CMD, CMD_START);
	while (reg_read(m, REG_STATUS) & STATUS_BUSY)
		;			/* poll until the device is ready */
	return reg_read(m, REG_DATA);
}
