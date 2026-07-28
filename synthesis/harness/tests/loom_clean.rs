//! Gate leg: exhaustive race check (the KCSAN analog). loom explores every
//! interleaving of two concurrent pushes; any unsynchronized field access
//! panics ("Causality violation"). The SAME test doubles as the negative
//! control: with the scaffold's lock acquisition sabotaged, it must FAIL.
#![cfg(loom)]

use loom::sync::Arc;
use m3harness::Ring;

#[test]
fn region_is_race_clean() {
    loom::model(|| {
        let r = Arc::new(Ring::new());
        let r2 = Arc::clone(&r);

        let t = loom::thread::spawn(move || {
            r2.push(b'a');
        });
        r.push(b'b');
        t.join().unwrap();

        assert_eq!(r.count(), 2);
    });
}
