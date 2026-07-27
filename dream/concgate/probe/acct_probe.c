// SPDX-License-Identifier: GPL-2.0
/* Concurrency gate probe: hammer the acct critical section from SMP kthreads and
 * judge the transplant two ways at once:
 *   (a) KCSAN — a reader kthread holds the lock and reads count/mirror with plain
 *       loads; a correct transplant serializes all access (clean), a transplant
 *       that lets `mirror` escape the lock races the reader's mirror read and
 *       KCSAN reports it, naming acct_reader / the escaped field;
 *   (b) the coupled invariant — every lock-held read must see mirror == count;
 *       a narrowed critical section breaks it (the reader sees count advanced but
 *       mirror not yet), which the probe counts directly.
 * Two independent oracles for one subtle bug. udelay pacing keeps KCSAN's
 * sampling window open (the M4 lesson). Which impl is linked is chosen by the
 * gate (stock C / correct Rust / subtle Rust).
 *
 * Console: ACCT_PROBE: count=.. mirror=.. expected=.. inv_violations=.. verdict=FUNC_PASS|FUNC_FAIL
 */
#include <linux/init.h>
#include <linux/kernel.h>
#include <linux/kthread.h>
#include <linux/completion.h>
#include <linux/atomic.h>
#include <linux/spinlock.h>
#include <linux/delay.h>
#include "acct.h"

#ifdef ACCT_USE_RUST
#define ACCT_ADD(f, l, d) cgir_acct_add((f), (l), (d))
#else
#define ACCT_ADD(f, l, d) acct_add_ref((f), (l), (d))
#endif

#define PUSHERS 4
#define ITERS   400000L

static struct acct_fields AF;
static DEFINE_SPINLOCK(acct_lock);
static atomic_t left = ATOMIC_INIT(PUSHERS);
static DECLARE_COMPLETION(done);
static long inv_violations, reader_reads, sink;

static int pusher(void *arg)
{
	long i;

	for (i = 0; i < ITERS; i++) {
		ACCT_ADD(&AF, &acct_lock, 1);
		udelay(2);			/* keep the KCSAN window open */
		if ((i & 1023) == 0)
			cond_resched();
	}
	if (atomic_dec_and_test(&left))
		complete(&done);
	return 0;
}

static int reader(void *arg)
{
	while (!kthread_should_stop()) {
		long c, m;

		spin_lock(&acct_lock);
		c = AF.count;			/* plain reads under the lock */
		m = AF.mirror;
		spin_unlock(&acct_lock);
		if (c != m)			/* coupled-invariant violation */
			inv_violations++;
		sink += c + m;
		reader_reads++;
		if ((reader_reads & 511) == 0)
			cond_resched();
	}
	return 0;
}

static int __init acct_probe_init(void)
{
	struct task_struct *t, *rd;
	long expected = (long)PUSHERS * ITERS;
	int i;
	bool pass;

	rd = kthread_create(reader, NULL, "acct_reader");
	if (IS_ERR(rd))
		return PTR_ERR(rd);
	kthread_bind(rd, 0);
	wake_up_process(rd);

	for (i = 0; i < PUSHERS; i++) {
		t = kthread_create(pusher, NULL, "acct_push%d", i);
		if (IS_ERR(t))
			return PTR_ERR(t);
		kthread_bind(t, (i + 1) % num_online_cpus());
		wake_up_process(t);
	}
	wait_for_completion(&done);
	msleep(300);
	kthread_stop(rd);

	pass = (AF.count == expected) && (AF.mirror == AF.count) &&
	       (inv_violations == 0) && (reader_reads > 0);
	pr_emerg("ACCT_PROBE: count=%ld mirror=%ld expected=%ld inv_violations=%ld reads=%ld verdict=%s\n",
		 AF.count, AF.mirror, expected, inv_violations, reader_reads,
		 pass ? "FUNC_PASS" : "FUNC_FAIL");
	return 0;
}
late_initcall(acct_probe_init);
