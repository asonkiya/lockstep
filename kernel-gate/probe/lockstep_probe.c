// SPDX-License-Identifier: GPL-2.0
/* Lockstep M4 in-kernel gate probe.
 *
 * Hammers the transplant seam (lockstep_ring_push — C in the stock leg, the
 * model's Rust in the rewrite leg) from SMP kthreads while a C-side reader
 * takes the SAME real spinlock and reads the protected fields with PLAIN
 * loads. Plain is deliberate twice over: it is correct (the lock serializes),
 * and it is what KCSAN instruments — its watchpoints park on these reads, so
 * an unlocked writer on another CPU (the dropped-lock negative control)
 * changes the value mid-watch and KCSAN reports the race, even though the
 * Rust object itself is uninstrumented. Same detection class as the M0
 * baseline's "race at unknown origin" findings.
 *
 * Console contract (grep keys for gate.sh):
 *   LOCKSTEP_PROBE: count=... head=... expected=... reads=... verdict=FUNC_PASS|FUNC_FAIL
 *   plus any "BUG: KCSAN" whose report window names lockstep_* symbols.
 */
#include <linux/init.h>
#include <linux/kernel.h>
#include <linux/kthread.h>
#include <linux/completion.h>
#include <linux/atomic.h>
#include <linux/spinlock.h>
#include <linux/sched.h>
#include <linux/delay.h>

#include "lockstep_ring.h"

#define PUSHERS 3
#define ITERS   1000000L

static struct ring_fields RF;
static DEFINE_SPINLOCK(ring_lock);

static atomic_t pushers_left = ATOMIC_INIT(PUSHERS);
static DECLARE_COMPLETION(pushers_done);
static long reader_reads;
static long reader_sink;

static int lockstep_pusher(void *arg)
{
	long i;

	for (i = 0; i < ITERS; i++) {
		lockstep_ring_push(&RF, &ring_lock, 'x');
		/* pace the pushers so the stress window stays open long enough
		 * for KCSAN's sampling watchpoints (~1 per 4000 accesses) to
		 * land on the reader's field loads many times. Without this, a
		 * LOCKLESS (buggy) target finishes the whole run in <1s and the
		 * sampler gets ~1 shot — the negative control would then lean
		 * on the functional signal alone. ~20 watchpoints per leg makes
		 * the KCSAN verdict itself decisive, in both directions. */
		udelay(10);
		if ((i & 1023) == 0)
			cond_resched();
	}
	if (atomic_dec_and_test(&pushers_left))
		complete(&pushers_done);
	return 0;
}

static int lockstep_reader(void *arg)
{
	while (!kthread_should_stop()) {
		spin_lock(&ring_lock);
		/* plain reads on purpose — see header comment */
		reader_sink += RF.count + RF.head + RF.buf[0];
		spin_unlock(&ring_lock);
		reader_reads++;
		if ((reader_reads & 511) == 0)
			cond_resched();
	}
	return 0;
}

static int __init lockstep_probe_init(void)
{
	struct task_struct *t, *reader;
	long expected = (long)PUSHERS * ITERS;
	int i;

	pr_emerg("LOCKSTEP_PROBE: starting %d pushers x %ld + reader (SMP=%d)\n",
		 PUSHERS, ITERS, num_online_cpus());

	reader = kthread_create(lockstep_reader, NULL, "lockstep_reader");
	if (IS_ERR(reader))
		return PTR_ERR(reader);
	kthread_bind(reader, 0);
	wake_up_process(reader);

	for (i = 0; i < PUSHERS; i++) {
		t = kthread_create(lockstep_pusher, NULL, "lockstep_push%d", i);
		if (IS_ERR(t))
			return PTR_ERR(t);
		kthread_bind(t, (i + 1) % num_online_cpus());
		wake_up_process(t);
	}

	wait_for_completion(&pushers_done);
	msleep(500);		/* let straggling KCSAN reports flush */
	kthread_stop(reader);

	pr_emerg("LOCKSTEP_PROBE: count=%ld head=%ld expected=%ld reads=%ld verdict=%s\n",
		 RF.count, RF.head, expected, reader_reads,
		 (RF.count == expected && RF.head == expected && reader_reads > 0)
			 ? "FUNC_PASS" : "FUNC_FAIL");
	return 0;
}
late_initcall(lockstep_probe_init);
