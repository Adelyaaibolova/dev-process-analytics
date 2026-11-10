// =============================================================================
// dev-process-analytics: Power Query (M) for Power BI Desktop
//
// Create one blank query per section below (Home > Get data > Blank query >
// Advanced Editor), paste the code and give the query EXACTLY the name shown.
// First create a text parameter named DataFolder that points to the folder
// with the CSV files, ending with a backslash, e.g. C:\projects\dpa\data\
// =============================================================================


// ---- Query: LoadCsv (function) ----------------------------------------------
// Reads one CSV from DataFolder, promotes headers and turns empty cells into null.
(fileName as text) as table =>
let
    Source   = Csv.Document(
                   File.Contents(DataFolder & fileName),
                   [Delimiter = ",", Encoding = 65001, QuoteStyle = QuoteStyle.Csv]),
    Promoted = Table.PromoteHeaders(Source, [PromoteAllScalars = true]),
    Nulls    = Table.ReplaceValue(Promoted, "", null, Replacer.ReplaceValue,
                                  Table.ColumnNames(Promoted))
in
    Nulls


// ---- Query: ParseTs (function) ------------------------------------------------
// Timestamps are written as "YYYY-MM-DD hh:mm:ss" in UTC.
(value as nullable text) as nullable datetime =>
    if value = null then null
    else DateTime.FromText(value, [Format = "yyyy-MM-dd HH:mm:ss", Culture = "en-US"])


// ---- Query: Window ---------------------------------------------------------------
let
    Raw   = LoadCsv("window.csv"),
    ToDay = (t as text) as date => Date.FromText(t, [Format = "yyyy-MM-dd", Culture = "en-US"]),
    Typed = Table.TransformColumns(Raw, {
                {"window_start", ToDay, type date},
                {"window_end",   ToDay, type date}})
in
    Typed


// ---- Query: PullRequests ---------------------------------------------------------
let
    Raw   = LoadCsv("pull_requests.csv"),
    Typed = Table.TransformColumns(Raw, {
                {"pr_number",       Int64.From, Int64.Type},
                {"author_is_bot",   Int64.From, Int64.Type},
                {"draft",           Int64.From, Int64.Type},
                {"comments",        Int64.From, Int64.Type},
                {"created_at",      ParseTs, type datetime},
                {"closed_at",       ParseTs, type nullable datetime},
                {"merged_at",       ParseTs, type nullable datetime},
                {"first_review_at", ParseTs, type nullable datetime}}),
    Key   = Table.AddColumn(Typed, "ItemKey", each "PR-" & Text.From([pr_number]), type text),
    Lead  = Table.AddColumn(Key, "LeadTimeHours",
                each if [merged_at] = null then null
                     else Duration.TotalHours([merged_at] - [created_at]),
                type nullable number)
in
    Lead


// ---- Query: Issues ----------------------------------------------------------------
let
    Raw   = LoadCsv("issues.csv"),
    Typed = Table.TransformColumns(Raw, {
                {"issue_number",  Int64.From, Int64.Type},
                {"author_is_bot", Int64.From, Int64.Type},
                {"comments",      Int64.From, Int64.Type},
                {"created_at",    ParseTs, type datetime},
                {"closed_at",     ParseTs, type nullable datetime}}),
    Key   = Table.AddColumn(Typed, "ItemKey", each "IS-" & Text.From([issue_number]), type text)
in
    Key


// ---- Query: CIRuns ----------------------------------------------------------------
let
    Raw     = LoadCsv("ci_runs.csv"),
    Typed   = Table.TransformColumns(Raw, {
                  {"run_id",         Int64.From, Int64.Type},
                  {"run_attempt",    Int64.From, Int64.Type},
                  {"created_at",     ParseTs, type datetime},
                  {"run_started_at", ParseTs, type nullable datetime},
                  {"updated_at",     ParseTs, type datetime}}),
    Key     = Table.AddColumn(Typed, "ItemKey", each "CI-" & Text.From([run_id]), type text),
    Outcome = Table.AddColumn(Key, "CIOutcome",
                  each if [conclusion] = "success" then "pass"
                       else if List.Contains({"failure", "timed_out", "startup_failure"}, [conclusion]) then "fail"
                       else "other",
                  type text)
in
    Outcome


