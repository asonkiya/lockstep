//! The M2 gate's race oracle: loom exhaustively explores thread interleavings of
//! the transplant and reports any data race — the userspace stand-in for KCSAN,
//! and stronger (exhaustive, not sampled). Built only under `--cfg loom`:
//!
//!     RUSTFLAGS="--cfg loom" cargo test --test loom_checks
//!
//! Two checks, mirroring the design's M2 proof:
//!   * `transplant_is_race_clean` — the `SpinLock<T>` transplant: no race, and the
//!     count is exact across every interleaving.
//!   * `dropped_lock_is_rejected` — the negative control: the same push with the
//!     lock dropped MUST be flagged by loom (the panic is the rejection).
#![cfg(loom)]

use loom::sync::Arc;
use transplant::Ring;

#[test]
fn transplant_is_race_clean() {
    loom::model(|| {
        let r = Arc::new(Ring::new());
        let r2 = Arc::clone(&r);

        let t = loom::thread::spawn(move || {
            r2.push(b'a');
        });
        r.push(b'b');
        t.join().unwrap();

        // functional invariant holds under every interleaving loom picks
        assert_eq!(r.count(), 2);
    });
}

#[test]
fn dropped_lock_is_rejected() {
    // Negative control: two writers touch the fields with NO lock held. loom must
    // detect the data race and panic; if it did not, the gate would be vacuous.
    // loom's own hook prints the panic, so silence it and capture the reason.
    let reason = std::sync::Arc::new(std::sync::Mutex::new(String::new()));
    let r2 = std::sync::Arc::clone(&reason);
    std::panic::set_hook(Box::new(move |info| {
        *r2.lock().unwrap() = info.to_string();
    }));
    let rejected = std::panic::catch_unwind(|| {
        loom::model(|| {
            let r = Arc::new(Ring::new());
            let r2 = Arc::clone(&r);

            let t = loom::thread::spawn(move || {
                r2.push_racy(b'a');
            });
            r.push_racy(b'b');
            t.join().unwrap();
        });
    })
    .is_err();
    let _ = std::panic::take_hook();

    assert!(
        rejected,
        "loom did NOT reject the dropped-lock transplant — the race oracle is vacuous"
    );
    // ...and it must reject for the RIGHT reason: a concurrent-access race, not
    // some incidental panic (the negative-control discipline from M1).
    let msg = reason.lock().unwrap().clone();
    assert!(
        msg.contains("Concurrent") && msg.contains("UnsafeCell"),
        "rejected, but not for a data race — loom said: {msg:?}"
    );
}
