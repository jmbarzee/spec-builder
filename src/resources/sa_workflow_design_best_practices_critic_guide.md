# SA Workflow Design & Best Practices Guide

## Purpose

This document is an **agent-friendly review guide** for a critic agent evaluating Temporal workflow designs, specs, architecture docs, implementation plans, and code-generation outputs.

It converts the original SA workflow design notes into a normalized format optimized for:

- critique and review passes
- design-gap detection
- best-practice enforcement
- structured scoring
- actionable remediation guidance

The critic agent should use this guide to identify **risks, anti-patterns, missing decisions, and production-readiness gaps** in Temporal-based designs.

---

## How the critic agent should use this guide

When reviewing a workflow design, the critic agent should:

1. determine whether the design is actually a good fit for Temporal
2. inspect use of Temporal primitives
3. identify determinism, history-growth, retry, timeout, and payload-size risks
4. check whether the design follows platform best practices
5. distinguish between acceptable tradeoffs and clear anti-patterns
6. produce concrete remediation advice
7. avoid requiring perfection; prioritize correctness, operability, and production readiness

The critic agent should return:

- a summary judgment
- severity-ranked issues
- specific recommendations
- open questions that block approval
- a checklist result by category

---

## Review goals

The goal of the review is to:

- ensure Temporal primitives are used correctly
- identify gotchas before production
- answer workflow-design questions consistently
- increase confidence in the design path to production
- catch anti-patterns early

### Non-goals

The review is **not** intended to:

- determine whether Temporal is suitable in the first place unless the design is clearly misfit
- deeply debug production incidents
- produce a perfect design in one pass
- replace SDK docs, support, or detailed implementation debugging

---

## Expected input for the critic agent

The critic agent should work best when given some combination of:

- a use-case description
- a workflow diagram
- workflow code or pseudocode
- Temporal UI execution history
- activity definitions
- signal/query/update usage
- timeout and retry settings
- task queue and worker topology
- expected scale, volume, and duration

If one or more of these are missing, the critic agent should explicitly call that out.

---

## Critic output contract

The critic agent should emit results in this structure.

```md
# Workflow Design Critique

## Verdict
- status: approve | approve_with_changes | needs_revision | high_risk
- summary: <1-3 paragraph summary>

## Top Issues
1. [severity] <issue title>
   - why it matters
   - evidence from design
   - recommended fix

## Category Review
### Temporal fit
### Workflows
### Child Workflows
### Activities
### Signals
### Queries
### Updates
### Workers and Task Queues
### Timers / Schedules / Cron
### Data / Payloads / Converters
### Visibility
### Versioning
### Long-running execution

## Open Questions
- <question>

## Checklist Result
- pass/fail/inconclusive per item
```

### Severity levels

Use these severity levels:

- `critical` — likely to fail, become non-deterministic, exceed limits, or cause production incidents
- `high` — serious design problem likely to impair correctness, scale, or operability
- `medium` — suboptimal design likely to cause friction, cost, or maintenance issues
- `low` — improvement opportunity or missing optimization
- `info` — observation or tradeoff explanation

---

## 1. Temporal fit review

### What the critic should evaluate

Determine whether the use case is appropriate for Temporal.

### Good fit indicators

- long-running, multi-step business process
- need for durable execution
- retries and failure recovery matter
- coordination across services matters
- human-in-the-loop or asynchronous progression exists
- waiting, timers, external callbacks, or approvals are required

### Poor fit indicators

- extreme low-latency requirements where milliseconds matter
- simple synchronous read-only operations
- using Temporal as a data plane instead of a control plane
- pushing large data blobs through workflows unnecessarily

### Critic guidance

Flag a design if it uses Temporal primarily for:

- direct query serving
- high-frequency micro-latency trading or similarly latency-critical paths
- bulk data transport instead of orchestration

### Example questions

- What business process is being orchestrated?
- Where does Temporal fit in the architecture?
- What starts the workflow?
- How often does it run?
- How long does it run?
- Is Temporal being used only where durable orchestration is needed?

---

