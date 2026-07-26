// SPDX-License-Identifier: GPL-2.0
/* Differential-oracle harness (the "oracle-manufacturing" prototype).
 *
 * The dream's research pass found that ~73% of the kernel (mostly drivers) has
 * NO functional oracle — the only gate is "still boots, no new KCSAN," which
 * certifies "didn't crash," not "is correct." A transplant that returns wrong
 * data but doesn't crash sails through. This harness manufactures the missing
 * oracle: it uses the driver's OWN C original as the spec.
 *
 * Method: both implementations are linked in — the C reference (lockstep_phc_*
 * _ref, diff_ref.c) and the candidate transplant (lockstep_phc_*, the model's
 * Rust). A fixed deterministic operation script is run against each, starting
 * from identical state, over a DETERMINISTIC cyclecounter (no ktime_get_raw,
 * so no real-time nondeterminism). Every observable — each gettime return, and
 * tc->nsec / cc->mult after every op — is recorded into a trace vector. The
 * two vectors must be bit-identical. Any divergence (wrong rounding, wrong
 * sign, wrong cast, wrong overflow) is caught, even though neither crashes and
 * neither races.
 *
 * Console contract:
 *   DIFF_PROBE: ops=N ref_hash=0x... cand_hash=0x... firstdiff=IDX verdict=DIFF_PASS|DIFF_FAIL
 */
#include <linux/init.h>
#include <linux/kernel.h>
#include <linux/spinlock.h>
#include <linux/timecounter.h>
#include <linux/clocksource.h>

#define MOCK_PHC_CC_SHIFT	31
#define MOCK_PHC_CC_MULT	(1 << MOCK_PHC_CC_SHIFT)

/* candidate (Rust) and reference (C) seams */
int lockstep_phc_adjfine(struct timecounter *, struct cyclecounter *, void *, long);
int lockstep_phc_adjtime(struct timecounter *, void *, s64);
int lockstep_phc_settime64(struct timecounter *, struct cyclecounter *, void *, u64);
u64 lockstep_phc_gettime64(struct timecounter *, void *);
int lockstep_phc_adjfine_ref(struct timecounter *, struct cyclecounter *, void *, long);
int lockstep_phc_adjtime_ref(struct timecounter *, void *, s64);
int lockstep_phc_settime64_ref(struct timecounter *, struct cyclecounter *, void *, u64);
u64 lockstep_phc_gettime64_ref(struct timecounter *, void *);

/* deterministic clock: advances a fixed step per read, resettable per run, so
 * both implementations see the identical cycle stream */
static u64 g_clock;
static u64 diff_cc_read(struct cyclecounter *cc)
{
	u64 v = g_clock;

	g_clock += 1000;	/* 1us per tick, deterministic */
	return v;
}

enum { OP_GET, OP_ADJFINE, OP_ADJTIME, OP_SETTIME };

struct op {
	int kind;
	s64 arg;
};

/* A fixed script exercising all four regions with values chosen to stress the
 * arithmetic the transplant rewrote: signed/negative adjfine (mult cast),
 * adjtime deltas (signed nsec add), settime jumps (re-init), interleaved
 * gettimes (accumulation). Deterministic, seed-free. */
#define NOPS 64
static struct op script[NOPS];

static void build_script(void)
{
	int i;

	for (i = 0; i < NOPS; i++) {
		switch (i % 8) {
		case 0: script[i] = (struct op){ OP_SETTIME, 1000000LL + i * 777LL }; break;
		case 1: script[i] = (struct op){ OP_GET, 0 }; break;
		case 2: script[i] = (struct op){ OP_ADJFINE, ((i % 2001) - 1000) << 16 }; break;
		case 3: script[i] = (struct op){ OP_GET, 0 }; break;
		case 4: script[i] = (struct op){ OP_ADJTIME, (i & 1) ? 1234 : -567 }; break;
		case 5: script[i] = (struct op){ OP_GET, 0 }; break;
		case 6: script[i] = (struct op){ OP_ADJFINE, -((i * 131) % 900) << 16 }; break;
		case 7: script[i] = (struct op){ OP_GET, 0 }; break;
		}
	}
}

/* 3 observables per op */
#define TRACE_LEN (NOPS * 3)

static void run(bool ref, u64 *trace)
{
	struct cyclecounter cc = {
		.read = diff_cc_read,
		.mask = CLOCKSOURCE_MASK(64),
		.mult = MOCK_PHC_CC_MULT,
		.shift = MOCK_PHC_CC_SHIFT,
	};
	struct timecounter tc;
	DEFINE_SPINLOCK(lock);
	int i, t = 0;
	u64 ret;

	g_clock = 0;			/* identical deterministic input per run */
	timecounter_init(&tc, &cc, 0);

	for (i = 0; i < NOPS; i++) {
		struct op *o = &script[i];

		ret = 0;
		switch (o->kind) {
		case OP_GET:
			ret = ref ? lockstep_phc_gettime64_ref(&tc, &lock)
				  : lockstep_phc_gettime64(&tc, &lock);
			break;
		case OP_ADJFINE:
			if (ref)
				lockstep_phc_adjfine_ref(&tc, &cc, &lock, o->arg);
			else
				lockstep_phc_adjfine(&tc, &cc, &lock, o->arg);
			break;
		case OP_ADJTIME:
			if (ref)
				lockstep_phc_adjtime_ref(&tc, &lock, o->arg);
			else
				lockstep_phc_adjtime(&tc, &lock, o->arg);
			break;
		case OP_SETTIME:
			if (ref)
				lockstep_phc_settime64_ref(&tc, &cc, &lock, o->arg);
			else
				lockstep_phc_settime64(&tc, &cc, &lock, o->arg);
			break;
		}
		trace[t++] = ret;		/* gettime return (0 for non-get) */
		trace[t++] = tc.nsec;		/* accumulated time state */
		trace[t++] = cc.mult;		/* rate state */
	}
}

static u64 fnv1a(const u64 *v, int n)
{
	u64 h = 0xcbf29ce484222325ULL;
	int i;

	for (i = 0; i < n; i++) {
		h ^= v[i];
		h *= 0x100000001b3ULL;
	}
	return h;
}

static u64 ref_trace[TRACE_LEN];
static u64 cand_trace[TRACE_LEN];

static int __init diff_probe_init(void)
{
	int i, firstdiff = -1;
	u64 rh, ch;

	build_script();
	run(true, ref_trace);		/* the C original — the spec */
	run(false, cand_trace);		/* the candidate transplant */

	for (i = 0; i < TRACE_LEN; i++) {
		if (ref_trace[i] != cand_trace[i]) {
			firstdiff = i;
			break;
		}
	}
	rh = fnv1a(ref_trace, TRACE_LEN);
	ch = fnv1a(cand_trace, TRACE_LEN);

	pr_emerg("DIFF_PROBE: ops=%d ref_hash=0x%llx cand_hash=0x%llx firstdiff=%d verdict=%s\n",
		 NOPS, rh, ch, firstdiff,
		 (firstdiff < 0) ? "DIFF_PASS" : "DIFF_FAIL");
	if (firstdiff >= 0)
		pr_emerg("DIFF_PROBE: at trace[%d] op#%d field=%s ref=%llu cand=%llu\n",
			 firstdiff, firstdiff / 3,
			 (const char *[]){ "gettime", "nsec", "mult" }[firstdiff % 3],
			 ref_trace[firstdiff], cand_trace[firstdiff]);
	return 0;
}
late_initcall(diff_probe_init);
