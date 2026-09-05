# Segment revenue behavior

Questions such as `2025년 3분기 사업부문별 매출` are classified as the
`revenue` metric with a segment aggregation cue. `사업부문` is not treated as
an independent `business_overview` metric when a revenue cue is present, so
the query remains a single periodic revenue lookup.

Structured DART tables can use hierarchical row headers such as
`차량부문 | 매출액 | 금액`. The parser preserves the leading labels together,
retains amount and ratio subheaders, and extracts the metric from the row
header when it is present in a separate cell. Ratio cells are excluded from
segment amount answers.

The answer writer lists each requested segment. Stage4 also checks that every
extracted segment amount appears in the final answer and fails closed when one
is missing.
