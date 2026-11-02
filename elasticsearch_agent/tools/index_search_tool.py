import json

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from elasticsearch_agent.config import cfg, validate_index_name
from elasticsearch_agent.log_init import logger


class SearchToolInput(BaseModel):
    """Input for the index show data tool."""

    index_name: str = Field(
        ..., description="The name of the index for which the data is to be retrieved"
    )
    query: str = Field(...,
                       description="The ElasticSearch JSON query used to filter all hits. Should use the _source field if possible to specify required fields.")
    from_: int = Field(
        ..., description="The record index from which the query will start"
    )
    size: int = Field(
        ...,
        description="How many records will be retrieved from the ElasticSearch query",
    )
    reason: str = Field(
        default="",
        description="Explain in one sentence why you are calling this tool and what you expect to get from it.",
    )


def elastic_search(
        index_name: str,
        query: str,
        from_: int = cfg.elastic_index_data_from,
        size: int = cfg.elastic_index_data_size,
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
            # Assume that there is a query but that the query part was omitted.
            query_dict = full_dict if full_dict else None

        # Elasticsearch rejects an empty or missing query clause — replace with match_all.
        if not query_dict:
            query_dict = {"match_all": {}}
        logger.info(query)

        retries = 0
        while retries < cfg.max_search_retries:
            res = cfg.elasticsearch().search(
                index=index_name,
                from_=from_,
                size=size,
                query=query_dict,
                aggs=aggs_dict,
                sort=sort_dict,
                source=source_dict
            )
            if aggs_dict is not None and size == 0:
                # When a result has aggregations, just return that and ignore the rest
                # Return valid JSON; Python's str(dict) uses single quotes and
                # cannot be decoded by the chart workflow with json.loads().
                final_res = json.dumps(res['aggregations'])
            else:
                final_res = str(res['hits'])
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
    return StructuredTool.from_function(elastic_search, description="this tool can be used to either search or get total count of records from a given index" , name="elastic_index_search_tool", args_schema=SearchToolInput)


if __name__ == "__main__":
    index_name = "order-read"
    tool = create_search_tool()
    res = tool.run(
        {
            "index_name": index_name,
            "query": """
                {
                  "size": 0, 
                  "query": {
                    "range": {
                      "orderDate": {
                        "gte": "2024-06-01",
                        "lte": "2024-12-01"
                      }
                    }
                  },
                  "aggs": {
                    "orderTotal": {
                      "stats": {
                        "field": "orderTotal"
                      }
                    }
                  }
                }
            """,
            "from_": 0,
            "size": 10,
        }
    )
