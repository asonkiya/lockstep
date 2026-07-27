// SPDX-License-Identifier: GPL-2.0
/* Stock C reference — the correct critical section: both coupled fields updated
 * under the lock. The KCSAN-clean baseline the transplant must match. */
#include <linux/spinlock.h>
#include "acct.h"

void acct_add_ref(struct acct_fields *f, void *lock, long delta)
{
	spinlock_t *l = lock;

	spin_lock(l);
	f->count += delta;
	f->mirror += delta;
	spin_unlock(l);
}
