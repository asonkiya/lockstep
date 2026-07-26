//! Functional stress harness for the transplant under real OS threads — the
//! "KUnit green" leg of the M2 gate: four writers hammer the ring; the final
//! count must be exact (no lost or torn updates). loom proves race-freedom
//! exhaustively; this proves the transplant actually works under real contention.

use std::sync::Arc;
use std::thread;

use transplant::Ring;

const WRITERS: usize = 4;
const PER_WRITER: usize = 50_000;

fn main() {
    let ring = Arc::new(Ring::new());
    let mut handles = Vec::new();
    for _ in 0..WRITERS {
        let r = Arc::clone(&ring);
        handles.push(thread::spawn(move || {
            for _ in 0..PER_WRITER {
                r.push(b'x');
            }
        }));
    }
    for h in handles {
        h.join().unwrap();
    }

    let got = ring.count();
    let want = WRITERS * PER_WRITER;
    if got != want {
        eprintln!("FAIL: count={got} want={want}");
        std::process::exit(1);
    }
    println!("OK: count={got} (exact under {WRITERS} concurrent writers)");
}
