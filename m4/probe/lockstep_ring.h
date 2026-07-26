/* SPDX-License-Identifier: GPL-2.0 */
/* The shared region subject: the ring's protected fields, C-defined, layout
 * shared with the Rust transplant (#[repr(C)] RingFields mirrors this exactly:
 * i64, i64, [u8;64] on LP64 arm64). The spinlock stays OUTSIDE this struct and
 * is passed as an opaque pointer — the transplant calls the kernel's real
 * out-of-line _raw_spin_lock/_raw_spin_unlock on it, so lockdep sees every
 * acquisition regardless of which language took the lock.
 */
#ifndef LOCKSTEP_RING_H
#define LOCKSTEP_RING_H

#define LOCKSTEP_RING_SIZE 64

struct ring_fields {
	long head;
	long count;
	unsigned char buf[LOCKSTEP_RING_SIZE];
};

/* The transplant seam. Exactly one definition links in per leg:
 *   stock leg   -> lockstep_target.c (C, spin_lock)
 *   rewrite leg -> the model's Rust object (.o_shipped)
 */
void lockstep_ring_push(struct ring_fields *f, void *lock, unsigned char c);

#endif