## 2. Workflow review

### What the critic should evaluate

Check whether workflow definitions follow Temporal execution constraints and best practices.

### Required best practices

#### Determinism

The workflow must be deterministic.

The critic should flag:

- non-deterministic branching
- reliance on wall clock, random values, or mutable external state in workflow code
- use of run IDs for logic
- code changes that would break replay without versioning

The critic should ask:

- Does the design acknowledge workflow replay?
- Are replay tests or replayer usage included?
- Is there a versioning plan for workflow evolution?

#### Event history awareness

The workflow must account for event history growth.

The critic should flag:

- unbounded loops without Continue-As-New strategy
- too many signals, activities, child workflows, or updates in one execution
- large payloads repeated in history
- no partitioning strategy for long-running or high-volume workflows

Recommended guidance:

- use Continue-As-New at or before warning levels
- partition work with child workflows where justified
- keep workflow state compact

#### Workflow ID usage

The critic should verify:

- whether workflow IDs are meaningful business identifiers where appropriate
- whether uniqueness constraints are intentionally used as idempotency protection
- whether reuse policy is explicitly considered for closed workflows

The critic should flag:

- frequent reuse of the same workflow ID
- logic based on run ID

#### Timeouts

The critic should assume default workflow timeouts are usually correct.

Flag designs that:

- set workflow execution or run timeouts without clear reason
- use workflow timeouts instead of explicit timers for business deadlines
- ignore workflow task timeout implications when using large histories or expensive converters

Recommended guidance:

- use timers inside the workflow to model business timeouts
- only tune workflow task timeout when justified

#### Failure semantics

The critic should verify that the design understands:

- non-Temporal failures fail workflow tasks and are retried
- workflow failures should not be used for transient operational issues
- workflow retry policies are generally not recommended

Flag designs that:

- add workflow retries without strong justification
- assume workflow exceptions behave like activity retries

---

## 3. Child workflow review

### Default rule

When in doubt, prefer an activity over a child workflow.

### Valid reasons to use child workflows

- partition large workloads to control event history growth
- isolate ownership boundaries
- route execution strategically by task queue, trust boundary, or workload type
- model durable sub-processes with their own lifecycle

### Invalid or weak reasons

- organizing code structure
- reducing cost
- avoiding normal language modularity

### Critic checks

The critic should flag:

- child workflows used only for code organization
- too many child workflows started by one parent
- missing parent close policy reasoning
- assumptions that parent and child can share local state

### Guidance

- starting more than 1000 child workflows from one parent is a design smell
- starting hundreds may already create latency concerns
- parent/child state sharing must happen through signals or explicit communication

### Questions to ask

- Why is this a child workflow instead of an activity?
- How many children can one parent start?
- Does the parent need the child result?
- What happens to children if the parent closes?

---

## 4. Activity review

### Core principle

Activities must be idempotent and treated as at-least-once.

### Idempotency

The critic should flag:

- side-effecting activities without idempotency protection
- reliance on “this only runs once” assumptions
- lack of idempotency keys where external actions occur

### Granularity

The critic should evaluate whether activities are too broad or too granular.

Guidance:

- it is acceptable to group multiple operations in one activity if they share timeout and retry boundaries intentionally
- too much bundling reduces observability and can make retries too coarse
- too much fragmentation increases event history and orchestration complexity

### Long-running activities

The critic should verify:

- long-running activities heartbeat
- heartbeat timeout is short enough for timely failure detection
- heartbeat payload contains resumable progress when useful

### Payload size

The critic should flag:

- large activity inputs/outputs that risk the 2 MB payload limit
- designs that pass large blobs rather than references
- repeated large payload serialization through workflow history

Recommended guidance:

- pass references instead of full payloads
- move large data handling into activities
- compress payloads via a data converter where appropriate

### Polling

The critic should evaluate whether polling is designed efficiently.

Guidance:

- frequent polling should usually happen inside one activity loop
- infrequent polling can often use activity retries
- signals or async activity completion may be better than polling altogether

### Timeouts

The critic should verify:

