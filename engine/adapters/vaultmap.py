# Antechamber — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""vaultmap — an Obsidian (or any markdown) vault, searched through vault-map (https://github.com/SolanceLab/vault-map).

    [[adapters.vaultmap]]
    id = "vault"                  # immutable; the fusion key namespace (reserve seats with
                                  # [recall] extra_seat_shelves = ["vault"])
    name = "My vault"             # shown only, and the source label
    folder = "~/Documents/Vault"  # the vault (vault-map reads it; nothing is written into it)
    vault_map = "~/tools/vault-map.py"   # the vault-map.py file to use
    vault_map_sha256 = ""         # optional: refuse any other vault-map bytes (pin a release)
    limit = 0                     # optional cap on results handed to recall (0 = recall's pool, as measured)

Measured shape (Antechamber's House vault test, 20 Oct): 3 x recall's pool asked, unusable paths dropped, the pool
handed to fusion, each item's text = the snippet only. This adapter also sends the section heading as `section`.

With a vault-map that has section search (search_sections), every result is the SECTION that answers, cut to its
best sentence window (about 150 characters); with an older vault-map, its whole-note search and first-hit snippet
are used. The section index lives in vault-map's own cache (~/.cache/vault-map) and is refreshed in the
background, never inside a recall. vault-map is read once from the configured file; when a sha256 is given, the
same bytes that were checked are the ones run.

The shelf also carries vault_folder, vault_map and vault_map_sha256, so an add-on can open whole notes from the
same folder with the same vault-map.
"""
import hashlib
import logging
import os
import threading
import types

from . import index as ix
from .item import make_item

log = logging.getLogger("antechamber.adapters")


class VaultMapError(RuntimeError):
    pass


def load_vaultmap(path, sha256=""):
    """vault-map as a module, from exactly the bytes read (and checked, when a sha256 is given)."""
    with open(path, "rb") as fh:
        src = fh.read()
    if sha256 and hashlib.sha256(src).hexdigest() != sha256.lower():
        raise VaultMapError(f"vault-map at {path} does not match vault_map_sha256: refusing to run it")
    mod = types.ModuleType("vaultmap_adapter_vm")
    mod.__file__ = path
    exec(compile(src, path, "exec"), mod.__dict__)
    return mod


class SectionIndex:
    """Holds vault-map's in-memory section index; refresh() rebuilds it (incremental via vault-map's cache)."""

    def __init__(self, vm, folder):
        self.vm, self.folder, self.ix, self.lock = vm, folder, None, threading.Lock()

    def refresh(self):
        built = self.vm.section_index(self.folder)
        with self.lock:
            self.ix = built
        return {"sections": built["n"]}

    def current(self):
        with self.lock:
            return self.ix


def split_vault_id(item_id):
    """A vault item id is '<path>#<section>', and a file name may itself hold '#'. Candidate paths, LONGEST first,
    so 'a.md#b.md#Sec' tries 'a.md#b.md' before 'a.md' (Codex review, 5 Nov: shortest-first opened the wrong note)."""
    return [item_id[:p] for p in range(len(item_id) - 1, -1, -1) if item_id[p] == "#"]


def to_items(results, iid, name):
    out = []
    for r in results:
        text = (r.get("snippet") or "").strip()
        if not text:
            continue
        sec = r.get("section") or None
        out.append(dict(make_item({"id": f'{r.get("path")}#{sec or ""}', "text": text, "date": r.get("modified"),
                                   "source": name, "title": r.get("title"), "section": sec,
                                   "metadata": {"path": r.get("path"), "also_in": r.get("also_in") or []}}),
                        _ns=iid))
    return out


def from_config(cfg, embedder=None):
    insts = ((cfg or {}).get("adapters") or {}).get("vaultmap") or []
    if not insts:
        return []
    from shelves import FnShelf
    from query import terms
    base = (cfg or {}).get("_config_dir") or os.getcwd()
    shelves = []
    for inst in insts:
        iid, name = ix.instance_ids(inst, "vaultmap")
        folder = os.path.expanduser(str(inst.get("folder") or ""))
        vm_path = os.path.expanduser(str(inst.get("vault_map") or ""))
        if not folder or not vm_path:
            raise VaultMapError(f"[[adapters.vaultmap]] {iid}: folder and vault_map are both required")
        folder = folder if os.path.isabs(folder) else os.path.join(base, folder)
        vm_path = vm_path if os.path.isabs(vm_path) else os.path.join(base, vm_path)
        if not os.path.isdir(folder):
            raise VaultMapError(f"[[adapters.vaultmap]] {iid}: folder {folder} does not exist")
        vm = load_vaultmap(vm_path, str(inst.get("vault_map_sha256") or ""))
        cap = int(inst.get("limit", 0) or 0)
        sections = hasattr(vm, "search_sections") and hasattr(vm, "section_index")
        idx = SectionIndex(vm, folder) if sections else None

        def search(ctx, vm=vm, folder=folder, idx=idx, cap=cap, iid=iid, name=name):
            # As measured: ask for 3 x recall's pool, drop unusable paths, hand recall the pool (default 20).
            pool = int(getattr(ctx, "pool", 0) or 20)
            limit = min(pool, cap) if cap > 0 else pool
            t = terms(getattr(ctx, "q", "") or "")
            if not t:
                return []
            if idx is not None:
                cur = idx.current()
                if cur is None:
                    return []                     # not refreshed yet; /health is not ok until it is
                res = vm.search_sections(t, folder, pool * 3, index=cur)
            else:
                res = vm.search(t, folder, pool * 3) or {}
            hits = [r for r in res.get("results", []) if r.get("path") and not os.path.isabs(str(r["path"]))]
            return to_items(hits[:limit], iid, name)

        shelf = FnShelf(iid, search, iid, "id")
        shelf.vault_folder = folder   # read by add-ons that open a whole note; recall never uses it
        shelf.vault_map = vm   # read by add-ons that open a whole note; recall never uses it
        shelf.vault_map_sha256 = str(inst.get("vault_map_sha256") or "").lower()   # read by add-ons that open a whole note; recall never uses it
        from .notes import make_open_note
        shelf.open = make_open_note(
            folder, "**/*.md", 2_000_000, split=split_vault_id)   # read only by the MCP open_item tool
        if idx is not None:
            shelf.index = idx
        shelves.append(shelf)
    return shelves
