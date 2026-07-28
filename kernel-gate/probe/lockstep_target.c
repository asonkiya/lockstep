// SPDX-License-Identifier: GPL-2.0
/* Stock leg: the region as C — the reference the transplant replaces. */
#include <linux/spinlock.h>
#include "lockstep_ring.h"

void lockstep_ring_push(struct ring_fields *f, void *lock, unsigned char c)
{
	spinlock_t *l = lock;

	spin_lock(l);
	f->buf[f->head % LOCKSTEP_RING_SIZE] = c;
	f->head++;
	f->count++;
	spin_unlock(l);
}
