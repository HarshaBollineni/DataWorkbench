# Operational Data Lineage Rules (synthetic — test only)

## Upstream feed

The retail_accounts table is fed nightly from the core banking extract; a
gap of more than one business day indicates a feed interruption, not a
genuine change in account activity.

## Join key

customers.customer_id is the sole valid join key into retail_accounts; any
other column that appears to correlate is coincidental, not a relationship.
