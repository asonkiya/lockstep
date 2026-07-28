// SPDX-License-Identifier: GPL-2.0
/* M4-breadth gate probe: hammer ptp_mock's whole locked cluster concurrently.
 *
 * Five SMP kthreads drive all four transplanted regions at once — 2x gettime,
 * 1x adjtime(+1000ns), 1x adjfine (sweeping ±1000ppm), 1x settime (forward
 * 10s jumps) — while a C reader takes the SAME real spinlock and reads
 * tc.nsec / tc.cycle_last / cc.mult with PLAIN loads (correct under the lock,
 * and KCSAN-instrumented: its watchpoints are the trap an unlocked transplant
 * walks into — the M4-depth mechanism).
 *
 * Functional oracle: PTP-clock invariants under full concurrency —
 *   * gettime is monotone nondecreasing per observer (settime only jumps
 *     FORWARD by 10s, far more than wall time between jumps; adjtime deltas
 *     are positive; mult stays positive) — a torn timecounter state shows up
 *     as a backward jump;
 *   * every hammer completes its exact op count;
 *   * the reader actually read (no vacuous pass).
 *
 * Console contract: LOCKSTEP_PHC: ... verdict=FUNC_PASS|FUNC_FAIL
 * udelay(10) pacing per op keeps the stress window open for KCSAN's sampler
 * (the run-1 lesson from the depth leg).
 */
#include <linux/init.h>
#include <linux/kernel.h>
#include <linux/kthread.h>
#include <linux/completion.h>
#include <linux/atomic.h>
#include <linux/spinlock.h>
#include <linux/timecounter.h>
#include <linux/timekeeping.h>
#include <linux/clocksource.h>
#include <linux/delay.h>

#include "lockstep_phc.h"

#define MOCK_PHC_CC_SHIFT	31
#define MOCK_PHC_CC_MULT	(1 << MOCK_PHC_CC_SHIFT)

#define HAMMER_ITERS	150000L
#define SETTIME_ITERS	2000L
#define SETTIME_JUMP	(10ULL * NSEC_PER_SEC)

static struct timecounter TC;
static struct cyclecounter CC;
static DEFINE_SPINLOCK(phc_lock);

static atomic_t hammers_left = ATOMIC_INIT(5);
static DECLARE_COMPLETION(hammers_done);

static long gettime_ok[2], monotone_violations;
static long adjtime_done, adjfine_done, settime_done;
static long reader_reads, reader_sink;

/* the driver's cyclecounter read callback (mock_phc_cc_read), verbatim */
static u64 probe_cc_read(struct cyclecounter *cc)
{
	return ktime_get_raw_ns();
}

static void hammer_exit(void)
{
	if (atomic_dec_and_test(&hammers_left))
		complete(&hammers_done);
}

static int gettime_hammer(void *arg)
{
	long idx = (long)arg;
	u64 last = 0, ns;
	long i;

	for (i = 0; i < HAMMER_ITERS; i++) {
		ns = lockstep_phc_gettime64(&TC, &phc_lock);
		if (ns < last)
			monotone_violations++;	/* this thread's private view */
		else
			gettime_ok[idx]++;
		last = ns;
		udelay(10);
		if ((i & 1023) == 0)
			cond_resched();
	}
	hammer_exit();
	return 0;
}

static int adjtime_hammer(void *arg)
{
	long i;

	for (i = 0; i < HAMMER_ITERS; i++) {
		lockstep_phc_adjtime(&TC, &phc_lock, 1000);
		adjtime_done++;
		udelay(10);
		if ((i & 1023) == 0)
			cond_resched();
	}
	hammer_exit();
	return 0;
}

static int adjfine_hammer(void *arg)
{
	long i, scaled_ppm;

	for (i = 0; i < HAMMER_ITERS; i++) {
		/* sweep ±1000 ppm (scaled_ppm is ppm << 16), well inside
		 * MOCK_PHC_MAX_ADJ_PPB — mult stays positive */
		scaled_ppm = ((i % 2001) - 1000) << 16;
		lockstep_phc_adjfine(&TC, &CC, &phc_lock, scaled_ppm);
		adjfine_done++;
		udelay(10);
		if ((i & 1023) == 0)
			cond_resched();
	}
	hammer_exit();
	return 0;
}

