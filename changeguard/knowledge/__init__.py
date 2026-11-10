"""Pipeline A: build the rule catalog from real insurer and regulator documents.

  Ingestor -> Segmenter -> Extractor (rule-based baseline, or Amazon Nova)
  -> Grounding verifier -> Reconciler -> catalog check -> analyst review
"""
