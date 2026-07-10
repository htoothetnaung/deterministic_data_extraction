"""Pydantic schemas for runtime extraction configuration settings."""
from __future__ import annotations

from pydantic import BaseModel, Field


class ModelSettings(BaseModel):
    """Configuration parameters for LLM models."""
    model_tier: str = Field(default="cost_effective", description="Determines target LLM class (cost_effective, speed, balanced, quality).")
    temperature: float = Field(default=0.0, ge=0.0, le=2.0, description="Temperature scaling parameter.")
    max_tokens: int = Field(default=2048, ge=1, description="Ceiling response tokens allowed.")


class RetrievalSettings(BaseModel):
    """Configuration parameters for text chunk retrieval and rank fusion."""
    scalar_chunk_limit: int = Field(default=3, description="Evidence chunks supplied for scalar or categorical fields.")
    narrative_chunk_limit: int = Field(default=8, description="Evidence chunks supplied for narrative or summary fields.")
    max_chunk_limit: int = Field(default=10, description="Upper bound for final fused chunks returned to extraction prompts.")
    retry_chunk_expansion: int = Field(default=2, description="Additional chunks requested per empty-result retry attempt.")
    dense_candidate_limit: int = Field(default=10, description="Nearest-vector candidates considered before rank fusion.")
    sparse_candidate_limit: int = Field(default=10, description="BM25 candidates considered before rank fusion.")
    rank_fusion_constant: int = Field(default=60, description="Reciprocal-rank-fusion smoothing constant.")


class QuerySettings(BaseModel):
    """Configuration parameters for generated retrieval queries and retry prompts."""
    empty_results_max_retry: int = Field(default=3, description="Controls how many times null or empty extracted fields are retried before returning the final payload.")
    query_min_words: int = Field(default=3, description="Minimum target words for generated retrieval queries.")
    query_max_words: int = Field(default=5, description="Maximum target words for generated retrieval queries.")
    prior_result_preview: bool = Field(default=True, description="Whether to include characters of previous empty output in retry prompts.")


class RuntimeSettings(BaseModel):
    """Consolidated configuration settings block for extraction and retrieval pipelines."""
    model: ModelSettings = Field(default_factory=ModelSettings)
    retrieval: RetrievalSettings = Field(default_factory=RetrievalSettings)
    queries: QuerySettings = Field(default_factory=QuerySettings)
