"""Auto-install missing packages before code execution.

Scans a code block for `import xxx` / `from xxx import yyy` statements,
checks whether the top-level package is available in the subprocess's
Python environment, and pre-installs any missing ones with pip.

This eliminates the common pattern:
  Step N:   import foo → ImportError
  Step N+1: pip install foo
  Step N+2: import foo  (wastes 2 LLM calls)

With auto-install:
  Step N: (auto-detect foo is missing → pip install -q foo) → import foo ✓
"""

from __future__ import annotations

import ast
import importlib.util
import logging
import re
import subprocess
import sys
from typing import Callable, Optional, Set

logger = logging.getLogger(__name__)

# Packages that are part of stdlib or always available — never pip install these.
# This is a conservative list covering Python 3.10-3.12 stdlib top-level names
# plus common builtins that might appear as import targets.
_STDLIB_MODULES: Set[str] = {
    "abc", "aifc", "argparse", "array", "ast", "asynchat", "asyncio",
    "asyncore", "atexit", "audioop", "base64", "bdb", "binascii",
    "binhex", "bisect", "builtins", "bz2", "calendar", "cgi", "cgitb",
    "chunk", "cmath", "cmd", "code", "codecs", "codeop", "collections",
    "colorsys", "compileall", "concurrent", "configparser", "contextlib",
    "contextvars", "copy", "copyreg", "cProfile", "crypt", "csv",
    "ctypes", "curses", "dataclasses", "datetime", "dbm", "decimal",
    "difflib", "dis", "distutils", "doctest", "email", "encodings",
    "enum", "errno", "faulthandler", "fcntl", "filecmp", "fileinput",
    "fnmatch", "fractions", "ftplib", "functools", "gc", "getopt",
    "getpass", "gettext", "glob", "grp", "gzip", "hashlib", "heapq",
    "hmac", "html", "http", "idlelib", "imaplib", "imghdr", "imp",
    "importlib", "inspect", "io", "ipaddress", "itertools", "json",
    "keyword", "lib2to3", "linecache", "locale", "logging", "lzma",
    "mailbox", "mailcap", "marshal", "math", "mimetypes", "mmap",
    "modulefinder", "multiprocessing", "netrc", "nis", "nntplib",
    "numbers", "operator", "optparse", "os", "ossaudiodev", "pathlib",
    "pdb", "pickle", "pickletools", "pipes", "pkgutil", "platform",
    "plistlib", "poplib", "posix", "posixpath", "pprint", "profile",
    "pstats", "pty", "pwd", "py_compile", "pyclbr", "pydoc",
    "queue", "quopri", "random", "re", "readline", "reprlib",
    "resource", "rlcompleter", "runpy", "sched", "secrets", "select",
    "selectors", "shelve", "shlex", "shutil", "signal", "site",
    "smtpd", "smtplib", "sndhdr", "socket", "socketserver", "spwd",
    "sqlite3", "sre_compile", "sre_constants", "sre_parse", "ssl",
    "stat", "statistics", "string", "stringprep", "struct",
    "subprocess", "sunau", "symtable", "sys", "sysconfig", "syslog",
    "tabnanny", "tarfile", "telnetlib", "tempfile", "termios", "test",
    "textwrap", "threading", "time", "timeit", "tkinter", "token",
    "tokenize", "tomllib", "trace", "traceback", "tracemalloc",
    "tty", "turtle", "turtledemo", "types", "typing", "unicodedata",
    "unittest", "urllib", "uu", "uuid", "venv", "warnings", "wave",
    "weakref", "webbrowser", "winreg", "winsound", "wsgiref",
    "xdrlib", "xml", "xmlrpc", "zipapp", "zipfile", "zipimport",
    "zlib", "_thread", "__future__",
}

# Common import aliases that map to different pip package names.
_IMPORT_TO_PIP: dict[str, str] = {
    "cv2": "opencv-python",
    "sklearn": "scikit-learn",
    "PIL": "Pillow",
    "bs4": "beautifulsoup4",
    "yaml": "pyyaml",
    "attr": "attrs",
    "dotenv": "python-dotenv",
    "gi": "PyGObject",
    "wx": "wxPython",
    "skimage": "scikit-image",
    "dateutil": "python-dateutil",
    "jose": "python-jose",
    "magic": "python-magic",
    "docx": "python-docx",
    "pptx": "python-pptx",
    "serial": "pyserial",
    "usb": "pyusb",
    "Bio": "biopython",
}


