"""
Embeds chunks with OpenAI and provides search. The index is a local .npz
file -- no vector database service to pay for or maintain. At pilot scale
(a few thousand chunks) brute-force cosine over numpy is instant.

  build_index()  -- run on ingest; embeds every chunk
  search(query)  -- hybrid retrieval at question time

Search combines three signals, each added because of a real failure seen in
testing:
  1. cosine similarity on embeddings -- the semantic backbone
  2. keyword overlap -- rescues answers buried inside long list pages, where
     one org's mention gets averaged away by its 29 neighbours
  3. per-page diversity cap -- stops five near-identical chunks of one page
     from filling every slot
Titles and source labels are embedded along with the text, so a page whose
body never repeats its own topic ("Contact") still matches.
"""

import json
import os

import numpy as np
from openai import OpenAI

from corrections import CORRECTION_LABEL
from settings import cfg

CHUNKS_FILE = "chunks.jsonl"
INDEX_FILE = "vector_index.npz"
BATCH_SIZE = 100

_STOPWORDS = {
    "a", "an", "the", "i", "is", "are", "was", "were", "be", "at", "in", "on",
    "of", "for", "to", "and", "or", "any", "such", "there", "it", "this",
    "that", "can", "do", "does", "how", "what", "where", "when", "who", "why",
    "want", "with", "my", "me", "you", "your", "we", "isu", "available",
    "about", "some", "which", "get", "have", "has", "am", "looking",
}


def get_client() -> OpenAI:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY environment variable is not set.")
    return OpenAI(api_key=api_key)


def _embed_input(chunk: dict) -> str:
    """What actually gets embedded: source + title + text, so a chunk carries
    the context of where it came from, not just its raw words."""
    return f"{chunk.get('source', '')} | {chunk['title']}\n{chunk['text']}"


def build_index():
    client = get_client()
    model = cfg("models", "embedding", "text-embedding-3-small")

    with open(CHUNKS_FILE, "r", encoding="utf-8") as f:
        chunks = [json.loads(line) for line in f]
    if not chunks:
        raise RuntimeError(f"No chunks in {CHUNKS_FILE}. Run the scraper and chunker first.")

    vectors = []
    for i in range(0, len(chunks), BATCH_SIZE):
        batch = chunks[i:i + BATCH_SIZE]
        response = client.embeddings.create(
            model=model, input=[_embed_input(c) for c in batch])
        vectors.extend(item.embedding for item in response.data)
        print(f"embedded {min(i + BATCH_SIZE, len(chunks))}/{len(chunks)} chunks")

    np.savez_compressed(
        INDEX_FILE,
        vectors=np.array(vectors, dtype=np.float32),
        urls=np.array([c["url"] for c in chunks], dtype=object),
        titles=np.array([c["title"] for c in chunks], dtype=object),
        texts=np.array([c["text"] for c in chunks], dtype=object),
        sources=np.array([c.get("source", "") for c in chunks], dtype=object),
        chunk_indices=np.array([c.get("chunk_index", 0) for c in chunks], dtype=np.int32),
    )
    print(f"\nSaved index with {len(chunks)} chunks -> {INDEX_FILE}")


