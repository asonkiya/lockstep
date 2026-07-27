/* Minimal userspace shim so the REAL lib/math/gcd.c compiles unchanged (bodies
 * are what we transplant; the kernel entry points are stubbed to their obvious
 * userspace meaning). This lets the gate exercise the actual kernel source. */
#ifndef CLUSTER_KDEFS_H
#define CLUSTER_KDEFS_H

/* __ffs(x): index of least-significant set bit (x != 0), same as the kernel's. */
static inline unsigned long __ffs(unsigned long x) { return __builtin_ctzl(x); }

/* swap(a,b) macro, matching include/linux/minmax.h semantics for our use. */
#define swap(a, b) do { typeof(a) __t = (a); (a) = (b); (b) = __t; } while (0)

/* efficient_ffs_key is DEFINE_STATIC_KEY_TRUE and is never disabled on the
 * arm64 build, so static_branch_likely() is a constant true here. */
#define DEFINE_STATIC_KEY_TRUE(name) int name
#define static_branch_likely(key)    (1)
#define EXPORT_SYMBOL(sym)
#define EXPORT_SYMBOL_GPL(sym)

#endif