def extract_imports(code: str) -> Set[str]:
    """Extract top-level package names from import statements in code.

    Uses AST parsing first (reliable), falls back to regex for syntax errors.
    Returns a set of top-level module names (e.g. "pandas", "geopandas").
    """
    packages: Set[str] = set()

    try:
        tree = ast.parse(code)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    packages.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.level == 0:
                    packages.add(node.module.split(".")[0])
    except SyntaxError:
        # Fallback: regex-based extraction for broken code
        import_re = re.compile(
            r"^\s*(?:import|from)\s+([a-zA-Z_][a-zA-Z0-9_]*)",
            re.MULTILINE,
        )
        for m in import_re.finditer(code):
            packages.add(m.group(1))

    return packages


def find_missing_packages(
    packages: Set[str],
    python_executable: str | None = None,
) -> list[str]:
    """Check which packages are not importable and return missing ones.

    Uses importlib.util.find_spec for the current interpreter. If a
    different python_executable is specified, falls back to subprocess check.
    """
    exe = python_executable or sys.executable
    missing: list[str] = []

    for pkg in packages:
        # Skip stdlib
        if pkg in _STDLIB_MODULES:
            continue
        # Skip packages that are injected as skills (not real modules)
        # These will be in the subprocess namespace but not importable normally
        # We'll let them fail at runtime if truly missing.

        if exe == sys.executable:
            # Fast path: same interpreter
            if importlib.util.find_spec(pkg) is None:
                missing.append(pkg)
        else:
            # Different interpreter: ask it
            try:
                result = subprocess.run(
                    [exe, "-c", f"import {pkg}"],
                    capture_output=True,
                    timeout=10,
                )
                if result.returncode != 0:
                    missing.append(pkg)
            except Exception:
                missing.append(pkg)

    return missing


def get_pip_package_name(import_name: str) -> str:
    """Map an import name to its pip package name."""
    return _IMPORT_TO_PIP.get(import_name, import_name)


def build_install_command(
    missing: list[str],
    python_executable: str | None = None,
) -> str:
    """Build a pip install command string for the missing packages."""
    exe = python_executable or sys.executable
    pip_names = [get_pip_package_name(m) for m in missing]
    return f"{exe} -m pip install -q {' '.join(pip_names)}"


def auto_install_missing(
    code: str,
    python_executable: str | None = None,
    progress_callback: Optional[Callable[[str, str], None]] = None,
    timeout: float = 120.0,
) -> Optional[str]:
    """Detect and install missing packages for a code block.

    Returns:
        A summary string of what was installed, or None if nothing was needed.
    """
    packages = extract_imports(code)
    if not packages:
        return None

    missing = find_missing_packages(packages, python_executable)
    if not missing:
        return None

    pip_names = [get_pip_package_name(m) for m in missing]
    logger.info("Auto-installing missing packages: %s (pip names: %s)", missing, pip_names)

    # Notify UI about pip install progress
    if progress_callback:
        try:
            progress_callback(
                "installing_packages",
                f"Installing {', '.join(pip_names)}...",
            )
        except Exception:
            pass

    exe = python_executable or sys.executable
    cmd = [exe, "-m", "pip", "install", "-q"] + pip_names

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode == 0:
            installed_msg = f"Auto-installed: {', '.join(pip_names)}"
            logger.info(installed_msg)
            return installed_msg
        else:
            err = result.stderr.strip() or result.stdout.strip()
            logger.warning("pip install failed (rc=%d): %s", result.returncode, err)
            # Don't block execution — let the code fail naturally with ImportError
            return None
    except subprocess.TimeoutExpired:
        logger.warning("pip install timed out after %.0fs", timeout)
        return None
    except Exception as e:
        logger.warning("pip install error: %s", e)
        return None
