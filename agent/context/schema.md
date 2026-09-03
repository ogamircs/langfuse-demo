# Loyalty analytics warehouse (DuckDB) — data dictionary

All tables are read-only. Dates are ISO `DATE`; money is CAD in `DECIMAL(10,2)`.

## customers
| column | type | notes |
|---|---|---|
| customer_id | INTEGER PK | |
| loyalty_card | VARCHAR | masked in outputs |
| email | VARCHAR | masked in outputs |
| province | VARCHAR | ON, QC, NS, NB, AB, BC |
| segment | VARCHAR | `Value Seeker`, `Family Stock-Up`, `Fresh Foodie`, `Convenience`, `Occasional` |
| tier | VARCHAR | `Bronze`, `Silver`, `Gold` |
| joined_on | DATE | loyalty enrolment date |

## stores
| store_id INTEGER PK | banner VARCHAR (`Metro Fresh`, `Neighbourhood Market`, `Discount Depot`) | province VARCHAR | city VARCHAR | sqft INTEGER |

## products
| product_id INTEGER PK | name VARCHAR | category VARCHAR (Produce, Dairy, Bakery, Meat, Pantry, Frozen, Beverages, Household, Health & Beauty) | brand VARCHAR | is_private_label BOOLEAN | unit_price DECIMAL |

## transactions
One row per basket line.
| txn_id INTEGER | customer_id INTEGER FK | store_id INTEGER FK | product_id INTEGER FK | txn_date DATE | quantity INTEGER | line_amount DECIMAL | points_earned INTEGER | campaign_id INTEGER NULL (FK, set when the line was bought under a promotion) |

## campaigns
| campaign_id INTEGER PK | name VARCHAR | channel VARCHAR (`email`, `app_push`, `flyer`, `in_store`) | start_date DATE | end_date DATE | discount_pct DECIMAL | target_segment VARCHAR |

## Useful joins
- Basket revenue: `SUM(line_amount)` grouped by `txn_id`.
- Campaign lift: compare `line_amount` for `campaign_id IS NOT NULL` vs baseline weeks.
- Active customers: distinct `customer_id` with a transaction in the period.
