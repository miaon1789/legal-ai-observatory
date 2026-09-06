USE LegalAIObservatory;
GO

/* ==========================================================================
   vw_retention_cohort
   Page 2. Grain: cohort week x level.

   Of the lawyers whose *first ever* session fell in ISO week W, the share with
   at least one session in week W+4. This is the metric that separates a real
   rollout from a training-day spike: a firm can put 400 people through an
   induction and post a fine adoption rate for one month while nobody comes
   back.

   It is deliberately not a DAX measure. A cohort calculation is expressible in
   DAX and unpleasant to read there, and it is business logic -- the definition
   of "came back" belongs beside the other rules, not inside a visual.

   Two things the view has to get right, both of which make the number look
   worse if ignored:

   1. Cohorts whose W+4 week falls outside the data window are INCOMPLETE, not
      low. They are returned with retained_lawyers = NULL and is_complete = 0 so
      the visual can drop them instead of drawing a cliff that is an artefact of
      where the extract ends.

   2. The follow-up window is the single week W+4, not "any time after W". The
      looser definition rises monotonically with the age of the cohort, so early
      cohorts always look better and the trend is an artefact of the calendar.
   ========================================================================== */
CREATE OR ALTER VIEW dbo.vw_retention_cohort AS
WITH sess AS (
    SELECT s.lawyer_id, d.[date], d.iso_year_week
    FROM dbo.fact_ai_session s
    JOIN dbo.dim_date d ON d.date_id = s.date_id
),
-- Monday of each ISO week, so cohorts can be compared by date arithmetic.
weeks AS (
    SELECT iso_year_week, MIN([date]) AS week_start
    FROM dbo.dim_date
    GROUP BY iso_year_week
),
window_bounds AS (
    SELECT MIN([date]) AS first_day, MAX([date]) AS last_day FROM sess
),
first_use AS (
    SELECT lawyer_id, MIN([date]) AS first_date
    FROM sess GROUP BY lawyer_id
),
cohort AS (
    SELECT f.lawyer_id, l.[level], w.iso_year_week AS cohort_week, w.week_start
    FROM first_use f
    JOIN dbo.dim_lawyer l ON l.lawyer_id = f.lawyer_id
    JOIN dbo.dim_date   d ON d.[date]    = f.first_date
    JOIN weeks          w ON w.iso_year_week = d.iso_year_week
),
-- Did the lawyer use it again in the week starting exactly 28 days later?
returned AS (
    SELECT c.lawyer_id,
           MAX(CASE WHEN s.[date] >= DATEADD(DAY, 28, c.week_start)
                     AND s.[date] <  DATEADD(DAY, 35, c.week_start)
                    THEN 1 ELSE 0 END) AS came_back
    FROM cohort c
    JOIN sess   s ON s.lawyer_id = c.lawyer_id
    GROUP BY c.lawyer_id
)
SELECT
    c.cohort_week,
    c.week_start,
    c.[level],
    COUNT(*) AS cohort_lawyers,
    CASE WHEN DATEADD(DAY, 34, c.week_start) <= b.last_day
         THEN SUM(r.came_back) END AS retained_lawyers,
    CASE WHEN DATEADD(DAY, 34, c.week_start) <= b.last_day
         THEN CAST(SUM(r.came_back) AS DECIMAL(9,4)) / COUNT(*) END AS week4_retention,
    CAST(CASE WHEN DATEADD(DAY, 34, c.week_start) <= b.last_day
              THEN 1 ELSE 0 END AS BIT) AS is_complete
FROM cohort c
JOIN returned r ON r.lawyer_id = c.lawyer_id
CROSS JOIN window_bounds b
GROUP BY c.cohort_week, c.week_start, c.[level], b.last_day;
GO
