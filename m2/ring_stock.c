/* M2 stock C: the critical section as it exists before transplant — a
 * spinlock-protected ring buffer, clean (no bug). This is the reference the
 * gate diffs against: the transplant must be race-clean *where stock is clean*.
 * Built under -fsanitize=thread and hammered by four writers; TSan reports zero
 * races (the lock is honored), giving the clean baseline.
 *
 * kernel-name shim so this reads like kernel C (spinlock_t / spin_lock); the
 * compiler sees pthread mutexes, which TSan models as happens-before.
 */
#include <pthread.h>
#include <stdio.h>

typedef pthread_mutex_t spinlock_t;
#define spin_lock(l)   pthread_mutex_lock(l)
#define spin_unlock(l) pthread_mutex_unlock(l)

#define SIZE 64

struct ring {
	spinlock_t lock;
	int head;
	int count;
	char buf[SIZE];
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

#define WRITERS 4
#define ITERS 50000

static struct ring R;

static void *writer(void *arg)
{
	(void)arg;
	for (int i = 0; i < ITERS; i++)
		ring_push(&R, 'x');
	return NULL;
}

int main(void)
{
	pthread_mutex_init(&R.lock, NULL);
	pthread_t t[WRITERS];
	for (int i = 0; i < WRITERS; i++)
		pthread_create(&t[i], NULL, writer, NULL);
	for (int i = 0; i < WRITERS; i++)
		pthread_join(t[i], NULL);

	int got = ring_count(&R), want = WRITERS * ITERS;
	printf("count=%d want=%d %s\n", got, want, got == want ? "OK" : "FAIL");
	return got == want ? 0 : 1;
}
