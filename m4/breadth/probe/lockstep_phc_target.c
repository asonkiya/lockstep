// SPDX-License-Identifier: GPL-2.0
/* Stock leg: ptp_mock's four regions VERBATIM (drivers/ptp/ptp_mock.c),
 * restructured only at the seam (component pointers instead of container_of;
 * timespec glue lives in the probe). The locking and region bodies are the
 * driver's own.
 */
#include <linux/spinlock.h>
#include <linux/math64.h>
#include "lockstep_phc.h"

#define MOCK_PHC_CC_SHIFT		31
#define MOCK_PHC_CC_MULT		(1 << MOCK_PHC_CC_SHIFT)
#define MOCK_PHC_FADJ_SHIFT		9
#define MOCK_PHC_FADJ_DENOMINATOR	15625ULL

int lockstep_phc_adjfine(struct timecounter *tc, struct cyclecounter *cc,
			 void *lock, long scaled_ppm)
{
	spinlock_t *l = lock;
	s64 adj;

	adj = (s64)scaled_ppm << MOCK_PHC_FADJ_SHIFT;
	adj = div_s64(adj, MOCK_PHC_FADJ_DENOMINATOR);

	spin_lock(l);
	timecounter_read(tc);
	cc->mult = MOCK_PHC_CC_MULT + adj;
	spin_unlock(l);

	return 0;
}

int lockstep_phc_adjtime(struct timecounter *tc, void *lock, s64 delta)
{
	spinlock_t *l = lock;

	spin_lock(l);
	timecounter_adjtime(tc, delta);
	spin_unlock(l);

	return 0;
}

int lockstep_phc_settime64(struct timecounter *tc, struct cyclecounter *cc,
			   void *lock, u64 ns)
{
	spinlock_t *l = lock;

	spin_lock(l);
	timecounter_init(tc, cc, ns);
	spin_unlock(l);

	return 0;
}

u64 lockstep_phc_gettime64(struct timecounter *tc, void *lock)
{
	spinlock_t *l = lock;
	u64 ns;

	spin_lock(l);
	ns = timecounter_read(tc);
	spin_unlock(l);

	return ns;
}
