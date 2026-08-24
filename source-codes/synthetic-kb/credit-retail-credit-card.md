# Retail Credit Card Rules (synthetic — test only)

## Utilization ceiling

Reported utilization above 100% of the credit limit requires a limit-change
or fee-posting explanation; otherwise treat it as a data-quality defect, not
a genuine risk signal.

## Minimum payment floor

The minimum payment due must never exceed the statement balance.
