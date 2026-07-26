/* SPDX-License-Identifier: GPL-2.0 */
/* The M4-breadth transplant seam: ptp_mock's locked cluster (4 regions sharing
 * mock_phc.lock protecting tc/cc), on component pointers.
 *
 * The container_of / timespec64 glue stays C-side (probe): it is registration
 * plumbing, not the region. What crosses the seam is exactly the protected
 * state (tc, cc), the real lock (opaque), and the region inputs. Exactly one
 * definition set links per leg: lockstep_phc_target.c (stock C, verbatim
 * region bodies) or the model's Rust object.
 */
#ifndef LOCKSTEP_PHC_H
#define LOCKSTEP_PHC_H

#include <linux/timecounter.h>

int lockstep_phc_adjfine(struct timecounter *tc, struct cyclecounter *cc,
			 void *lock, long scaled_ppm);
int lockstep_phc_adjtime(struct timecounter *tc, void *lock, s64 delta);
int lockstep_phc_settime64(struct timecounter *tc, struct cyclecounter *cc,
			   void *lock, u64 ns);
u64 lockstep_phc_gettime64(struct timecounter *tc, void *lock);

#endif
