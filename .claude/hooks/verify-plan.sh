#!/bin/bash

# Plan verification gate for ExitPlanMode
# Blocks the first attempt so Claude self-verifies the plan.
# On the second attempt (after verification), allows through.

PROJECT_HASH=$(echo "$PWD" | shasum | cut -d' ' -f1)
FLAG="/tmp/.claude-plan-verified-${PROJECT_HASH}"

# If flag exists and is fresh (< 10 min old), allow through
if [ -f "$FLAG" ]; then
    if find "$FLAG" -mmin -10 2>/dev/null | grep -q .; then
        rm -f "$FLAG"
        exit 0
    fi
    rm -f "$FLAG"
fi

# Create flag so next ExitPlanMode attempt passes
touch "$FLAG"

# Block with verification instructions (stderr goes to Claude)
cat >&2 << 'VERIFICATION'
PLAN VERIFICATION REQUIRED

Before presenting this plan to the user, you must verify it as a staff engineer would.
Re-read the plan you wrote and critically evaluate it against ALL criteria below.

## 1. Simplicity
- Can any part be done in a simpler way?
- Are there unnecessary abstractions, layers, or indirection?
- Is this the minimum viable approach, or are you gold-plating?
- Would a mid-level engineer understand this without a walkthrough?

## 2. Over-engineering
- Are you building for hypothetical future requirements?
- Are there config systems, extension points, or abstraction layers that aren't needed right now?
- Are you adding flexibility nobody asked for?

## 3. First Principles
- If any part feels force-fitted, step back. What is the actual problem? What is the simplest way to solve exactly that?
- Don't inherit complexity from existing patterns if a cleaner path exists.
- Don't carry forward assumptions from how things were done before if they don't apply.

## 4. Design Principles & Project Guidelines
- Does the plan follow CLAUDE.md rules?
- Single responsibility per module/function?
- No leaky abstractions (modules receiving more data than they need)?
- Files staying under 200-300 lines?
- Proper separation of concerns?
- RLS and tenant isolation maintained?
- No security violations (key exposure, bypassed auth)?
- Credit system rules followed?
- Database migrations handled correctly?

## 5. Backward Compatibility
- Does this plan break existing functionality?
- If this is a continuation of a previous plan that has NOT shipped to production yet:
  backward compat is unnecessary tech debt. Skip it and note why.
- If the affected code IS in production or you are UNSURE:
  flag it and ASK the user whether backward compatibility should be ensured.
- API contract changes: are existing clients affected?

## 6. Potential Problems
- What could go wrong with this approach?
- Are there edge cases the plan doesn't address?
- Are there assumptions that should be validated?
- Are there dependencies on things that might change?
- Migration risks for existing data?
- Performance implications at scale?

## Action Required
1. Review the plan against every criterion above.
2. If issues are found: revise the plan, update the plan file with fixes, then call ExitPlanMode again.
3. If the plan passes all checks: add a brief "Verification Notes" section at the end of the plan summarizing what you checked and any tradeoffs accepted, then call ExitPlanMode again.
4. If you have questions about backward compatibility or requirements, use AskUserQuestion BEFORE calling ExitPlanMode.

Be honest and critical. Catching issues now saves implementation time.
VERIFICATION

exit 2
