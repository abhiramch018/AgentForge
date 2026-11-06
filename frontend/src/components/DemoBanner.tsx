const DEMO = import.meta.env.VITE_DEMO_MODE === "true";

export function DemoBanner() {
  if (!DEMO) return null;
  return (
    <div className="demo-banner" role="status">
      Demonstration mode — uses the real AgentForge API flow with fake LLMs.
      Not live model quality. No paid API calls.
    </div>
  );
}
