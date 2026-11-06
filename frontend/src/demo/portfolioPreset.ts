/** Offline portfolio demo preset — fake-model path; not live intelligence. */

export const PORTFOLIO_DEMO_MARKER = "PORTFOLIO_VENDOR_COMPARE";

export const PORTFOLIO_GOAL = `${PORTFOLIO_DEMO_MARKER}

You are comparing two **fictional** vendors using only the supplied briefs below.
Do not browse the web. Do not invent prices beyond the briefs.

## Decision context
Mid-size SaaS team needs managed container capacity (~40 vCPU / 160 GB). Prefer
predictable cost and EU residency unless multi-region failover is clearly worth the premium.

## Brief: NordicCloud
List price (demo): $4,200 / month for up to 40 vCPU and 160 GB RAM.
Support: Business-hours; 8-hour severity-1 response target.
Strengths: EU data residency; predictable pricing; included staging.
Weaknesses: No multi-region active-active; limited GPU SKUs.

## Brief: HarborStack
List price (demo): $5,100 / month + $600 policy pack.
Support: 24×7 severity-1; 1-hour response target.
Strengths: Multi-region failover; richer observability.
Weaknesses: Higher price; $8,000 kickoff fee.

Produce analyses and a recommendation grounded only in this supplied evidence.
`;

export const PORTFOLIO_ARTIFACT_PATH = "portfolio/vendor_recommendation.md";

export const PORTFOLIO_ARTIFACT_CONTENT = `# Vendor recommendation (portfolio demo)

Proposed write after human approval.

Winner: NordicCloud at $4,200/mo vs HarborStack at $5,100/mo (+$600 policy).
Rationale: lower predictable cost + EU residency for this mid-size profile.
Residual risk: no multi-region active-active.

Labels: offline DemoLLM; supplied-evidence only; not live model intelligence.
`;
