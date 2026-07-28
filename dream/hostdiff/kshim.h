/* kshim.h — minimal host shim so REAL kernel TUs compile as userspace on an
 * LP64 host whose ABI matches the arm64 target (unsigned long = 64-bit, int =
 * 32-bit; this Mac is arm64, same widths as the kernel build). The hostdiff
 * harness strips `#include <linux/...>` from the TU and injects this instead.
 * Grow it per family, like the mirrors: each addition unlocks every TU that
 * needed it. Anything NOT shimable (per-cpu, RCU, locks) is out of T0's scope
 * by the purity router anyway. */
#ifndef HOSTDIFF_KSHIM_H
#define HOSTDIFF_KSHIM_H

#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdlib.h>

/* kernel fixed-width names */
typedef uint8_t  u8;  typedef int8_t  s8;
typedef uint16_t u16; typedef int16_t s16;
typedef uint32_t u32; typedef int32_t s32;
typedef uint64_t u64; typedef int64_t s64;
typedef u8 __u8; typedef u16 __u16; typedef u32 __u32; typedef u64 __u64;
typedef s8 __s8; typedef s16 __s16; typedef s32 __s32; typedef s64 __s64;

/* bit helpers (LP64: unsigned long is 64-bit, matching arm64) */
static inline unsigned long __ffs(unsigned long x) { return (unsigned long)__builtin_ctzl(x); }
static inline unsigned long __fls(unsigned long x) { return 63ul - (unsigned long)__builtin_clzl(x); }
static inline int fls(unsigned int x)  { return x ? 32 - __builtin_clz(x) : 0; }
static inline int ffs(int x)           { return __builtin_ffs(x); }
static inline int fls64(u64 x)         { return x ? 64 - __builtin_clzll(x) : 0; }
#define BIT(n)          (1UL << (n))
#define BIT_ULL(n)      (1ULL << (n))
#define GENMASK(h, l)   (((~0UL) << (l)) & (~0UL >> (63 - (h))))
#define BITS_PER_LONG   64

/* generic helpers */
#define swap(a, b)      do { typeof(a) __t = (a); (a) = (b); (b) = __t; } while (0)
#define min(a, b)       ((a) < (b) ? (a) : (b))
#define max(a, b)       ((a) > (b) ? (a) : (b))
#define min_t(t, a, b)  ((t)(a) < (t)(b) ? (t)(a) : (t)(b))
#define max_t(t, a, b)  ((t)(a) > (t)(b) ? (t)(a) : (t)(b))
#define clamp(v, lo, hi) min(max(v, lo), hi)
#define clamp_t(t, v, lo, hi) min_t(t, max_t(t, v, lo), hi)
#define ARRAY_SIZE(a)   (sizeof(a) / sizeof((a)[0]))
#define DIV_ROUND_UP(n, d)      (((n) + (d) - 1) / (d))
#define DIV_ROUND_CLOSEST(n, d) (((n) + (d) / 2) / (d))
/* kernel abs() is type-generic; host <stdlib.h> abs suffices for int, and the
 * pure-math TUs T0 targets use it on longs via labs-compatible widths */
#define abs(x)          _Generic((x), int: abs, long: labs, long long: llabs)(x)

/* annotations / no-ops on host */
#define likely(x)       (x)
#define unlikely(x)     (x)
#define __always_inline inline
#define __maybe_unused  __attribute__((unused))
#undef __pure           /* Darwin sys/cdefs.h defines it; kernel meaning wins */
#define __pure          __attribute__((pure))
#define __attribute_const__ __attribute__((const))
#define __init
#define __exit
#define __user
#define __force
#define __must_check
#define noinline        __attribute__((noinline))
#define EXPORT_SYMBOL(s)
#define EXPORT_SYMBOL_GPL(s)
#define MODULE_LICENSE(s)
#define MODULE_AUTHOR(s)
#define MODULE_DESCRIPTION(s)

/* static keys: DEFINE_STATIC_KEY_TRUE keys read as enabled, FALSE as disabled —
 * matches the arm64 defaults for the pure-math TUs T0 targets. */
#define DEFINE_STATIC_KEY_TRUE(name)  int name = 1
#define DEFINE_STATIC_KEY_FALSE(name) int name = 0
#define static_branch_likely(k)   (1)
#define static_branch_unlikely(k) (0)

/* UAPI-stable constants: fcntl O_* flags (asm-generic, arm64) and errno values.
 * These are ABI-frozen — identical across every arch and kernel version — so
 * hardcoding them is FAITHFUL, not a config assumption. (Config-DEPENDENT
 * constants like XA_CHUNK_SHIFT are deliberately NOT here — those route to the
 * in-kernel gate where the real value is used.) */
#define O_RDONLY   00000000
#define O_WRONLY   00000001
#define O_RDWR     00000002
#define O_CREAT    00000100
#define O_EXCL     00000200
#define O_TRUNC    00001000
#define O_APPEND   00002000
#define O_NONBLOCK 00004000
#define O_DSYNC    00010000
#define O_DIRECT   00040000
#define O_LARGEFILE 00100000
#define O_DIRECTORY 00200000
#define O_NOFOLLOW  00400000
#define O_CLOEXEC  02000000
#define EPERM 1
#define ENOENT 2
#define EIO 5
#define EBADF 9
#define EAGAIN 11
#define ENOMEM 12
#define EACCES 13
#define EFAULT 14
#define EBUSY 16
#define EEXIST 17
#define ENODEV 19
#define EINVAL 22
#define ENFILE 23
#define ENOSPC 28
#define EROFS 30
#define ERANGE 34
#define ENOSYS 38
#define EOPNOTSUPP 95

/* sanity traps (a pure fn hitting these on host = real divergence anyway) */
#define BUG()           abort()
#define BUG_ON(c)       do { if (c) abort(); } while (0)
#define WARN_ON(c)      (c)
#define WARN_ON_ONCE(c) (c)

#endif /* HOSTDIFF_KSHIM_H */
