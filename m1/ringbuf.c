/* M1 cross-check subject: a spinlock-protected ring buffer.
 *
 * This ONE file is read two ways:
 *   (1) statically, by extract.py — which sees kernel-style `spinlock_t` /
 *       `spin_lock(&r->lock)` names (shimmed to pthreads below) and builds the
 *       `protects` map;
 *   (2) dynamically, compiled with -fsanitize=thread and hammered by the harness
 *       in main() — TSan then OBSERVES which fields actually race.
 *
 * The M1 proof (design.md §4) is that (1) agrees with (2): every field the map
 * calls protected is race-clean under load, and the field it flags unprotected
 * (`name`, deliberately touched with no lock held) is the one TSan races on.
 * TSan is the userspace stand-in for KCSAN/lockdep; the discipline is identical.
 */
#include <pthread.h>
#include <stdio.h>
#include <string.h>

/* kernel-name shim: extract.py sees spinlock_t / spin_lock, the compiler sees
 * pthread mutexes (portable, and TSan models them as happens-before). */
typedef pthread_mutex_t spinlock_t;
#define spin_lock(l)   pthread_mutex_lock(l)
#define spin_unlock(l) pthread_mutex_unlock(l)

#define SIZE 64

struct ring {
	spinlock_t lock;
	int head;
	int count;
	char buf[SIZE];
	const char *name;
};

void ring_push(struct ring *r, char c)
{
	spin_lock(&r->lock);
	r->buf[r->head % SIZE] = c;
	r->head++;
	r->count++;
	spin_unlock(&r->lock);
}

int ring_count(struct ring *r)
{
	int n;
	spin_lock(&r->lock);
	n = r->count;
	spin_unlock(&r->lock);
	return n;
}

/* THE DELIBERATE BUG: name is written with no lock held. The static map must
 * flag it unprotected, and TSan must race on it at runtime. */
void ring_set_name(struct ring *r, const char *nm)
{
	r->name = nm;
}

/* ---- runtime harness (not part of the IR subject; extract ignores main) ---- */

#define ITERS 20000

static struct ring R;

static void *pusher(void *arg)
{
	long id = (long)arg;
	for (int i = 0; i < ITERS; i++) {
		ring_push(&R, (char)('a' + (id & 15)));
		if ((i & 1023) == 0)
			(void)ring_count(&R);
	}
	return NULL;
}

static void *namer(void *arg)
{
	const char *names[] = {"alpha", "bravo", "charlie", "delta"};
	for (int i = 0; i < ITERS; i++)
		ring_set_name(&R, names[i & 3]);
	return NULL;
}

int main(void)
{
	pthread_mutex_init(&R.lock, NULL);
	R.name = "init";

	pthread_t t[6];
	/* 4 pushers race on head/count/buf — but always under the lock */
	for (long i = 0; i < 4; i++)
		pthread_create(&t[i], NULL, pusher, (void *)i);
	/* 2 namers race on ->name with no lock — the deliberate data race */
	for (int i = 4; i < 6; i++)
		pthread_create(&t[i], NULL, namer, NULL);

	for (int i = 0; i < 6; i++)
		pthread_join(t[i], NULL);

	printf("count=%d name=%s\n", ring_count(&R), R.name);
	return 0;
}
