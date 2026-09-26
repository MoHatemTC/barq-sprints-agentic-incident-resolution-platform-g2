## S2.6 no-regression check — baseline rows against the shared collection

`barq_knowledge_base` holds the KB articles *and* the manual's extracted pages, so the two compete for the same 5 slots. 37 baseline rows (32 answerable), scored twice: `kb_only` filters the manual out, `mixed` is what a live query gets. Same collection, same code, same pass — the `is_stressor` filter is the only difference.

| mode | hit@k (kb only) | hit@k (mixed) | Δ | MRR (kb only) | MRR (mixed) | hits lost | gained | verdict |
|---|---|---|---|---|---|---|---|---|
| `dense` | 0.281 | 0.281 | +0.000 | 0.250 | 0.250 | 0 | 0 | held |
| `hybrid` | 0.312 | 0.281 | -0.031 | 0.258 | 0.245 | 1 | 0 | **REGRESSION** |
| `hybrid_rerank` | 0.312 | 0.250 | -0.062 | 0.232 | 0.214 | 2 | 0 | **REGRESSION** |

### Where the manual took a slot

A manual section has no KB number, so an empty string in a result list is a stressor point. Counted per baseline query:

| mode | queries with 1 manual hit in top-k | queries with 2+ |
|---|---|---|
| `dense` | 4 | 0 |
| `hybrid` | 13 | 0 |
| `hybrid_rerank` | 26 | 0 |

#### `INC1027` — New starter locked out on day one and also cannot see the finance shared folder

- expects: `['KB0005', 'KB0020']`
- kb only (first hit at rank 4): `['7.4', 'KB0003', '6.2', 'KB0005']`
- mixed: `['7.4', '6.2', 'KB0003', '']`  ← `''` is a manual page

#### `INC1011` — User locked out and also cannot connect VPN after password change this morning

- expects: `['KB0005', 'KB0001']`
- kb only (first hit at rank 3): `['3.2', '7.2', 'KB0005', '7.4']`
- mixed: `['', '3.2', '7.2']`  ← `''` is a manual page

#### `INC1027` — New starter locked out on day one and also cannot see the finance shared folder

- expects: `['KB0005', 'KB0020']`
- kb only (first hit at rank 4): `['3.2', '7.4', '6.2', 'KB0005']`
- mixed: `['', '3.2', '7.4', '6.2']`  ← `''` is a manual page