// ---- Query: Events ----------------------------------------------------------------
// One row per event inside the analysis window. Every KPI is a measure on this
// table, filtered by EventType, so all charts share one date axis.
let
    W       = Window{0},
    Start   = DateTime.From(W[window_start]),
    EndExcl = DateTime.From(Date.AddDays(W[window_end], 1)),
    Inside  = (t) => t <> null and t >= Start and t < EndExcl,
    Hours   = (later, earlier) => if later = null or earlier = null then null
                                  else Duration.TotalHours(later - earlier),

    Blank = [ItemKey = null, ItemType = null, Number = null, IsBot = null, Merged = null,
             LeadTimeHours = null, HoursToFirstReview = null, ReviewedWithin24h = null,
             DaysToClose = null, CIWorkflow = null, CIOutcome = null, CIDurationMin = null],
    Event = (fields as record, eventType as text, moment as datetime) as record =>
                Blank & fields & [EventType = eventType, EventTime = moment,
                                  EventDate = DateTime.Date(moment)],

    PRs = Table.ToRecords(PullRequests),
    PRBase = (p) => [ItemKey = p[ItemKey], ItemType = "PR", Number = p[pr_number],
                     IsBot = p[author_is_bot]],
    PREvents =
        List.Transform(List.Select(PRs, each Inside([created_at])), (p) =>
            Event(PRBase(p) & [
                HoursToFirstReview = Hours(p[first_review_at], p[created_at]),
                ReviewedWithin24h  = if p[first_review_at] <> null
                                        and Hours(p[first_review_at], p[created_at]) <= 24
                                     then 1 else 0],
                "PR opened", p[created_at]))
      & List.Transform(List.Select(PRs, each Inside([merged_at])), (p) =>
            Event(PRBase(p) & [LeadTimeHours = p[LeadTimeHours]], "PR merged", p[merged_at]))
      & List.Transform(List.Select(PRs, each Inside([closed_at])), (p) =>
            Event(PRBase(p) & [Merged = if p[merged_at] <> null then 1 else 0],
                  "PR closed", p[closed_at])),

    Iss = Table.ToRecords(Issues),
    IssueBase = (i) => [ItemKey = i[ItemKey], ItemType = "Issue", Number = i[issue_number],
                        IsBot = i[author_is_bot]],
    IssueEvents =
        List.Transform(List.Select(Iss, each Inside([created_at])), (i) =>
            Event(IssueBase(i), "Issue opened", i[created_at]))
      & List.Transform(List.Select(Iss, each Inside([closed_at])), (i) =>
            Event(IssueBase(i) & [DaysToClose = Duration.TotalDays(i[closed_at] - i[created_at])],
                  "Issue closed", i[closed_at])),

    Runs = Table.ToRecords(CIRuns),
    CIEvents =
        List.Transform(List.Select(Runs, each Inside([created_at])), (r) =>
            Event([ItemKey = r[ItemKey], ItemType = "CI run", Number = r[run_id], IsBot = 0,
                   CIWorkflow = r[workflow], CIOutcome = r[CIOutcome],
                   CIDurationMin = if r[run_started_at] = null then null
                                   else Duration.TotalMinutes(r[updated_at] - r[run_started_at])],
                  "CI run", r[created_at])),

    EventsType = type table [
        EventType = text, EventTime = datetime, EventDate = date,
        ItemKey = text, ItemType = text, Number = nullable Int64.Type, IsBot = nullable Int64.Type,
        Merged = nullable Int64.Type, LeadTimeHours = nullable number,
        HoursToFirstReview = nullable number, ReviewedWithin24h = nullable Int64.Type,
        DaysToClose = nullable number, CIWorkflow = nullable text, CIOutcome = nullable text,
        CIDurationMin = nullable number],
    Result = Table.FromRecords(PREvents & IssueEvents & CIEvents, EventsType)
in
    Result


// ---- Query: Calendar --------------------------------------------------------------
// One row per day of the window; weeks start on Monday.
let
    W     = Window{0},
    Days  = List.Dates(W[window_start],
                       Duration.Days(W[window_end] - W[window_start]) + 1,
                       #duration(1, 0, 0, 0)),
    DayTable = Table.FromList(Days, Splitter.SplitByNothing(), type table [Date = date]),
    Week  = Table.AddColumn(DayTable, "Week", each Date.StartOfWeek([Date], Day.Monday), type date),
    Month = Table.AddColumn(Week, "Month",
                each Date.ToText([Date], [Format = "yyyy-MM", Culture = "en-US"]), type text)
in
    Month
