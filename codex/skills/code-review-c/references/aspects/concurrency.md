# Concurrency

Trace writers/readers, synchronization, ownership transfer, queue progress, and shutdown.
For every new wait, enumerate what settles it and whether peer I/O, cancellation or
abort is sequenced after the wait. Compare ordering with base. Verify lifecycle guards
cover setup and alternate writers; supported compile/runtime conditions matter. Show
a reachable interleaving and consequence rather than a hypothetical race.
