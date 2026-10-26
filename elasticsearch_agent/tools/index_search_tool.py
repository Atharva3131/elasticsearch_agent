import json

import tiktoken
from langchain.tools import StructuredTool
from pydantic.v1 import BaseModel, Field  # <-- Uses v1 namespace

from elasticsearch_agent.config import cfg
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


def elastic_search(
        index_name: str,
        query: str,
        from_: int = cfg.elastic_index_data_from,
        size: int = cfg.elastic_index_data_size,
):
    """Executes a specific query on an ElasticSearch index and returns all hits or aggregation results"""
    size = min(cfg.elastic_index_data_max_size, size)
    encoding = tiktoken.encoding_for_model(cfg.model)
    try:
        full_dict: dict = json.loads(query)
        query_dict = full_dict["query"] if "query" in full_dict else None
        aggs_dict = full_dict["aggs"] if "aggs" in full_dict else None
        sort_dict = full_dict["sort"]  if "sort" in full_dict else None
        source_dict = full_dict["_source"] if "_source" in full_dict else None

        if query_dict is None and aggs_dict is None and sort_dict is None:
            # Assume that there is a query but that the query part was ommitted.
            query_dict = full_dict
        logger.info(query)

        retries = 0
        while retries < cfg.max_search_retries:
            res = cfg.es.search(
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
                final_res = str(res['aggregations'])
            else:
                final_res = str(res['hits'])
            tokens = encoding.encode(final_res)
            retries += 1
            if len(tokens) > cfg.token_limit:
                size -= 1
            else:
                return final_res

    except Exception as e:
        logger.exception(f"Could not execute query {query}")
        msg = str(e)
        return msg


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
