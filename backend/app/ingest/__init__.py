"""Receipt ingest pipeline (Phase 2C).

A job moves ``captured -> ocr -> header -> lines -> resolve -> review``. Each stage
is a function that reads the latest results of earlier stages, does one unit of
work, and returns an outcome; :mod:`app.ingest.stages` persists an
``ingest_stage_result`` for every attempt and advances the job. Receipt text and
model output are untrusted data throughout: they are parsed against schemas,
never logged, never interpolated into SQL, and never treated as instructions.
"""
