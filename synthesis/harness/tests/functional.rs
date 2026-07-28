//! Gate leg: functional correctness under real contention ("KUnit green").
//! 4 writers x 50k pushes; the final count must be exact — a lost or torn
//! update fails the candidate.
#![cfg(not(loom))]

use std::sync::Arc;
use std::thread;

use m3harness::Ring;

#[test]
fn exact_count_under_contention() {
    const WRITERS: usize = 4;
    const PER: usize = 50_000;

    let ring = Arc::new(Ring::new());
    let handles: Vec<_> = (0..WRITERS)
        .map(|_| {
            let r = Arc::clone(&ring);
            thread::spawn(move || {
                for _ in 0..PER {
                    r.push(b'x');
                }
            })
        })
        .collect();
    for h in handles {
        h.join().unwrap();
    }
    assert_eq!(ring.count(), WRITERS * PER);
}
