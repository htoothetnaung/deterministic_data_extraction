"""Sentence splitter service.

PLACEHOLDER. Implement sentence segmentation here.

Responsibilities:
  * Split cleaned text into sentences for downstream embedding / chunking.
  * Preserve sentence bounding boxes / page references when available.

Suggested libraries:
  * ``nltk`` (sent_tokenize)
  * ``spacy`` (sentence boundary detection)
  * A rule-based splitter for domain-specific documents.

TODO: implement real splitting.
"""
from __future__ import annotations


def split_sentences(text: str) -> list[str]:
    """Split a block of text into sentences."""
    # TODO: implement real sentence splitting.
    if not text:
        return []
    return [text]
