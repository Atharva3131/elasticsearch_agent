from elasticsearch_agent.agent.agent_factory import agent_factory
from elasticsearch_agent.log_init import logger

agent_executor = agent_factory()


def test_list_indices():
    execution_template("Can you please list all indices?")


def test_list_count():
    execution_template(
        """Give me the total count of records in ma-order-1 index where the order belongs to Rakuten - Japan marketplace
        """
    )


def test_list_counts():
    execution_template(
        "Can you please list all indices with its corresponding record count?"
    )


def test_fields():
    execution_template("Which are the fields of the ma-order-1 index?")


def highest_order_total_order():
    execution_template(
        "Which order has the highest order total for year 2024 according to ma-order-1 index?"
    )


def highest_order_total_order_for_rakuten():
    execution_template(
        # """Give me the order with highest order total order among all of the `Rakuten - Japan` marketplace orders according to ma-order-1 index"""
        """
        Give me the order which satisfies the following
        Marketplace: Rakuten - Japan
        Order Total: Highest among all
        """
    )


def rakuten_vs_amazon_fr_revenue():
    execution_template(
        """
        Can you compare the total revenue from Rakuten - Japan marketplace vs Amazon - France marketplace? According to ma-order-1 index
        """
    )


def rakuten_vs_amazon_fr_vs_ebay_revenue():
    execution_template(
        """
        Can you compare the revenue among Rakuten - Japan and Amazon - France and eBay - United States marketplace? According to ma-order-1 index
        """
    )


def top_five_selling_sku():
    execution_template(
        """Can you me top five selling sku for year 2024? According to ma-order-1"""
    )


def particular_sku_revenue_yoy_comparison():
    execution_template(
        """
        Can you compare the revenue of the sku 98765434-L for the year 2024 and 2023? According to the ma-order-1 index
        """
    )


def skus_greater_than_100000_revenue():
    execution_template(
        """
        Can you list top 10 skus where the skus' revenue was greater than $100000 in the year 2024? According to the ma-order-1 index
        The revenue is calculate as sum of all the order's orderTotal.
        Use aggs to aggregate the result based on the query, and you don't need any source for this kind of query
        """
    )



import sys

report_file = None

if len(sys.argv) > 1:
    report_file = sys.argv[1] if sys.argv[1] != '.' else None


def execution_template(question: str) -> str:
    res = agent_executor.run(f"""
Make sure that you query first the indices in the ElasticSearch database.
Make sure that after querying the indices you query the field names.

If user asks for count the record use `_count` query 
If user asks for records then use the `_search` query
                             
Then answer this question:
{question}
""")
    logger.info(question)
    logger.info(res)
    if report_file is not None:
        with open(report_file, "a") as f:
            f.write(
                f"""
                {question}
                {res}
                """
            )
    return res


if __name__ == "__main__":
    # To test and pipe the result into a file, use:
    # python.exe .\elasticsearch_agent\agent\test\agent_factory_test.py test_output.txt
    test_list_indices()
    # test_list_count()
    # test_list_counts()
    # test_fields()
    # highest_order_total_order()
    # highest_order_total_order_for_rakuten()
    # rakuten_vs_amazon_fr_revenue()
    # rakuten_vs_amazon_fr_vs_ebay_revenue()
    # top_five_selling_sku()
    # particular_sku_revenue_yoy_comparison()
    # skus_greater_than_100000_revenue()
