# Open questions (the orchestrator asks at most 5 per round, blocking ones first)

Each question keeps a permanent ID (Q101, Q102 …) and is stored as one full block; asking = copying the block.
An answered question moves to DECISIONS.md with {{owner_name}}'s words verbatim and the time.

## Example (delete it once a real question is here)

**Q101: Should the survey cleaning drop answers with an empty age field?**
- **Project and row:** Project A; {{task_id_prefix}}-1 "Clean the survey dataset" (link).
- **Background:** the cleaned survey feeds the first analysis ({{task_id_prefix}}-2).
- **What happened:** 1,200 answers checked; 31 have no age (report: ws01:/home/alex/projects/survey/check.md).
- **Where:** ws01, folder ws01:/home/alex/projects/survey/.
- **When:** open since Nov 9 11:40; needed before the cleaning step runs (planned Nov 10 morning).
- **Why you are asked:** dropping rows changes the sample; that is a scientific choice.
- **Options:** A drop them (1,169 left; can be undone, raw file kept). B keep them and impute age (adds a model
  choice). C keep them and leave age empty (later steps must handle gaps).
- **Constraints:** none.
- **Recommendation:** A, because age is a main covariate and 2.6 % is a small loss.
- **Answer expected:** "Q101 A", "Q101 B" or "Q101 C".