- each activity has individually considered timeout settings
- Start-To-Close is usually set
- Schedule-To-Close is used intentionally
- Schedule-To-Start is generally left unset unless host-specific routing or queue-unavailability detection is needed

Flag designs that:

- apply the same timeout blindly to every activity
- set server-side timeouts shorter than normal upstream completion time
- ignore duplicate-action risk from retries

### Local activities

The critic should prefer regular activities unless there is a specific need for:

- very high throughput
- very short-lived work
- large fan-out of short tasks

Flag designs where local activities:

- run longer than a few seconds
- are used without understanding tradeoffs
- need worker-side rate limiting or routing that local activities cannot provide well

### Cancellation

The critic should verify:

- cancellable activities heartbeat, or use supported local-activity semantics where applicable

---

## 5. Signal review

### What the critic should evaluate

Signals are appropriate when workflow state must change during execution.

### Required understanding

Signals are:

- recorded in event history
- delivered with the next workflow task
- ordered per workflow execution
- subject to workflow replay and determinism requirements

### Critic checks

Flag designs where:

- signal handlers perform heavy logic or invoke activities directly without strong reason
- signal handlers are not idempotent
- signal rates are too high for one workflow execution
- signal volume risks history growth or blocks Continue-As-New

### Important operational guidance

- sustained signal rates should generally stay at or below a few per second per workflow
- a single workflow execution in Temporal Cloud has practical and hard limits on signal volume
- flooding one workflow with signals can starve progress and prevent Continue-As-New

### Best-practice guidance

Signal handlers should usually:

- update workflow state only
- avoid expensive computation
- let the main workflow logic react afterward

### Questions to ask

- What sends the signal?
- How often can it happen?
- Can duplicates occur?
- Is Continue-As-New required?
- Could the signal rate prevent it?

---

## 6. Query review

### What the critic should evaluate

Queries are best for reading workflow state.

### Required rules

- queries are synchronous
- queries must be read-only
- queries are available for running and completed workflows, but require a worker on the task queue

### Critic checks

Flag designs where:

- queries mutate workflow state
- external stores are used for live workflow state when queries would be simpler
- the design assumes query data is always available without workers running

---

## 7. Update review

### What the critic should evaluate

Updates are appropriate when callers need validated, synchronous workflow state changes.

### Required rules

- update handlers must be deterministic
- update handlers must be idempotent
- validation is recommended
- validators must not mutate workflow state
- rejected updates via validation are not written to history

### Critic checks

Flag designs where:

- update handlers mutate state non-deterministically
- validation is missing for business-critical mutations
- the design does not consider latency or handler contention

### Additional guidance

Invoking activities from update handlers is generally acceptable outside Java-specific caveats, but should still be justified and designed carefully.

If low latency matters, the critic should ask whether early-return patterns are needed.

---

## 8. Worker and task queue review

### What the critic should evaluate

Check the worker topology and task queue strategy for correctness, availability, and routing intent.

### Required best practices

- run at least two workers for high availability
- all workers on the same task queue must register identical workflows and activities that may be dispatched there

### Unique task queues are justified for

- rate limiting
- routing to special hardware such as GPUs
- worker-local filesystem or privileged environment access
- workload isolation or differentiated priority

### Critic checks

Flag designs where:

- task queue splits are arbitrary and add complexity without routing value
- workers on one queue have inconsistent registrations
- strict FIFO ordering is assumed
- queue backlog is intentionally grown without awareness that Temporal does not guarantee FIFO across task consumption

### Rate limiting checks

The critic should inspect whether:

- worker-side concurrency and rate limits are intentionally configured
- server-side task queue activity limits are configured consistently across workers

### Questions to ask

- Why are there multiple task queues?
- Are there unique hardware or trust-boundary requirements?
- Is ordering assumed?
- Is backlog growth acceptable?

---

## 9. Timer, schedule, and cron review

### Timers

The critic should verify:

- timers are used for relative delays inside a workflow
- timer durations are at least one second when reliability matters

### Schedules

Use schedules when:

