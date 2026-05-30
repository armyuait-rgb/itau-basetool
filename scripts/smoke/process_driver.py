from __future__ import annotations

import subprocess
import threading
from typing import Callable


def start_output_reader(proc: subprocess.Popen[str], sink: list[str]) -> threading.Thread:
    def _reader() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            sink.append(line)

    thread = threading.Thread(target=_reader, daemon=True)
    thread.start()
    return thread


def tail_output(chunks: list[str], limit: int = 20) -> str:
    if not chunks:
        return ""
    return "".join(chunks[-limit:])


def write_command(
    proc: subprocess.Popen[str],
    command: str,
    *,
    output_chunks: list[str] | None = None,
    label: str | None = None,
) -> None:
    if proc.stdin is None:
        raise RuntimeError(_format_command_error(label, command, output_chunks, "stdin pipe unavailable"))

    if proc.poll() is not None:
        raise RuntimeError(
            _format_command_error(
                label,
                command,
                output_chunks,
                f"process already exited with code {proc.returncode}",
            )
        )

    try:
        proc.stdin.write(command)
        proc.stdin.flush()
    except (BrokenPipeError, OSError, ValueError) as exc:
        raise RuntimeError(_format_command_error(label, command, output_chunks, str(exc))) from exc


def write_commands(
    proc: subprocess.Popen[str],
    commands: list[str],
    *,
    output_chunks: list[str] | None = None,
    label: str | None = None,
) -> None:
    for command in commands:
        write_command(proc, command, output_chunks=output_chunks, label=label)


def shutdown_runner(
    proc: subprocess.Popen[str],
    *,
    output_chunks: list[str] | None = None,
    wait_timeout: float = 15,
) -> None:
    write_commands(
        proc,
        ["stop\n", "exit\n"],
        output_chunks=output_chunks,
        label="shutdown",
    )
    if proc.stdin is not None:
        try:
            proc.stdin.close()
        except OSError:
            pass
    proc.wait(timeout=wait_timeout)


def _format_command_error(
    label: str | None,
    command: str,
    output_chunks: list[str] | None,
    reason: str,
) -> str:
    prefix = f"{label}: " if label else ""
    rendered = command.replace("\n", "\\n")
    tail = tail_output(output_chunks or [])
    if tail:
        return f"{prefix}failed to send {rendered!r}: {reason}\nrecent output:\n{tail}"
    return f"{prefix}failed to send {rendered!r}: {reason}"


def poll_or_raise(
    proc: subprocess.Popen[str],
    *,
    output_chunks: list[str] | None = None,
    context: str = "runner",
) -> None:
    code = proc.poll()
    if code is None:
        return
    tail = tail_output(output_chunks or [])
    message = f"{context} exited early with code {code}"
    if tail:
        message = f"{message}\nrecent output:\n{tail}"
    raise RuntimeError(message)
