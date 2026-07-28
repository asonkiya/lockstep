// PLACEHOLDER — synthesize.py overwrites this file with each model candidate.
// This reference impl (the hand transplant from m2) keeps the crate green so
// the harness itself can be developed and its tests trusted before any model
// output lands. If you see this comment in a gate report, no candidate was
// installed — that is a harness bug, not a solve.
// abstraction: SpinLock<T>

use crate::SpinLock;

pub const SIZE: usize = 64;

pub struct RingFields {
    head: usize,
    count: usize,
    buf: [u8; SIZE],
}

pub struct Ring {
    inner: SpinLock<RingFields>,
}

impl Default for Ring {
    fn default() -> Self {
        Self::new()
    }
}

impl Ring {
    pub fn new() -> Self {
        Ring {
            inner: SpinLock::new(RingFields {
                head: 0,
                count: 0,
                buf: [0; SIZE],
            }),
        }
    }

    pub fn push(&self, c: u8) {
        let mut g = self.inner.lock();
        g.with_mut(|f| {
            let h = f.head % SIZE;
            f.buf[h] = c;
            f.head += 1;
            f.count += 1;
        });
    }

    pub fn count(&self) -> usize {
        let g = self.inner.lock();
        g.with(|f| f.count)
    }
}
