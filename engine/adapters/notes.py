# Antechamber — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""notes — a folder of Markdown (or plain text) notes, searched by meaning and by words.

    [[adapters.notes]]
    id = "notes"                  # immutable; names the index file and the fusion key
    name = "My notes"             # shown only
    folder = "~/notes"            # relative paths are read from the config file's folder
    glob = "**/*.md"
    max_file_bytes = 2000000      # larger files are skipped (logged)
    max_passages = 2000           # per file
    limit = 20                    # items handed to recall per question

A passage is a paragraph under its nearest heading, cut at spaces to at most 500 characters. Its date is the
note's front-matter `date:` or, failing that, the file's modification time (UTC).

Only regular files inside the folder are read: symbolic links are not followed, hidden folders are skipped,
files with NUL bytes (binary) are skipped.
"""
import hashlib
import logging
import os
import re
import stat

from . import embedders, index as ix
from .item import normalize_date

log = logging.getLogger("antechamber.adapters")

SPLITTER_VERSION = 1
PASSAGE_CHARS = 500
_NONBLOCK = getattr(os, "O_NONBLOCK", 0)
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
MIN_CHARS = 20
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


def glob_regex(pattern):
    """A glob over '/'-separated relative paths: '*' stays inside one folder, '**/' spans any folders (or none)."""
    out, i = "", 0
    while i < len(pattern):
        c = pattern[i]
        if pattern.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
        elif pattern.startswith("**", i):
            out += ".*"
            i += 2
        elif c == "*":
            out += "[^/]*"
            i += 1
        elif c == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(c)
            i += 1
    return re.compile("^" + out + "$")


def front_matter(text):
    """(fields, body): a leading '---' block's simple `key: value` lines, and the text after it."""
    m = re.match(r"^---[ \t]*\n(.*?)\n---[ \t]*(?:\n|$)", text, re.S)
    if not m:
        return {}, text
    fields = {}
    for line in m.group(1).splitlines():
        kv = re.match(r"^([A-Za-z_][\w-]*)\s*:\s*(.*?)\s*$", line)
        if kv:
            fields[kv.group(1).lower()] = kv.group(2).strip("'\"")
    return fields, text[m.end():]


def passages(text):
    """Split a note into (heading, passage) pairs: paragraphs under their nearest heading, each <= 500 chars,
    cut at a space, never mid-word."""
    out, head = [], ""
    for block in re.split(r"\n\s*\n", text):
        b = block.strip()
        if not b:
            continue
        m = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", b.split("\n", 1)[0])
        if m:
            head = m.group(2).strip()[:120]
            b = b.split("\n", 1)[1].strip() if "\n" in b else ""
            if not b:
                continue
        if len(b) < MIN_CHARS:
            continue
        while b:
            if len(b) <= PASSAGE_CHARS:
                out.append((head, b))
                break
            cut = b.rfind(" ", 0, PASSAGE_CHARS)
            cut = cut if cut > PASSAGE_CHARS // 2 else PASSAGE_CHARS
            out.append((head, b[:cut].rstrip()))
            b = b[cut:].lstrip()
    return out


def _inside(root, path):
    try:
        return os.path.commonpath([root, path]) == root
    except ValueError:
        return False


def _parts_below(roots, path):
    """The path components of `path` below the first of `roots` that holds it; None when none does."""
    p = os.path.abspath(path)
    for r in roots:
        if p != r and _inside(r, p):
            parts = os.path.relpath(p, r).split(os.sep)
            if all(x not in ("", ".", "..") for x in parts):
                return parts
    return None


def open_below(root, parts):
    """Open root/parts... for reading, one component at a time from the root's open folder, refusing a symbolic
    link at EVERY step (no path is ever resolved, so nothing can be swapped in between). Returns a descriptor."""
    dfd = os.open(root, os.O_RDONLY | _DIRECTORY)
    try:
        for name in parts[:-1]:    # folders: O_DIRECTORY | O_NOFOLLOW
            nfd = os.open(name, os.O_RDONLY | _DIRECTORY | _NOFOLLOW, dir_fd=dfd)
            os.close(dfd)
            dfd = nfd
        # the leaf: O_NOFOLLOW but NOT O_DIRECTORY (it is a file); S_ISREG is checked by the caller on the descriptor
        return os.open(parts[-1], os.O_RDONLY | _NOFOLLOW | _NONBLOCK, dir_fd=dfd)
    finally:
        os.close(dfd)


def make_opener(root):
    """path -> binary file object opened by open_below, regular files only; OSError otherwise. Index hashes through
    this, so hashing and reading take the same safe path."""
    real_root = os.path.realpath(root)
    roots = (real_root, os.path.abspath(root))

    def opener(path):
        parts = _parts_below(roots, path)
        if parts is None:
            raise PermissionError(f"{path} is not inside the folder")
        f = os.fdopen(open_below(real_root, parts), "rb")
        if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
            f.close()
            raise OSError(f"{path} is not a regular file")
        return f
    opener.parts = lambda path: _parts_below(roots, path)
    return opener


