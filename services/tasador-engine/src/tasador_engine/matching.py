"""Compare the identified item with items the caller already owns."""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from .schemas import Identification, KnownItem, SimilarKnownItem

MIN_SIMILARITY = 0.6
MAX_RESULTS = 5


def _norm(text: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def _label(brand: str | None, model: str | None, name: str | None) -> str:
    return _norm(f"{brand or ''}{model or ''}") or _norm(name)


def similar_items(best: Identification, known: list[KnownItem]) -> list[SimilarKnownItem]:
    target_model = _norm(best.model)
    target = _label(best.brand, best.model, best.name)
    if not target:
        return []
    scored: list[SimilarKnownItem] = []
    for item in known:
        if target_model and _norm(item.model) == target_model:
            score = 1.0
        else:
            other = _label(item.brand, item.model, item.name)
            if not other:
                continue
            score = SequenceMatcher(None, target, other).ratio()
        if score >= MIN_SIMILARITY:
            scored.append(SimilarKnownItem(id=item.id, similarity=round(score, 3)))
    scored.sort(key=lambda s: s.similarity, reverse=True)
    return scored[:MAX_RESULTS]
