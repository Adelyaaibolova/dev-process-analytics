# DAX measures

Create these on the `Events` table (Table tools > New measure). They use the
same rules as the Qlik app and the Python check:

- **Window:** only events inside `window.csv` (the `Events` query already filters).
- **Week:** starts on Monday (`Calendar[Week]`).
- **Bots:** pull requests and issues opened by bot accounts are excluded.
- **CI outcome:** `pass` / `fail`; cancelled, skipped and unfinished runs are ignored.

## Measures

```dax
PRs opened =
CALCULATE ( COUNTROWS ( Events ), Events[EventType] = "PR opened", Events[IsBot] = 0 )

PRs merged =
CALCULATE ( COUNTROWS ( Events ), Events[EventType] = "PR merged", Events[IsBot] = 0 )

PRs closed =
CALCULATE ( COUNTROWS ( Events ), Events[EventType] = "PR closed", Events[IsBot] = 0 )

Merge rate =
DIVIDE (
    CALCULATE ( COUNTROWS ( Events ), Events[EventType] = "PR closed", Events[IsBot] = 0, Events[Merged] = 1 ),
    [PRs closed]
)

Median PR lead time (h) =
CALCULATE ( MEDIAN ( Events[LeadTimeHours] ), Events[EventType] = "PR merged", Events[IsBot] = 0 )

Median time to first review (h) =
CALCULATE ( MEDIAN ( Events[HoursToFirstReview] ), Events[EventType] = "PR opened", Events[IsBot] = 0 )

Reviewed within 24 h =
DIVIDE (
    CALCULATE ( COUNTROWS ( Events ), Events[EventType] = "PR opened", Events[IsBot] = 0, Events[ReviewedWithin24h] = 1 ),
    [PRs opened]
)

Issues opened =
CALCULATE ( COUNTROWS ( Events ), Events[EventType] = "Issue opened", Events[IsBot] = 0 )

Issues closed =
CALCULATE ( COUNTROWS ( Events ), Events[EventType] = "Issue closed", Events[IsBot] = 0 )

Median days to close an issue =
CALCULATE ( MEDIAN ( Events[DaysToClose] ), Events[EventType] = "Issue closed", Events[IsBot] = 0 )

CI runs completed =
CALCULATE ( COUNTROWS ( Events ), Events[EventType] = "CI run", Events[CIOutcome] IN { "pass", "fail" } )

CI failure rate =
DIVIDE (
    CALCULATE ( COUNTROWS ( Events ), Events[EventType] = "CI run", Events[CIOutcome] = "fail" ),
    [CI runs completed]
)

Median CI duration (min) =
CALCULATE ( MEDIAN ( Events[CIDurationMin] ), Events[EventType] = "CI run", Events[CIOutcome] IN { "pass", "fail" } )
```

Formats: counts as whole numbers; *Merge rate*, *Reviewed within 24 h* and
*CI failure rate* as percentage; medians with one decimal.

`MEDIAN` ignores blank values, so pull requests that were never reviewed do not
distort the median time to first review; they still count in the denominator of
*Reviewed within 24 h*.