def scan(root, pattern, max_file_bytes):
    """{absolute path: (mtime, size)} of the regular files under root matching pattern."""
    rx, found = glob_regex(pattern), {}
    root = os.path.realpath(root)
    for d, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(x for x in dirs if not x.startswith(".")
                         and not os.path.islink(os.path.join(d, x)))
        for name in sorted(files):
            p = os.path.join(d, name)
            rel = os.path.relpath(p, root).replace(os.sep, "/")
            if not rx.match(rel):
                continue
            try:
                st = os.lstat(p)
            except OSError:
                continue
            if not stat.S_ISREG(st.st_mode):        # symbolic links, devices, pipes: never read
                continue
            if not _inside(root, os.path.realpath(p)):
                log.warning("notes: %s resolves outside %s; skipped", p, root)
                continue
            if st.st_size > max_file_bytes:
                log.warning("notes: %s is %d bytes (limit %d); skipped", p, st.st_size, max_file_bytes)
                continue
            found[p] = (st.st_mtime, st.st_size)
    return found


def make_reader(root, max_file_bytes, strict=False):
    """strict=True: a refusal (outside the folder, a link, not a regular file, grew) raises OSError so the index
    keeps the file's last version; the default returns [] and logs."""
    opener = make_opener(root)

    def refuse(msg):
        if strict:
            raise OSError(msg)
        log.warning("notes: %s", msg)
        return []

    def read(path):
        parts = opener.parts(path)
        if parts is None:
            return refuse(f"{path} is not inside the folder; skipped")
        try:   # every folder on the way and the file itself are opened without following a link; regular files only
            f = opener(path)
        except OSError as e:
            return refuse(f"{path} not opened ({e.strerror or e}); skipped")
        with f:
            raw = f.read(max_file_bytes + 1)
            mtime = os.fstat(f.fileno()).st_mtime
        if len(raw) > max_file_bytes:
            return refuse(f"{path} grew past {max_file_bytes} bytes; skipped")
        if b"\x00" in raw[:8192]:
            log.warning("notes: %s looks binary; skipped", path)
            return []
        text = raw.decode("utf-8-sig", errors="replace")
        fields, body = front_matter(text)
        rel = "/".join(parts)
        date = normalize_date(fields.get("date")) or normalize_date(mtime)
        h1 = re.search(r"^#\s+(.+)$", body, re.M)
        title = fields.get("title") or (h1.group(1).strip() if h1 else os.path.splitext(os.path.basename(rel))[0])
        out, used = [], set()
        for n, (head, txt) in enumerate(passages(body)):
            pid = rel + "#" + hashlib.sha256((head + "\n" + txt).encode()).hexdigest()[:12]
            if pid in used:
                pid += f"-{n}"
            used.add(pid)
            out.append({"id": pid, "text": txt, "date": date, "source": rel, "title": title, "section": head or None})
        return out
    return read


def resolve(base_dir, p):
    p = os.path.expanduser(p)
    return os.path.realpath(p if os.path.isabs(p) else os.path.join(base_dir, p))


def base_dir_of(cfg):
    return (cfg or {}).get("_config_dir") or os.getcwd()


def from_config(cfg, embedder=None):
    """[[adapters.notes]] tables -> one shelf each. `embedder` overrides the [embedder] table (tests, demos)."""
    insts = ((cfg or {}).get("adapters") or {}).get("notes") or []
    if not insts:
        return []
    base = base_dir_of(cfg)
    if embedder is None:
        embedder = embedders.shared(cfg)
    shelves = []
    for inst in insts:
        iid, name = ix.instance_ids(inst, "notes")
        if not inst.get("folder"):
            raise embedders.ConfigError(f"[[adapters.notes]] {iid}: folder is missing")
        root = resolve(base, inst["folder"])
        if not os.path.isdir(root):
            raise embedders.ConfigError(f"[[adapters.notes]] {iid}: folder {root} does not exist")
        pattern = inst.get("glob", "**/*.md")
        mfb = int(inst.get("max_file_bytes", 2_000_000))
        fp = {"adapter": "notes", "root": root, "glob": pattern, "max_file_bytes": mfb,
              "max_passages": int(inst.get("max_passages", 2000)), "splitter": SPLITTER_VERSION}
        index = ix.Index(ix.index_path(cfg, iid, base), fp, embedder, lambda r=root, p=pattern, m=mfb: scan(r, p, m),
                         make_reader(root, mfb, strict=True), max_passages=fp["max_passages"],
                         opener=make_opener(root), max_bytes=mfb)
        shelves.append(ix.local_shelf(iid, index, embedder, lambda r, i=iid, n=name: ix.row_item(i, r, n),
                                      int(inst.get("limit", 20))))
    return shelves

