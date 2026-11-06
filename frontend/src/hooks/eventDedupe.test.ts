import { describe, expect, it } from "vitest";
import { mergeLifecycleEvent } from "./useJobEvents";

describe("mergeLifecycleEvent", () => {
  it("dedupes by event_id", () => {
    const first = mergeLifecycleEvent([], {
      event_id: 1,
      event_type: "job.submitted",
    });
    const second = mergeLifecycleEvent(first, {
      event_id: 1,
      event_type: "job.submitted",
    });
    expect(second).toHaveLength(1);
    const third = mergeLifecycleEvent(second, {
      event_id: 2,
      event_type: "job.running",
    });
    expect(third.map((e) => e.event_id)).toEqual([1, 2]);
  });
});
