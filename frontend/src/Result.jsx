const RECOMMENDATION_COLORS = {
  accept: "success",
  accept_with_revisions: "primary",
  reject: "danger",
  needs_more_information: "warning",
};

function Points({ title, items, className }) {
  if (!items?.length) return null;
  return (
    <div className="col-md-6">
      <div className={`fw-semibold small ${className}`}>{title}</div>
      <ul className="small mb-2 ps-3">
        {items.map((item, i) => (
          <li key={i}>{item}</li>
        ))}
      </ul>
    </div>
  );
}

export default function Result({ response, scoreRange }) {
  const { result } = response;
  const max = scoreRange?.[1];
  const score = (value) => (value == null ? "–" : max != null ? `${value} / ${max}` : value);
  const color = RECOMMENDATION_COLORS[result.recommendation] || "secondary";

  return (
    <div>
      <div className="d-flex flex-wrap align-items-center gap-3 mb-3">
        <span className={`badge fs-6 text-bg-${color}`}>{result.recommendation.replaceAll("_", " ")}</span>
        <span>
          Weighted score <strong>{score(result.weighted_score)}</strong>
        </span>
        <span>
          Overall score <strong>{score(result.overall_score)}</strong>
        </span>
      </div>
      <p>{result.summary}</p>

      {result.skill_evaluations.map((item) => (
        <div className="card mb-3" key={item.skill}>
          <div className="card-header d-flex justify-content-between">
            <span>
              {item.skill} <span className="text-body-secondary small">weight {item.weight}</span>
            </span>
            <strong>{score(item.score)}</strong>
          </div>
          <div className="card-body">
            <div className="row">
              <Points title="Strengths" items={item.strengths} className="text-success" />
              <Points title="Weaknesses" items={item.weaknesses} className="text-danger" />
            </div>
            {item.comments && <p className="small mb-0">{item.comments}</p>}
          </div>
        </div>
      ))}

      {result.questions_for_applicant.length > 0 && (
        <>
          <h2 className="h6">Questions for the applicant</h2>
          <ol className="small">
            {result.questions_for_applicant.map((q, i) => (
              <li key={i}>{q}</li>
            ))}
          </ol>
        </>
      )}

      <p className="text-body-secondary small mb-0">
        {response.area && `Area ${response.area} · `}
        {response.provider} · {response.model}
      </p>
    </div>
  );
}
