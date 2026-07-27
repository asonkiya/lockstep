/* SPDX-License-Identifier: GPL-2.0 */
/* Concurrency-gate subject: a spinlock-protected coupled invariant. Two fields
 * that must always move together (mirror == count) — the archetype of shared
 * state a critical section protects. The transplant seam takes the real kernel
 * spinlock (opaque) + the fields; the correct rewrite updates both inside one
 * guard scope, a subtly-wrong rewrite lets one field escape the lock. */
#ifndef CONCGATE_ACCT_H
#define CONCGATE_ACCT_H
#include <linux/types.h>

struct acct_fields {
	long count;
	long mirror;   /* invariant: mirror == count at every lock release */
};

/* the transplant seam (Rust provides cgir_acct_add; C stock provides acct_add_ref) */
void acct_add_ref(struct acct_fields *f, void *lock, long delta);
void cgir_acct_add(struct acct_fields *f, void *lock, long delta);

#endif
