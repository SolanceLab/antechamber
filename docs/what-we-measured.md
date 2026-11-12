# What we measured

[← back to the README](../README.md)

**Where the lead comes from.** Most of the lead comes from returning the lines around each match. Given the same surrounding lines, standard search still trails: by 6.9 points on LoCoMo (where it was handed less text, about 4,700 characters against 8,300) and by 5.1 on LongMemEval at equal text (about 25,000 characters each).

Next benchmark: LongMemEval-M (about 500 conversations per question), recall only, planned and run slowly; see [antechamber-benchmark](https://github.com/SolanceLab/antechamber-benchmark).

**Recall** (the headline, proven at full scale). Antechamber finds the evidence needed to answer far more often than standard vector search, on every question the method never saw:

| Benchmark | Questions | Standard search | Antechamber | Gain |
|---|---|---|---|---|
| LoCoMo | 1,493 | 56.7% | **78.9%** | +22.2 points |
| LongMemEval (S, cleaned) | 412 | 62.6% | **77.7%** | +15.0 points |

"Found" = every evidence line for the question is among what recall hands the reader. No language model inside recall: local embeddings, keyword shelves, fusion.

**With written memories** (optional supplement). On top of Antechamber, a memory writer plus People cards (our method) vs a plain generic memory writer, answered by three readers and judged by Grok with the official LongMemEval judging prompts (LoCoMo strict, 600 questions per set; a step counts only if it holds on two separate sets):

- Small local model (Qwen 3.5 9B): **+6.5 / +6.3** over generic memory, **+12** over conversation only. Holds.
- Large models (Claude Sonnet, GPT Sol): ties generic memory; Sol +3–5 over conversation only.
- LongMemEval: 30 questions per set, too few to separate the setups (Sonnet already 97% on validation with conversation only).

**Vault notes** (vault-map adapter; measured on our own build notes, not a public benchmark). The right note among the top results for a fresh question, 24 questions per set, three never-seen sets: vault-map v1.2.0 **3, 2, 2 of 24**; section search **20, 15, 18 of 24**. Inside Antechamber's final top 8 the section search lifts vault notes from 0–1 to 6–9 of 24. Demonstration on our notes, small samples.

**Honest limits of these numbers.**

- Answer accuracy with standard search was not measured; the recall gain is the claim.
- LongMemEval answering used 60 + 28 questions, not all 500.
- Judge: Grok, not GPT-4o; LoCoMo judged with LongMemEval's general prompt; F1 reported alongside.
- "Not mentioned" (trick) questions: every reader answers instead of abstaining (0–30%), memory or not.

**Method and reproduction:** [antechamber-benchmark](https://github.com/SolanceLab/antechamber-benchmark).

**Benchmark data licences.** LoCoMo: CC BY-NC 4.0 (cite, do not redistribute its text commercially). LongMemEval: MIT. Neither dataset is included in this repo.
