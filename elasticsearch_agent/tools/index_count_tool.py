import json

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from elasticsearch_agent.config import cfg, validate_index_name
from elasticsearch_agent.log_init import logger


class CountToolInput(BaseModel):
    """Input for the index show data tool."""

    index_name: str = Field(
        ..., description="The name of the index for which the count is to be retrieved"
    )
    query: str = Field(...,
                       description="The ElasticSearch JSON query used to filter through the records and fetch the index count")


def elastic_search(
        index_name: str,
        query: str
):
    """Executes a specific query on an ElasticSearch index and returns count of records"""
    index_name = validate_index_name(index_name)
    try:
        full_dict: dict = json.loads(query)
        query_dict = None
        if "query" in full_dict:
            query_dict = full_dict["query"]
        if query_dict is not None:
            logger.info(query)

            res = cfg.elasticsearch().count(
                index=index_name,
                query=query_dict
            )
        else:
            res = cfg.elasticsearch().count(
                index=index_name
            )

        final_res = str(res['count'])
        return final_res

    except Exception:
        logger.exception(f"Could not execute query {query}")
        raise


def create_count_tool():
    return StructuredTool.from_function(
        elastic_search,
        description="this tool can be used to count of records from a given index" ,
        name="elastic_index_count_tool",
        args_schema=CountToolInput
    )


if __name__ == "__main__":
    index_name = "order-read"
    tool = create_count_tool()
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
