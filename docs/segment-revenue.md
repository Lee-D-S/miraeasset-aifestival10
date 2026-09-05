# Segment revenue behavior

Questions such as `2025년 3분기 사업부문별 매출` are classified as the
`revenue` metric with a segment aggregation cue. `사업부문` is not treated as
an independent `business_overview` metric when a revenue cue is present, so
the query remains a single periodic revenue lookup.

Structured DART tables can use hierarchical row headers such as
`차량부문 | 매출액 | 금액`. The parser preserves the leading labels together,
retains amount and ratio subheaders, and extracts the metric from the row
header when it is present in a separate cell. It also reconstructs period
groups when the amount/ratio header omits a leading alignment cell or the
converted row has trailing empty cells. This keeps prior-year values and ratio
cells out of the requested period's segment amount answer.

The answer writer lists each requested segment. Stage4 also checks that every
extracted segment amount appears in the final answer and fails closed when one
is missing.