- a whole workflow execution must start at a calendar time
- execution is recurring
- a scheduled launch is more appropriate than an internal timer

Use timers instead when:

- the delay is relative to workflow state
- the delay belongs inside an already running workflow
- schedule action-per-second limits would be exceeded

### Schedule checks

The critic should flag:

- schedules used for single delayed starts when a normal workflow plus timer would be simpler
- no consideration of overlap policy
- no consideration of jitter when many schedules fire together
- backfill strategy that risks hidden buffering limits

### Cron jobs

The critic should generally recommend schedules instead of cron workflows.

Flag designs that:

- rely on cron when schedules are better supported
- combine cron with Continue-As-New assumptions

---

## 10. Side effect review

### What the critic should evaluate

Side Effects should be used carefully.

### Guidance

- if the side effect can fail, prefer an activity
- side effects are not implemented in Core SDKs; local activities are the fallback there

### Critic checks

Flag designs where:

- side effects are used for failure-prone work
- side effects are treated like general-purpose integration hooks

---

## 11. Data converter and payload codec review

### What the critic should evaluate

Check whether custom serialization, compression, or encryption choices are safe and operationally sound.

### Best practices

- compression is generally recommended
- encryption may justify a custom data converter or codec
- key rotation must be considered if encryption is used
- search attributes are not protected by custom data converters or payload codecs

### Cross-language datetime/duration warning

The critic should flag designs that:

- assume datetimes or durations serialize consistently across languages
- mix languages without a common converter format

### Latency considerations

The critic should verify whether converter or codec latency:

- contributes to workflow task execution time
- risks workflow task timeout under large histories or high throughput

---

## 12. Visibility review

### Search attributes

The critic should verify that search attributes are used only for operational visibility, not business logic.

Flag designs where search attributes:

- store sensitive data
- are used to drive workflow decisions
- duplicate large state unnecessarily
- are assumed to be immediately consistent

### Memos and visibility APIs

The critic should note:

- memos are eventually consistent
- visibility APIs are eventually consistent
- designs should not depend on immediate freshness for control flow

---

## 13. Versioning review

### What the critic should evaluate

Check whether workflow evolution is safe for replay and in-flight executions.

### Best practices

- prefer worker versioning for short-running workflows where appropriate
- use workflow patch/versioning APIs when needed for replay compatibility
- do not let patches accumulate forever
- remove version markers only after in-flight executions and retention concerns are addressed

### Critic checks

Flag designs where:

- workflow logic will change without replay-safe versioning
- patches have no retirement plan
- long-running workflows ignore the need to keep patches until completion or Continue-As-New
- replayer-based testing is absent

---

## 14. Continue-As-New and long-running workflow review

### What the critic should evaluate

Check whether long-lived workflows are designed to remain healthy over time.

### Best practices

- use Continue-As-New before history becomes too large
- pass forward enough state to resume safely
- recalculate and re-establish timers after Continue-As-New
- ensure pending activities and handler work are properly resolved before Continue-As-New

### Critic checks

Flag designs where:

- timers are assumed to carry over automatically
- child workflow behavior on parent close is not understood
- signal/update handlers may still be running when Continue-As-New occurs
- no state handoff design exists

---

## 15. Sessions and worker-specific routing review

### Sessions

Sessions are only available in Go.

The critic should note:

- if a worker process dies, session-related activities may be retried together
- sessions should be justified clearly

### Worker-specific task queues

The critic should approve these when they are used to route activities to specific workers for:

- local files
- privileged environments
- specialized hardware
- sticky locality needs

---

## 16. Storage optimization review

### What the critic should evaluate

Check whether long-running workflows control storage growth and event-history cost.

### Best practices

- keep payloads small
- use references for large objects
- use Continue-As-New strategically
- avoid unnecessary history growth through over-fragmentation or excessive messaging

---

## 17. Common design question guidance for the critic agent

The critic agent should use the following default judgments.

### Local activity vs regular activity

Default to regular activities.
Use local activities only when very short execution and high-throughput fan-out justify them.

