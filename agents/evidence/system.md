# DBKit Evidence Agent

Placeholder — Phase-03 scope.

In Phase-02, Evidence Agent does not decide what to collect and does not
structure EvidenceBundle output.

Phase-02 evidence requirements are decided by the MySQL Analyzer Agent in
`evidence_planning` mode. Runtime converts that EvidenceRequest into a guarded
CollectionPlan and executes deterministic collector tools to produce RawEvidence.
