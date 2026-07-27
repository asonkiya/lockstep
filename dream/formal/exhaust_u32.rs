// Complete exhaustive proof over the FULL 32-bit domain: the model's
// __sw_hweight32 transplant equals the trusted popcount (`u32::count_ones`) for
// every one of the 2^32 inputs. In-kernel exhaustion stopped at 2^16 (a boot is
// too slow for 4 billion iterations); native optimized Rust does the whole
// domain in seconds. count_ones is the trusted oracle, and the kernel's
// __sw_hweight32 is by definition popcount, so cgir == count_ones proves cgir
// equivalent to the kernel C over the entire domain. This is a proof, not a
// sample — the model-checker-free formal tier for a 32-bit-domain function.

// the model's actual transplant (dream/widerun/cand/__sw_hweight32.rs), verbatim
fn cgir_sw_hweight32(mut w: u32) -> u32 {
    w = w.wrapping_sub((w >> 1) & 0x55555555);
    w = (w & 0x33333333).wrapping_add((w >> 2) & 0x33333333);
    w = (w.wrapping_add(w >> 4)) & 0x0f0f0f0f;
    (w.wrapping_mul(0x01010101)) >> 24
}

// a distinct, naive reference popcount — kept behind black_box so the compiler
// cannot fold "cgir == reference" into a tautology; every input is genuinely run.
#[inline(never)]
fn ref_popcount(mut w: u32) -> u32 {
    let mut c = 0u32;
    while w != 0 {
        c += w & 1;
        w >>= 1;
    }
    c
}

fn main() {
    let mut bad: u64 = 0;
    let mut first_bad: i64 = -1;
    let mut x: u32 = 0;
    loop {
        let got = std::hint::black_box(cgir_sw_hweight32(std::hint::black_box(x)));
        let want = std::hint::black_box(ref_popcount(std::hint::black_box(x)));
        if got != want {
            bad += 1;
            if first_bad < 0 {
                first_bad = x as i64;
            }
        }
        if x == u32::MAX {
            break;
        }
        x = x.wrapping_add(1);
    }
    println!(
        "EXHAUST_U32: __sw_hweight32 domain=4294967296 bad={} first_bad={} verdict={}",
        bad,
        first_bad,
        if bad == 0 { "PROVEN" } else { "COUNTEREXAMPLE" }
    );
    std::process::exit(if bad == 0 { 0 } else { 1 });
}
