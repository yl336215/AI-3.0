"""Same-directory publication locking for formal raw-data targets."""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
import errno
import os
from pathlib import Path
import stat
import sys

if os.name == "nt":
    import msvcrt
else:
    import fcntl


@dataclass(frozen=True)
class PublicationLock:
    path: Path
    descriptor: int


def acquire_publication_lock(target: Path) -> PublicationLock:
    """Prevent concurrent AI-3.0 writers from publishing the same target."""

    lock_path = target.with_name(f".{target.name}.ai3-publish.lock")
    flags = os.O_CREAT | os.O_RDWR
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(lock_path, flags, 0o600)
    try:
        if os.name == "nt":
            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"\0")
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError) as exc:
        os.close(descriptor)
        raise FileExistsError(f"another publication is active for: {target}") from exc
    except BaseException:
        os.close(descriptor)
        raise
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise RuntimeError(f"unsafe publication lock path: {lock_path}")
    except BaseException:
        os.close(descriptor)
        raise
    return PublicationLock(lock_path, descriptor)


def publish_noreplace(partial: Path, target: Path) -> None:
    """Atomically expose ``partial`` without ever replacing ``target``.

    Both paths must be on the same filesystem. Creating the hard link is one
    atomic no-replace operation: even a non-AI-3.0 writer racing with
    publication wins with ``EEXIST`` instead of being overwritten. The
    excluded partial name is then removed on a best-effort basis.
    """

    if sys.platform == "darwin":
        library = ctypes.CDLL(None, use_errno=True)
        rename_exclusive = getattr(library, "renamex_np", None)
        if rename_exclusive is not None:
            rename_exclusive.argtypes = [
                ctypes.c_char_p,
                ctypes.c_char_p,
                ctypes.c_uint,
            ]
            rename_exclusive.restype = ctypes.c_int
            # Darwin RENAME_EXCL is an atomic rename that fails if target
            # exists, and works on filesystems such as exFAT without hardlinks.
            if rename_exclusive(
                os.fsencode(partial), os.fsencode(target), 0x00000004
            ) == 0:
                return
            error_number = ctypes.get_errno()
            if error_number == errno.EEXIST:
                raise FileExistsError(
                    error_number,
                    f"formal target already exists: {target}",
                    str(target),
                )
            raise OSError(
                error_number,
                f"filesystem does not support safe no-replace publication: {target}",
                str(target),
            )

    try:
        os.link(partial, target, follow_symlinks=False)
    except FileExistsError as exc:
        raise FileExistsError(f"formal target already exists: {target}") from exc
    except OSError as exc:
        raise OSError(
            f"filesystem does not support safe no-replace publication: {target}"
        ) from exc
    try:
        partial.unlink(missing_ok=True)
    except OSError:
        # The formal target now refers to the verified inode. A leftover
        # partial name is excluded from scans and must not invalidate success.
        pass


def release_publication_lock(lock: PublicationLock) -> None:
    """Best-effort unlock without masking an already published file.

    The small lock file intentionally remains in place. The kernel lock, not
    file existence, owns exclusivity, so a killed process releases it
    automatically and cannot permanently block retry.
    """

    try:
        if os.name == "nt":
            os.lseek(lock.descriptor, 0, os.SEEK_SET)
            msvcrt.locking(lock.descriptor, msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(lock.descriptor, fcntl.LOCK_UN)
    except OSError:
        pass
    finally:
        try:
            os.close(lock.descriptor)
        except OSError:
            pass


__all__ = [
    "PublicationLock",
    "acquire_publication_lock",
    "publish_noreplace",
    "release_publication_lock",
]
