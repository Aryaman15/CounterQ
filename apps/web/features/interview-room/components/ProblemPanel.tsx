export type CandidateProblemView = {
  title: string;
  statement: string[];
  examples: Array<{ input: string; output: string; explanation: string }>;
  constraints: string[];
  functionSignature: string;
};

type ProblemPanelProps = {
  problem: CandidateProblemView;
};

export function ProblemPanel({ problem }: ProblemPanelProps) {
  return (
    <section className="problem-panel" aria-labelledby="problem-title">
      <div className="problem-scroll">
        <p className="panel-kicker">Problem</p>
        <h1 id="problem-title">{problem.title}</h1>
        <div className="problem-statement">
          {problem.statement.map((paragraph) => (
            <p key={paragraph}>{paragraph}</p>
          ))}
        </div>
        <section aria-labelledby="signature-title" className="problem-section">
          <h2 id="signature-title">Function Signature</h2>
          <pre className="signature-block">{problem.functionSignature}</pre>
        </section>
        <section aria-labelledby="examples-title" className="problem-section">
          <h2 id="examples-title">Examples</h2>
          <div className="examples-list">
            {problem.examples.map((example, index) => (
              <article className="example-block" key={example.input}>
                <h3>Example {index + 1}</h3>
                <dl>
                  <div>
                    <dt>Input</dt>
                    <dd>{example.input}</dd>
                  </div>
                  <div>
                    <dt>Output</dt>
                    <dd>{example.output}</dd>
                  </div>
                  <div>
                    <dt>Explanation</dt>
                    <dd>{example.explanation}</dd>
                  </div>
                </dl>
              </article>
            ))}
          </div>
        </section>
        <section aria-labelledby="constraints-title" className="problem-section">
          <h2 id="constraints-title">Constraints</h2>
          <ul className="constraints-list">
            {problem.constraints.map((constraint) => (
              <li key={constraint}>{constraint}</li>
            ))}
          </ul>
        </section>
      </div>
    </section>
  );
}
