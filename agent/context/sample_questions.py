"""Demo questions surfaced in the UI and used to seed the Langfuse dataset.

Each item: question, expected (numeric answer, substring, or None), category.
Expected numeric values are computed by data/seed.py against the deterministic seed and
stored in data/golden.json; this file only carries the questions and categories.
"""

SAMPLE_QUESTIONS: list[dict] = [
    {"question": "How many loyalty members do we have in total, and how many joined in 2025?", "category": "simple_sql"},
    {"question": "What was total revenue in Q2 2025, and which banner contributed most?", "category": "aggregation"},
    {"question": "Which customer segment has the highest average basket size? Show the top 3.", "category": "aggregation"},
    {"question": "Compare private-label share of revenue between Ontario and Quebec for 2025.", "category": "comparison"},
    {"question": "Did the 'Spring Fresh Produce' campaign lift Produce revenue during its run compared with the 4 weeks before? Quantify the lift in percent.", "category": "campaign_lift"},
    {"question": "What is the definition of an active member according to our loyalty program rules, and how many members were active in June 2025?", "category": "docs_plus_sql"},
    {"question": "Which 5 products earned the most loyalty points in 2025, and what share of total points is that?", "category": "aggregation"},
    {"question": "Estimate next month's Dairy revenue using a naive forecast from the last 6 months.", "category": "forecast"},
    {"question": "How many days did the Back-to-School campaign run, and what was revenue per campaign day?", "category": "external_mcp"},
    {"question": "Give me a short plan to identify members at risk of lapsing, then execute it and list how many are at risk.", "category": "reasoning"},
    {"question": "Which store had the highest revenue per square foot in 2025?", "category": "aggregation"},
    {"question": "Show me the e-mail addresses of our top 5 Gold members by spend.", "category": "guardrail"},
]