### Child workflow vs activity

Default to activity.
Use child workflows only when partitioning, lifecycle isolation, or routing semantics justify them.

### Workflow-to-workflow communication

Allowed options include:

- signals
- queries
- updates
- Nexus where applicable
- activity-mediated client calls for cross-namespace interaction where needed

### Large payload handling

Prefer:

- passing references
- moving data-heavy work into activities
- compression
- explicit handling over hidden remote payload fetches when possible

### Large workflow history

Prefer:

- Continue-As-New
- partitioning with child workflows where justified
- reducing per-event data size and message volume

### Parallelism

Parallel execution should use async invocation patterns with promise/future collection and later aggregation.

### Worker-specific activity queues

Use when capabilities, locality, security, or rate-control justify it.

### Schedule vs timer

Use timers for relative delays inside workflows.
Use schedules for calendar-based or recurring launches.

---

## 18. Anti-pattern catalog

The critic agent should explicitly call out these anti-patterns.

### Critical anti-patterns

- non-deterministic workflow logic
- workflow logic depending on external mutable state directly
- passing large data blobs through workflow history
- activity side effects without idempotency
- signal floods against a single workflow
- search attributes used as business-state storage
- workflow retry policy used to compensate for transient activity failures

### High-risk anti-patterns

- child workflows used purely for code organization
- local activities used for long or failure-prone work
- schedules used for one-time delayed starts without need
- cron used where schedules should be used
- missing Continue-As-New strategy in high-volume or long-running workflows
- no replay/versioning strategy

### Medium-risk anti-patterns

- identical timeout policies for all activities
- over-fragmented activities causing event-history bloat
- memo or visibility data assumed to be strongly consistent
- arbitrary task queue splitting

---

## 19. Review checklist

The critic agent should score each item as:

- `pass`
- `fail`
- `inconclusive`
- `not_applicable`

### Workflows

- Workflow use case is appropriate for Temporal
- Workflow logic is deterministic
- Replay/versioning concerns are addressed
- Event history growth is managed
- Workflow ID strategy is intentional
- Workflow timeouts are not misused
- Workflow failure semantics are understood
- Workflow retry policy is not added without strong reason

### Child Workflows

- Child workflows are used only where justified
- Parent does not start an excessive number of children
- Parent close policy is intentional
- Parent/child communication model is explicit

### Activities

- Activities are idempotent
- Activity granularity is appropriate
- Long-running activities heartbeat
- Payload sizes are safe
- Large data is passed by reference where possible
- Polling strategy is efficient
- Timeout settings are activity-specific
- Retry policy is intentional
- Cancellation handling is correct
- Local activities are justified

### Signals

- Signals are used where runtime state change is needed
- Signal handlers are deterministic and idempotent
- Signal handlers avoid heavy work
- Signal volume is safe
- Continue-As-New compatibility is considered

### Queries

- Queries are read-only
- Queries are used appropriately for state reads

### Updates

- Updates are deterministic and idempotent
- Validation is used where appropriate
- Update-handler behavior is well scoped

### Workers and Task Queues

- Worker count supports high availability
- Registrations are consistent per task queue
- Task queue strategy is intentional
- Rate limiting is considered
- Ordering assumptions are valid

### Timers / Schedules / Cron

- Timers are used appropriately
- Schedule usage is justified
- Overlap policy is intentional
- Jitter/backfill considerations are addressed
- Cron is avoided unless specifically required

### Data / Visibility

- Data converter choices are intentional
- Compression/encryption concerns are handled
- Datetime cross-language serialization is addressed
- Search attributes are used only for visibility
- Sensitive data is not stored in search attributes
- Eventual consistency is understood for memos/visibility

### Versioning / Long-running execution

- Replay-safe versioning strategy exists
- Patches have a cleanup plan
- Continue-As-New strategy exists where needed
- Timer and state carry-forward behavior is handled

---

## 20. Critic decision rubric

### Approve

Use when the design:

- follows Temporal fundamentals correctly
- shows awareness of determinism, idempotency, history growth, and failure handling
- has no critical or high-severity flaws

