import type { PlanTask } from "../api/types";

/** Accessible dependency list — no heavy graph library. */
export function DependencyList({ tasks }: { tasks: PlanTask[] }) {
  if (!tasks.length) {
    return <p className="muted">No tasks in plan.</p>;
  }
  return (
    <ul className="dep-list" aria-label="Task dependencies">
      {tasks.map((task) => (
        <li key={task.id}>
          <div className="dep-head">
            <code>{task.id}</code>
            <span className="pill">{task.role}</span>
          </div>
          <p>{task.description}</p>
          <p className="muted">
            Depends on:{" "}
            {task.depends_on.length
              ? task.depends_on.map((d) => <code key={d}>{d} </code>)
              : "— (independent)"}
          </p>
        </li>
      ))}
    </ul>
  );
}
