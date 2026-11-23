"""Run a job while keeping the laptop from idle-sleeping (systemd-inhibit).

Only blocks *idle* suspend while the job runs; closing the lid still suspends
(logind's LidSwitchIgnoreInhibited=yes default), and when no job runs the
normal power settings apply.
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess


def awake_command(cmd: list[str], why: str) -> list[str]:
    inhibit = shutil.which("systemd-inhibit")
    if not inhibit:
        return cmd
    return [inhibit, "--what=idle:sleep", "--who=Surah channel", f"--why={why}", "--mode=block", *cmd]


def run_awake(cmd: list[str], why: str, timeout: float | None = None, **kwargs) -> subprocess.CompletedProcess[str]:
    """Like subprocess.run(capture_output=True, text=True) but holds a sleep inhibitor.

    Runs in its own process group so a timeout stops the job itself, not just the wrapper.
    If the inhibitor is refused (e.g. right after boot, before the login session is ready,
    polkit answers "Access denied"), the job runs anyway without it instead of failing.
    """
    result = _run(awake_command(cmd, why), timeout, **kwargs)
    if result.returncode != 0 and result.stderr.lstrip().startswith("Failed to inhibit"):
        result = _run(cmd, timeout, **kwargs)
        result.stderr = "keep_awake: sleep inhibitor refused, ran without it\n" + (result.stderr or "")
    return result


def _run(cmd: list[str], timeout: float | None, **kwargs) -> subprocess.CompletedProcess[str]:
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            start_new_session=True, **kwargs)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.communicate()
        raise
    return subprocess.CompletedProcess(proc.args, proc.returncode, out, err)