def _cosine_similarity(query_vec: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    q = query_vec / (np.linalg.norm(query_vec) + 1e-8)
    m = matrix / (np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-8)
    return m @ q


def _keyword_overlap(query: str, haystacks: list[str]) -> np.ndarray:
    """Fraction of meaningful query words literally present in each chunk."""
    words = {w.strip(".,?!:;()\"'").lower() for w in query.split()}
    words = {w for w in words if len(w) > 2 and w not in _STOPWORDS}
    if not words:
        return np.zeros(len(haystacks), dtype=np.float32)
    scores = np.zeros(len(haystacks), dtype=np.float32)
    for i, text in enumerate(haystacks):
        scores[i] = sum(1 for w in words if w in text) / len(words)
    return scores


_index_cache: dict | None = None


def _load_index() -> dict:
    """Cached in memory -- the server would otherwise re-read the whole index
    from disk on every single question."""
    global _index_cache
    if _index_cache is None:
        if not os.path.exists(INDEX_FILE):
            raise RuntimeError(f"{INDEX_FILE} not found. Run ingest.py first.")
        data = np.load(INDEX_FILE, allow_pickle=True)
        _index_cache = {
            "vectors": data["vectors"],
            "urls": data["urls"],
            "titles": data["titles"],
            "texts": data["texts"],
            "sources": data["sources"] if "sources" in data else
                       np.array([""] * len(data["urls"]), dtype=object),
            "chunk_indices": data["chunk_indices"] if "chunk_indices" in data else
                             np.zeros(len(data["urls"]), dtype=np.int32),
            "is_correction": np.array(
                [1.0 if str(s) == CORRECTION_LABEL else 0.0
                 for s in (data["sources"] if "sources" in data
                           else [""] * len(data["urls"]))],
                dtype=np.float32),
            "haystacks": [
                (str(data["titles"][i]) + "\n" + str(data["texts"][i])).lower()
                for i in range(len(data["urls"]))
            ],
        }
    return _index_cache


def reset_index_cache():
    """Called after a rebuild so the running server picks up new content
    without a restart."""
    global _index_cache
    _index_cache = None


def _result(idx: int, score: float, data: dict) -> dict:
    return {
        "url": str(data["urls"][idx]),
        "title": str(data["titles"][idx]),
        "source": str(data["sources"][idx]),
        "text": str(data["texts"][idx]),
        "chunk_index": int(data["chunk_indices"][idx]),
        "score": float(score),
    }


def search(query: str, top_k: int | None = None,
           max_per_url: int | None = None) -> list[dict]:
    """Hybrid search: embeddings + keyword overlap, with a per-page cap and
    optional neighbour expansion (pulling the chunks adjacent to a strong hit,
    which matters when an answer straddles a chunk boundary)."""
    top_k = top_k or int(cfg("retrieval", "candidates", 20))
    max_per_url = max_per_url or int(cfg("retrieval", "max_per_url", 3))
    keyword_weight = float(cfg("retrieval", "keyword_weight", 0.12))

    data = _load_index()
    client = get_client()
    model = cfg("models", "embedding", "text-embedding-3-small")

    query_vec = np.array(
        client.embeddings.create(model=model, input=[query]).data[0].embedding,
        dtype=np.float32)

    scores = _cosine_similarity(query_vec, data["vectors"])
    scores = scores + keyword_weight * _keyword_overlap(query, data["haystacks"])

    # Admin-verified corrections get a boost so they outrank the scraped page
    # that produced the wrong answer in the first place. Without this, a
    # correction is just one more voice in the noise. Tune in config.yaml;
    # set to 0 to switch the behaviour off entirely.
    boost = float(cfg("retrieval", "correction_boost", 0.10))
    if boost:
        scores = scores + boost * data["is_correction"]

    results = []
    per_url: dict[str, int] = {}
    for idx in np.argsort(scores)[::-1]:
        url = str(data["urls"][idx])
        if per_url.get(url, 0) >= max_per_url:
            continue
        per_url[url] = per_url.get(url, 0) + 1
        results.append(_result(int(idx), scores[idx], data))
        if len(results) >= top_k:
            break

    if cfg("retrieval", "neighbor_expansion", True) and results:
        results = _expand_neighbors(results, data, scores)
    return results


def _expand_neighbors(results: list[dict], data: dict, scores: np.ndarray) -> list[dict]:
    """For the single best hit, also include the chunks immediately before and
    after it on the same page. Costs nothing (no API call) and repairs answers
    that were split across a chunk boundary."""
    best = results[0]
    have = {(r["url"], r["chunk_index"]) for r in results}
    wanted = {(best["url"], best["chunk_index"] - 1),
              (best["url"], best["chunk_index"] + 1)}
    for idx in range(len(data["urls"])):
        key = (str(data["urls"][idx]), int(data["chunk_indices"][idx]))
        if key in wanted and key not in have:
            results.append(_result(idx, scores[idx], data))
            have.add(key)
    return results


if __name__ == "__main__":
    build_index()
