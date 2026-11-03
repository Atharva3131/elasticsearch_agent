import json
import threading

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from elasticsearch_agent.config import cfg, validate_index_name
from elasticsearch_agent.log_init import logger

# ---------------------------------------------------------------------------
# Thread-local user context — set by the workflow before tool execution.
# The LLM never sees this; it's injected transparently per-request.
# ---------------------------------------------------------------------------
_user_context: threading.local = threading.local()


def set_user_context(claims: dict | None) -> None:
    """Store JWT claims for the current thread (called by workflow before graph.invoke)."""
    _user_context.claims = claims or {}


def _get_seller_filter() -> dict | None:
    """Return an ES filter clause for ROLE_USER, or None for ROLE_ADMIN / unauthenticated."""
    from elasticsearch_agent.auth import build_seller_filter
    claims = getattr(_user_context, "claims", {})
    return build_seller_filter(claims)


def _inject_seller_filter(query_dict: dict, seller_filter: dict) -> dict:
    """Wrap the LLM's query in a bool.filter that AND-s in the seller constraint."""
    # If already a bool query, append to its filter list.
    if "bool" in query_dict:
        existing_filter = query_dict["bool"].get("filter", [])
        if isinstance(existing_filter, dict):
            existing_filter = [existing_filter]
        query_dict["bool"]["filter"] = existing_filter + [seller_filter]
        return query_dict
    # Otherwise wrap the whole query.
    return {
        "bool": {
            "must": [query_dict],
            "filter": [seller_filter],
        }
    }


class SearchToolInput(BaseModel):
    """Input for the index show data tool."""

    index_name: str = Field(
        ..., description="The name of the index for which the data is to be retrieved"
    )
    query: str = Field(
        ...,
        description="The ElasticSearch JSON query used to filter all hits. Should use the _source field if possible to specify required fields.",
    )
    from_: int = Field(..., description="The record index from which the query will start")
    size: int = Field(..., description="How many records will be retrieved from the ElasticSearch query")
    reason: str = Field(
        default="",
        description="Explain in one sentence why you are calling this tool and what you expect to get from it.",
    )


def elastic_search(
    index_name: str,
    query: str,
    from_: int = cfg.elastic_index_data_from,
    size: int = cfg.elastic_index_data_size,
    reason: str = "",
):
    """Executes a specific query on an ElasticSearch index and returns all hits or aggregation results"""
    index_name = validate_index_name(index_name)
    size = min(cfg.elastic_index_data_max_size, size)
    try:
        # Defend against the LLM passing non-JSON or empty strings.
        stripped = (query or "").strip()
        if not stripped:
            full_dict: dict = {}
        else:
            try:
                full_dict = json.loads(stripped)
            except json.JSONDecodeError:
                logger.warning(f"Query is not valid JSON, falling back to match_all: {query!r}")
                full_dict = {}

        query_dict = full_dict.get("query")
        aggs_dict = full_dict.get("aggs")
        sort_dict = full_dict.get("sort")
        source_dict = full_dict.get("_source")

        if query_dict is None and aggs_dict is None and sort_dict is None:
            query_dict = full_dict if full_dict else None

        # Elasticsearch rejects an empty or missing query clause — replace with match_all.
        if not query_dict:
            query_dict = {"match_all": {}}

        # --- Data governance: inject seller filter for ROLE_USER ---
        seller_filter = _get_seller_filter()
        if seller_filter:
            query_dict = _inject_seller_filter(query_dict, seller_filter)

        logger.info(json.dumps({"query": query_dict, "aggs": aggs_dict}))

        retries = 0
        while retries < cfg.max_search_retries:
            res = cfg.elasticsearch().search(
                index=index_name,
                from_=from_,
                size=size,
                query=query_dict,
                aggs=aggs_dict,
                sort=sort_dict,
                source=source_dict,
            )
            if aggs_dict is not None and size == 0:
                final_res = json.dumps(res["aggregations"])
            else:
                final_res = str(res["hits"])
            tokens = len(final_res.split())
            retries += 1
            if tokens > cfg.token_limit and size > 0:
                size -= 1
            else:
                return final_res

        raise RuntimeError("Elasticsearch search exceeded the configured retry limit")

    except Exception:
        logger.exception(f"Could not execute query {query}")
        raise


def create_search_tool():
    return StructuredTool.from_function(
        elastic_search,
        description="this tool can be used to either search or get total count of records from a given index",
        name="elastic_index_search_tool",
        args_schema=SearchToolInput,
    )