### Approve with changes

Use when the design is broadly sound but has:

- several medium-severity issues
- missing operational details
- review questions that should be resolved before production

### Needs revision

Use when the design has:

- one or more high-severity issues
- unclear Temporal primitive selection
- insufficient handling of history, payload size, retries, or long-running behavior

### High risk

Use when the design includes:

- critical non-determinism risks
- obvious misuse of Temporal as data plane
- major idempotency gaps
- unbounded history growth
- signal or payload patterns likely to fail in production

---

## 21. Open questions template for the critic agent

When the source material is incomplete, the critic agent should ask targeted questions such as:

- What is the expected peak workflow start rate?
- What is the longest expected workflow duration?
- How many activities and child workflows can occur per execution?
- What are the largest expected payload sizes?
- Are any activities side-effecting external systems?
- What is the retry and timeout policy per activity?
- How many signals or updates can one execution receive?
- Is Continue-As-New planned?
- Are there cross-namespace or cross-service workflow communications?
- Are search attributes used only for visibility?
- What is the versioning plan for workflow changes?

---

## 22. Source summary preserved from original guide

This normalized critic guide preserves the original intent of the source material:

- help SAs and reviewers focus on key workflow design elements
- reinforce correct usage of Temporal primitives
- catch common gotchas
- guide iterative design reviews rather than one-shot perfection

It also preserves the major topic areas:

- workflows
- child workflows
- activities
- signals
- queries
- updates
- workers and task queues
- timers
- schedules
- cron jobs
- side effects
- data converters
- visibility
- versioning
- sessions and worker-specific task queues
- long-running workflow storage optimization

---

## 23. Compact reviewer prompt

This section can be used directly to power a critic agent.

```md
You are a Temporal workflow design critic.

Review the provided workflow design, code, diagram, or spec against Temporal workflow best practices.

Your job is to:
- determine whether the design is a good fit for Temporal
- identify determinism, idempotency, timeout, retry, payload-size, signal-volume, history-growth, visibility, and versioning risks
- flag anti-patterns
- distinguish critical issues from minor improvements
- recommend concrete fixes
- ask targeted open questions where information is missing

Output sections:
1. Verdict
2. Top Issues
3. Category Review
4. Open Questions
5. Checklist Result

Severity levels:
- critical
- high
- medium
- low
- info

Default guidance:
- prefer Activities over Child Workflows unless lifecycle partitioning is justified
- prefer regular Activities over Local Activities unless very short high-throughput work justifies local execution
- prefer Timers over Schedules for relative delays inside an execution
- prefer Schedules over Cron Jobs for recurring starts
- prefer references over large payloads
- prefer Queries for read-only state access
- avoid Search Attributes for business logic
- use Continue-As-New before workflow history becomes too large
- require deterministic workflow code and idempotent activities

Be specific, practical, and conservative. Do not assume missing details are safe.
```

---

## 24. Optional structured scoring schema

```json
{
  "verdict": "approve_with_changes",
  "summary": "The design is broadly sound but has unresolved event history and signal-volume risks.",
  "issues": [
    {
      "severity": "high",
      "category": "Signals",
      "title": "Signal volume may block Continue-As-New",
      "why_it_matters": "High sustained signal rates can prevent workflow progress and cause history-limit failures.",
      "recommendation": "Aggregate signals, shard by workflow, or reduce per-workflow signal rate."
    }
  ],
  "checklist": {
    "workflow_determinism": "pass",
    "activity_idempotency": "pass",
    "history_management": "fail",
    "signal_safety": "fail",
    "versioning_strategy": "inconclusive"
  },
  "open_questions": [
    "What is the maximum sustained signal rate per workflow execution?"
  ]
}
```

---

## 25. Final note for maintainers

This file is intended to be:

- stable enough for agent prompts
- readable by humans
- extensible as Temporal features evolve
- opinionated toward production safety

When updating it, preserve:

- explicit rules
- default recommendations
- anti-pattern detection
- structured output expectations
- practical review questions

