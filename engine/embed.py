# Antechamber — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""The embedder call: one question in, one vector out. Backend from [embedder] in the settings file."""
import json, urllib.request
import config as C


def embed(text, timeout=30):
    req = urllib.request.Request(C.EMBED_URL, data=json.dumps({"model": C.EMBED_MODEL, "input": text[:2000],
                                 "dimensions": C.EMBED_DIMS, "keep_alive": -1}).encode(), headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())["embeddings"][0]
