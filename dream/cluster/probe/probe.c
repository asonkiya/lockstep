/* Differential driver: the exported entry only. `gcd` resolves to the woven
 * shell (-> cgir_gcd, the Rust cluster); `gcd_ref` is the stock C original
 * (built from the same source with -Dgcd=gcd_ref, carrying its OWN static
 * binary_gcd). We never call binary_gcd by name — it is private in Rust and
 * gone from the woven C — yet driving `gcd` over a wide domain exercises it. */
#include <stdio.h>
#include <stdint.h>

extern unsigned long gcd(unsigned long a, unsigned long b);      /* woven -> Rust */
extern unsigned long gcd_ref(unsigned long a, unsigned long b);  /* stock C */

int main(void)
{
	unsigned long cases = 0, bad = 0;
	unsigned long first_a = 0, first_b = 0, first_got = 0, first_exp = 0;

	/* edges: zeros, ones, equal, powers of two, adjacent, coprime */
	static const unsigned long edge[] = {
		0, 1, 2, 3, 4, 6, 7, 8, 12, 15, 16, 17, 24, 36, 48, 1024,
		1u << 20, 0xFFFFFFFFUL, 0xFFFFFFFEUL, 0x100000000UL,
	};
	const int E = (int)(sizeof(edge) / sizeof(edge[0]));
	for (int i = 0; i < E; i++)
		for (int j = 0; j < E; j++) {
			unsigned long a = edge[i], b = edge[j];
			unsigned long got = gcd(a, b), exp = gcd_ref(a, b);
			cases++;
			if (got != exp && bad++ == 0) {
				first_a = a; first_b = b; first_got = got; first_exp = exp;
			}
		}

	/* deterministic pseudo-random pairs (fixed seed -> reproducible) */
	uint64_t s = 0x9E3779B97F4A7C15ULL;
	for (long k = 0; k < 2000000; k++) {
		s ^= s << 13; s ^= s >> 7; s ^= s << 17;   /* xorshift64 */
		unsigned long a = (unsigned long)(s >> 1);
		s ^= s << 13; s ^= s >> 7; s ^= s << 17;
		unsigned long b = (unsigned long)(s >> 1);
		unsigned long got = gcd(a, b), exp = gcd_ref(a, b);
		cases++;
		if (got != exp && bad++ == 0) {
			first_a = a; first_b = b; first_got = got; first_exp = exp;
		}
	}

	printf("CLUSTER cases=%lu bad=%lu verdict=%s\n",
	       cases, bad, bad ? "DIVERGE" : "MATCH");
	if (bad)
		printf("  first divergence: gcd(%lu,%lu) got=%lu exp=%lu\n",
		       first_a, first_b, first_got, first_exp);
	return bad ? 1 : 0;
}
