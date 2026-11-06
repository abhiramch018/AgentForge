import type { PlanTask } from "../api/types";

interface PlanGraphProps {
  tasks: PlanTask[];
}

export function PlanGraph({ tasks }: PlanGraphProps) {
  if (!tasks.length) {
    return null;
  }
  return (
    <section className="panel" aria-labelledby="plan-graph-heading">
      <h2 id="plan-graph-heading">Plan tasks</h2>
      <ol className="plan-list">
        {tasks.map((task) => (
          <li key={task.id} className="plan-item">
            <div className="plan-item-head">
              <code className="mono">{task.id}</code>
              <span className="role-chip">{task.role}</span>
            </div>
            <p>{task.description}</p>
            {task.depends_on.length > 0 ? (
              <p className="depends">
                <span className="sr-only">Depends on </span>
                Depends on:{" "}
                {task.depends_on.map((d, i) => (
                  <span key={d}>
                    {i > 0 ? ", " : ""}
                    <code className="mono">{d}</code>
                  </span>
                ))}
              </p>
            ) : (
              <p className="depends muted">No upstream dependencies</p>
            )}
          </li>
        ))}
      </ol>
    </section>
  );
}