static int settime_hammer(void *arg)
{
	u64 base = SETTIME_JUMP;
	long i;

	for (i = 0; i < SETTIME_ITERS; i++) {
		base += SETTIME_JUMP;	/* strictly forward, >> wall time between calls */
		lockstep_phc_settime64(&TC, &CC, &phc_lock, base);
		settime_done++;
		usleep_range(10000, 20000);
	}
	hammer_exit();
	return 0;
}

static int phc_reader(void *arg)
{
	while (!kthread_should_stop()) {
		spin_lock(&phc_lock);
		/* plain reads on purpose — KCSAN watchpoint bait */
		reader_sink += TC.nsec + TC.cycle_last + CC.mult;
		spin_unlock(&phc_lock);
		reader_reads++;
		if ((reader_reads & 511) == 0)
			cond_resched();
	}
	return 0;
}

static int __init lockstep_phc_probe_init(void)
{
	struct task_struct *t, *reader;
	int cpu = 0, i;
	bool pass;

	/* the Rust mirror assumes these exact layouts — fail the BUILD if the
	 * kernel's structs ever drift */
	BUILD_BUG_ON(sizeof(struct timecounter) != 40);
	BUILD_BUG_ON(sizeof(struct cyclecounter) != 24);
	BUILD_BUG_ON(offsetof(struct timecounter, nsec) != 16);
	BUILD_BUG_ON(offsetof(struct cyclecounter, mult) != 16);

	/* replicate mock_phc_create's init, minus PTP-class registration
	 * (no userspace in this boot; the regions are what is under test) */
	CC.read = probe_cc_read;
	CC.mask = CLOCKSOURCE_MASK(64);
	CC.mult = MOCK_PHC_CC_MULT;
	CC.shift = MOCK_PHC_CC_SHIFT;
	timecounter_init(&TC, &CC, 0);

	pr_emerg("LOCKSTEP_PHC: starting 5 hammers + reader over 4 regions (SMP=%d)\n",
		 num_online_cpus());

	reader = kthread_create(phc_reader, NULL, "lockstep_phcread");
	if (IS_ERR(reader))
		return PTR_ERR(reader);
	kthread_bind(reader, 0);
	wake_up_process(reader);

	{
		struct task_struct *(mk[5]);
		mk[0] = kthread_create(gettime_hammer, (void *)0L, "lockstep_get0");
		mk[1] = kthread_create(gettime_hammer, (void *)1L, "lockstep_get1");
		mk[2] = kthread_create(adjtime_hammer, NULL, "lockstep_adjt");
		mk[3] = kthread_create(adjfine_hammer, NULL, "lockstep_adjf");
		mk[4] = kthread_create(settime_hammer, NULL, "lockstep_sett");
		for (i = 0; i < 5; i++) {
			t = mk[i];
			if (IS_ERR(t))
				return PTR_ERR(t);
			kthread_bind(t, (++cpu) % num_online_cpus());
			wake_up_process(t);
		}
	}

	wait_for_completion(&hammers_done);
	msleep(500);
	kthread_stop(reader);

	pass = (monotone_violations == 0) &&
	       (gettime_ok[0] + gettime_ok[1] == 2 * HAMMER_ITERS) &&
	       (adjtime_done == HAMMER_ITERS) &&
	       (adjfine_done == HAMMER_ITERS) &&
	       (settime_done == SETTIME_ITERS) &&
	       (reader_reads > 0);

	pr_emerg("LOCKSTEP_PHC: gettime_ok=%ld/%ld violations=%ld adjtime=%ld adjfine=%ld settime=%ld reads=%ld verdict=%s\n",
		 gettime_ok[0] + gettime_ok[1], 2 * HAMMER_ITERS,
		 monotone_violations, adjtime_done, adjfine_done, settime_done,
		 reader_reads, pass ? "FUNC_PASS" : "FUNC_FAIL");
	return 0;
}
late_initcall(lockstep_phc_probe_init);
