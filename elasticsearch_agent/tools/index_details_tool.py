from typing import Type, Optional

from pydantic import BaseModel, Field

from langchain_core.callbacks import (
    AsyncCallbackManagerForToolRun,
    CallbackManagerForToolRun,
)

from elasticsearch_agent.config import cfg, validate_index_name
from langchain_core.tools import BaseTool
from elasticsearch_agent.log_init import logger



class IndexDetailsInput(BaseModel):
    """Input for the list index details tool."""

    index_name: str = Field(..., description="The name of the index for which the details are to be retrieved")
    reason: str = Field(
        default="",
        description="Explain in one sentence why you are calling this tool and what you expect to get from it.",
    )


class IndexDetailsTool(BaseTool):
    """Tool for getting information about a single ElasticSearch index"""

    name: str = "elastic_index_show_details"
    description: str = "Input is an index name, output is a JSON based string with the aliases, mappings containing the field names and settings of an index"

    def _run(
        self,
        index_name: str,
        run_manager: Optional[CallbackManagerForToolRun] = None,
    ) -> str:
        """Get all indices in the elastic search server ususally separated by a line break"""
        try:
            index_name = validate_index_name(index_name)
            client = cfg.elasticsearch()
            alias = client.indices.get_alias(index=index_name)
            field_mappings = client.indices.get_field_mapping(
                index=index_name, fields="*"
            )
            field_settings = client.indices.get_settings(index=index_name)
            return str(
                {
                    "alias": alias[index_name],
                    "field_mappings": field_mappings[index_name],
                    "settings": field_settings[index_name],
                }
            )
        except Exception:
            logger.exception(f"Could not fetch index information for {index_name}")
            raise

    async def _arun(
        self,
        index_name: str = "",
        run_manager: Optional[AsyncCallbackManagerForToolRun] = None,
    ) -> str:
        raise NotImplementedError("IndexDetailsTool does not support async")

    args_schema: Optional[Type[BaseModel]] = IndexDetailsInput

if __name__ == "__main__":
    index_name = "ma-order-1"
    tool = IndexDetailsTool()
    result = tool(index_name)
    logger.info("result: %s", result)
