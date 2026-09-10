"""Make stdout survive a console that is not UTF-8.

On Windows Python encodes stdout with the *console* code page, not UTF-8:
cp1252 under Git Bash, cp936 under a Chinese cmd.exe. Every script here prints
Chinese - shot headlines, chart labels, check names - so on such a console the
`print` itself raises UnicodeEncodeError.

That failure is worse than it looks. It does not happen at startup where a
missing dependency would; it happens at the first Chinese character, which is
partway through a build, *after* the narration and the images have been paid
for. The traceback names charmap.py, so it reads as a Python bug rather than a
terminal setting, and the half-built project is left behind with no summary.

Importing this module reconfigures both streams once. Characters the terminal
genuinely cannot draw degrade to replacements instead of killing the run.
"""
import sys


def _utf8(stream):
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:                      # a pipe or a captured buffer
        return
    try:
        reconfigure(encoding="utf-8", errors="replace")
    except (ValueError, OSError):
        # Already detached, or a stream that will not be reconfigured. Printing
        # is not worth aborting over - the caller has real work to do.
        pass


for _stream in (sys.stdout, sys.stderr):
    _utf8(_stream)
