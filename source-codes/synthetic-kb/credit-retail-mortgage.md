# Retail Mortgage Credit Risk Rules (synthetic — test only)

## Delinquency chronology

A mortgage account cannot move from 30 days past due directly to current
status without a payment, restructuring, cure, or data-correction event.

## Origination chronology

An origination date must not occur after the first scheduled payment date.

## Outcome leakage

A default indicator must not be used as an origination predictor unless its
observation timestamp predates the decision point.
