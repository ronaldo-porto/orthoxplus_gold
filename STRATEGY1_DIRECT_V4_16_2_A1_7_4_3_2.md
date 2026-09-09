# Strategy1-Direct V4.16.2 A1.7.4.3.2 — Identity-Safe Ownership Release

A1.7.4.3.2 is a narrow mechanical correction on A1.7.4.3.1. Runtime evidence
showed stale `OrderCancellationsEvent` responses for older exchange order IDs
releasing ownership of a newer pending order on the same book/side. That could
resubmit the newer order and create real 0.50+ BASE same-book stacking.

The repair maintains a bounded exact mapping:

`exchangeOrderId -> (bookId, clientOrderId, side, remaining quantity)`

Cancellation/fill release is authorized only by that exact identity. Unknown or
failed stale cancellation notices are logged and ignored; they never release by
book/side alone. Placement failures may still release by their exact client id.
Account snapshots register acknowledged exchange identities, and exact own fills
reduce only the matching identity/pending reservation.

New diagnostics:

- `A17432_STALE_CANCEL_IGNORED`
- `A17432_IDENTITY_RELEASE`
- `A17432_RELEASE_MISMATCH_BLOCK`

All A1.7.4.3.1 trading economics, 0.25 BASE size, hard 2.0 BASE aggregate cap,
A1.7.4.2 dust-Kappa guard, A1.7.4.1 de-dup, tail recovery, FastPath, and
qualification behavior are frozen.
