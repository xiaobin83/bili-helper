"""Interactive confirmation prompt for destructive or irreversible operations."""

import threading
from typing import Optional


def confirm_execution(
    preview_text: str,
    timeout: Optional[float] = 60.0,
) -> bool:
    """Print *preview_text* and wait for user confirmation.

    Parameters
    ----------
    preview_text : str
        Description of the action to be confirmed, printed verbatim to stdout.
    timeout : float or None, optional
        Seconds to wait before automatically returning ``False``.
        ``None`` disables the timeout.

    Returns
    -------
    bool
        ``True`` when the user enters ``y`` or ``yes`` (case-insensitive).
        ``False`` for any other input, empty input, or timeout.
    """
    print(preview_text)

    timed_out = threading.Event()
    timer: Optional[threading.Timer] = None

    def _on_timeout() -> None:
        timed_out.set()

    if timeout is not None:
        timer = threading.Timer(timeout, _on_timeout)
        timer.start()

    try:
        raw = input("Proceed? [y/N] ").strip().lower()
        confirmed = raw in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        confirmed = False
    finally:
        if timer is not None:
            timer.cancel()

    # If the timer fired at any point, treat as rejection.
    if timed_out.is_set():
        return False
    return confirmed
