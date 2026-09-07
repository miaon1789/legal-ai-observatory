let
    Source = Sql.Database("10.211.55.2,1433", "LegalAIObservatory"),
    Evaluation = Source{[Schema = "evaluation", Item = "vw_case_results"]}[Data]
in
    Evaluation
