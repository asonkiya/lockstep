// abstraction: spin_lock/unlock around fields -> SpinLock<Fields> + guard scope
use crate::SpinLock;

struct RingBuffer {
    head: usize,
    count: usize,
    buf: [u8; 64],
}

pub struct Ring {
    inner: SpinLock<RingBuffer>,
}

impl Ring {
    pub fn new() -> Ring {
        Ring {
            inner: SpinLock::new(RingBuffer {
                head: 0,
                count: 0,
                buf: [0; 64],
            }),
        }
    }

    pub fn push(&self, c: u8) {
        let mut guard = self.inner.lock();
        guard.with_mut(|rb| {
            rb.buf[rb.head % 64] = c;
            rb.head += 1;
            rb.count += 1;
        });
    }

    pub fn count(&self) -> usize {
        let guard = self.inner.lock();
        guard.with(|rb| rb.count)
    }
}

impl Default for Ring {
    fn default() -> Self {
        Self::new()
    }
}
