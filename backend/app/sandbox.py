"""The sandbox gate for untrusted code execution.

Hard guardrail: the Python kernel (and the AI agent) must only
ever execute inside the microVM — never on the host in production. This module
is the single place that decides whether code execution is permitted, so the
policy can't drift across call sites.

- Inside the VM, `entrypoint.sh` exports `SMOLDUCK_IN_VM=1`; the kernel is on.
- On the host it is OFF by default. A developer doing native dev can opt in with
  `SMOLDUCK_ALLOW_HOST_KERNEL=1`, but it can never be the default outside the VM.
"""

from __future__ import annotations

import os

IN_VM_ENV = "SMOLDUCK_IN_VM"
ALLOW_HOST_ENV = "SMOLDUCK_ALLOW_HOST_KERNEL"


def in_vm() -> bool:
    return os.environ.get(IN_VM_ENV) == "1"


def kernel_enabled() -> bool:
    """True only inside the VM, or when a developer explicitly opts in on the host."""
    if in_vm():
        return True
    return os.environ.get(ALLOW_HOST_ENV) == "1"


def kernel_disabled_reason() -> str:
    return (
        "the Python kernel runs untrusted code and is only enabled inside the "
        "smolduck microVM. (Native dev: set SMOLDUCK_ALLOW_HOST_KERNEL=1 to opt in.)"
    )
