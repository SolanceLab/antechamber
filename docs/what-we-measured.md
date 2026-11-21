# What we measured

[← back to the README](../README.md)

**Where the lead comes from.** Most of the recall lead comes from returning the lines around each match. With those lines included, Antechamber leads standard search by 6.9 points on LoCoMo and 5.1 points on LongMemEval-S.

| Search comparison | Standard search with surrounding lines | Antechamber |
|---|---|---|
| LoCoMo | 4,703 characters · **1,773 tokens** | 8,253 characters · **2,633 tokens** |
| LongMemEval-S | 25,746 characters · **8,860 tokens** | 24,964 characters · **7,041 tokens** |

The character figures above use the text fields returned by search. Tokens were counted on the rendered text the answering model received, averaging 5,037 and 7,327 characters on LoCoMo and 25,979 and 20,673 on LongMemEval-S, in table order.

All token figures are means per question, measured on 21 Nov 2026 with Anthropic's token counting endpoint and `claude-sonnet-5-5`. [Endpoint documentation](https://platform.claude.com/docs/en/api/messages/count_tokens)

The LongMemEval-S equal-text comparison refers to search at roughly 25,000 returned characters per arm. The 5.1-point lead measures evidence retrieval.

Next benchmark: LongMemEval-M (about 500 conversations per question), recall only, planned and run slowly; see [antechamber-benchmark](https://github.com/SolanceLab/antechamber-benchmark).

**Recall** (the headline, proven at full scale). Antechamber finds the evidence needed to answer far more often than standard vector search, on every question the method never saw:

| Benchmark | Questions | Standard search | Antechamber | Gain |
|---|---|---|---|---|
| LoCoMo | 1,493 | 56.7% | **78.9%** | +22.2 points |
| LongMemEval (S, cleaned) | 412 | 62.6% | **77.7%** | +15.0 points |

"Found" = every evidence line for the question is among what recall returns to the reader. No language model inside recall: local embeddings, keyword shelves, fusion.

## Answering with chat notes

In these tests, the notes were written by [Scriptorium](https://github.com/SolanceLab/scriptorium)'s notes writer, a script that reads each conversation and writes short notes from it, because test conversations come with no notes of their own.

The benchmark measured the October instructions. The default instructions shipped today have since changed.

| Setup | Mean characters | Mean tokens | Reserved seats | Writer prompt | Writer model |
|---|---:|---|---:|---|---|
| Chats only | 7,214 | 2,591 | 0 | — | — |
| Generic notes | 7,997 | 2,874 | 3 | `generic_memory.md` | Claude Sonnet 5.5 |
| Our notes plus People | 10,586 | 3,755 | 3 | October `prompt-public.md` | Claude Sonnet 5.5 |

Means use the formatted recall context sent to Qwen for each of the 600 LoCoMo validation questions. The size-matched sample was too small to establish whether notes improve answers at equal text size.

Three models answered the questions. Grok judged the answers using the official LongMemEval judging prompts. The LoCoMo results use strict scores on two sets of 600 questions:

- Small local model (Qwen 3.5 9B): **+6.5 / +6.3 points** for our notes plus People over generic notes, **+12 points** over chats only. The higher-scoring setup also received more text.
- Large models (Claude Sonnet, GPT Sol): our notes plus People tied generic notes. GPT Sol gained 3–5 points over chats only.
- LongMemEval answering: 30 questions per set, too few to distinguish the setups. Sonnet scored 97% on validation with chats only.

**Vault notes** (vault-map adapter; measured on our own build notes, not a public benchmark). The right note among the top results for a fresh question, 24 questions per set, three never-seen sets: vault-map v1.2.0 **3, 2, 2 of 24**; section search **20, 15, 18 of 24**. Inside Antechamber's final top 8 the section search lifts vault notes from 0–1 to 6–9 of 24. Demonstration on our notes, small samples.

**Honest limits of these numbers.**

- Answer accuracy with standard search was not measured; the recall gain is the claim.
- LongMemEval answering used 60 + 28 questions, not all 500.
- Judge: Grok, not GPT-4o; LoCoMo judged with LongMemEval's general prompt; F1 reported alongside.
- "Not mentioned" (trick) questions: every reader answers instead of abstaining (0–30%), notes or not.

**Method and reproduction:** [antechamber-benchmark](https://github.com/SolanceLab/antechamber-benchmark).

**Benchmark data licences.** LoCoMo: CC BY-NC 4.0 (cite, do not redistribute its text commercially). LongMemEval: MIT. Neither dataset is included in this repo.
