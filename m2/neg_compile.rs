// Compile-time negative control — NOT part of the cargo build. gate.py compiles
// this against the built `transplant` rlib and asserts rustc REJECTS it.
//
// This is the deeper Rust-for-Linux guarantee: in the C stock a "dropped lock" is
// a reviewer's judgment call (did every field access take the spinlock?); in the
// transplant it is a type error. The protected fields live inside `SpinLock<T>`
// and are reachable only through a `Guard`, which only exists after `lock()`.
// There is no safe expression that touches them without the lock.

use transplant::Ring;

fn main() {
    let r = Ring::new();

    // Attempt the "dropped lock": read a protected field without locking.
    // Every line below is a compile error — that is the point.
    let _ = r.inner; // error[E0616]: field `inner` of `Ring` is private
    let _ = r.head; //  error[E0609]: no field `head` on type `Ring`
}
