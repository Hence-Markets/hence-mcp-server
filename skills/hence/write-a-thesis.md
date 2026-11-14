# Writing a Hence thesis yourself

You do not have to ask Hence to research an idea. You can do the thinking yourself, with your
own model and your own sources, write the thesis in the format below, have Hence check it,
and put it in the person's Hence library as a draft. They open the draft in Hence, read it,
change what they want and save it. From then on it is their thesis: Hence prices it, replays
its history, and they can trade the basket.

Use this when the person has a view of their own, wants a basket shaped to their constraints
("no leverage", "only names I already follow", "half in cash"), or wants to revise a thesis
they already have. Use Hence's own research instead when they want Hence's pipeline and its
sources behind the idea.

What Hence does for a thesis you wrote: checks it against its own listings and keeps it as a
private draft, labelled as written by you. What Hence does not do: research it, vouch for it,
or turn it into a thesis on your say-so. Only the person's own save does that.

## The method

A thesis is a claim about the world, and a basket that pays if the claim is right. Work in
this order. Each step has a test; do not go on until it passes.

1. **The claim.** One sentence that could turn out false. "AI is big" is not a claim. "The
   hyperscalers' capex is growing faster than the revenue it produces, and the suppliers are
   priced as if it were not" is one.
   *Test: can you say what would have to happen for it to be wrong?*

2. **The mechanism.** How the claim becomes a price move: who earns less or more, through what,
   and why the market has not priced it. This is the part most theses skip.
   *Test: could someone who disagrees with you still follow the chain?*

3. **Why now.** The dated events that will show whether you are right: results, filings,
   decisions, deliveries. Give each a date.
   *Test: is there at least one event inside the horizon that would move your confidence?*

4. **The counter-case.** The strongest argument against you, stated the way its best advocate
   would. If the obvious trade is crowded, say so here.

5. **What proves it wrong.** Observable, specific, checkable by someone else. This is what the
   person will hold you to.

6. **Map the exposures before you pick anything.** List what the claim touches:
   - *direct*: the businesses or assets the claim is about;
   - *second order*: who supplies them, finances them, competes with them, or is paid by them.
     A second-order name can deserve a larger weight than a direct one when the event moves
     more of its value. Size by how much the event moves the asset, not by how close it sits;
   - *hedges*: what you hold so the basket expresses the claim and not the whole market.
   Then find which of them Hence lists: `search_markets`, one search per name. Use the
   `market_id` it returns, exactly. Do not invent one, and do not assume a ticker is listed.
   *Test: is anything obvious missing? If a name a reader would expect is left out, either add
   it or say why not in the counter-case.*

7. **One venue per basket.** A basket is traded in one place: perpetuals (Hyperliquid and its
   builder venues) or spot tokens (Robinhood Chain). Do not mix them. Perpetuals can be long or
   short and carry funding; spot tokens are long only. Count each underlying once.

8. **Weights.** Each holding's weight is its share of the money, in percent. Weights and cash
   add up to exactly 100. Size by two things: how much of the asset's value the claim moves
   (more exposure, more weight) and how sure you are of the link (a link you have not checked
   against a source gets less). Then lean against volatility: a calm name needs more weight
   than a wild one to carry the same risk. Do not equal-weight by default; do not put a hedge
   above the idea.

9. **A reason for every holding.** One or two sentences: what it is, its role (core, second
   order, hedge), and why this side. If the link is from your own knowledge and not from a
   source you read, write "Knowledge, unverified:" at the start of the reason. Hence shows
   that to the person.

10. **Sources.** What you actually read, with its address and date. Not what you remember
    reading. No source is better than an invented one: an invented source is the one thing
    that makes the whole thesis untrustworthy.

11. **Checkpoints.** Up to five of the dated events from step 3, each with what to look for
    and whether it would support or contradict the claim.

Then check it, fix what the check says, and save it.

## The format

A thesis is one JSON object. Field names match what `get_thesis` returns, so reading a thesis
and writing one use the same words.

```json
{
  "format": "hence-thesis/1",
  "title": "Overfunded buildout",
  "summary": "If hyperscaler capex keeps outrunning the revenue it produces, AI suppliers de-rate.",
  "mechanism": "How the claim becomes a price move.",
  "why_now": "The dated events that will show it.",
  "counter_case": "The strongest argument against it.",
  "invalidation": "What would prove it wrong.",
  "horizon_days": 180,
  "topic": "AI & compute",
  "cash_weight_pct": 20,
  "holdings": [
    {
      "market_id": "perp:xyz:NVDA",
      "side": "short",
      "weight_pct": 50,
      "role": "core",
      "reason": "Accelerator supplier whose orders follow hyperscaler capex most directly."
    },
    {
      "market_id": "perp:BTC",
      "side": "long",
      "weight_pct": 30,
      "role": "hedge",
      "reason": "Knowledge, unverified: liquidity hedge so the basket is not a bet on risk appetite."
    }
  ],
  "sources": [
    { "title": "Form 10-Q", "url": "https://www.sec.gov/...", "publisher": "SEC", "published_at": "2026-10-28" }
  ],
  "checkpoints": [
    { "by": "2026-11-30", "observable": "Data-centre revenue growth slows in the quarterly results", "direction": "supports" }
  ]
}
```

| Field | Rule |
|---|---|
| `format` | `hence-thesis/1` |
| `title` | Up to 180 characters. The claim, not a label |
| `summary` | Up to 1,000 characters. If X, then Y |
| `mechanism` | Up to 4,000 characters |
| `why_now`, `counter_case`, `invalidation` | Up to 2,000 characters each. None may be empty |
| `horizon_days` | 1 to 10,950 |
| `topic` | Up to 80 characters |
| `cash_weight_pct` | 0 up to, but not including, 100 |
| `holdings` | 1 to 20. One venue. Each `market_id` once |
| `holdings[].market_id` | Exactly as `search_markets` returned it |
| `holdings[].side` | `long` or `short`. Spot tokens: `long` only |
| `holdings[].weight_pct` | Above 0. All weights plus `cash_weight_pct` equal 100 |
| `holdings[].role` | `core`, `second_order` or `hedge` |
| `holdings[].reason` | Required, up to 1,000 characters |
| `sources` | Optional, up to 30. `url` must be an https address you read |
| `checkpoints` | Optional, up to 5. `by` is a date (`YYYY-MM-DD`) after today and within 12 months; `observable` is up to 300 characters; `direction` is `supports` or `contradicts`: which way your view moves if it happens |

## Check, then save

1. **`check_thesis`** with the object. It saves nothing. It answers `ok`, a list of
   `problems` (each names the field and what to change) and a list of `warnings` (things that
   are allowed but that the person should know: a holding with no price history, a basket
   concentrated in one name, no sources). It also returns the thesis as Hence would store it,
   with each holding's name and venue filled in from Hence's own listings, so you can see
   exactly what the person will see.
2. Fix every problem. A warning is yours to weigh; tell the person about the ones that matter.
3. **Show the person the thesis and ask before you save it.** Saving puts it in their library.
4. **`save_thesis_draft`** with the same object. It lands in their Hence library as a private
   draft, labelled as written by you. The answer has its `url`. Give them the link: they
   review the draft in Hence and save it there. That save is what makes it their thesis, and
   they are the one who shares it or trades it. You can do neither. Saving the same object
   again updates the same draft.

From a terminal:

```bash
hence draft new > thesis.json       # a blank thesis to fill in
hence draft check thesis.json       # what is wrong with it; nothing is saved
hence draft save thesis.json        # save it to the connected account (asks first)
```

## What you must not do

- Do not save a thesis the person has not seen.
- Do not present it as Hence's research. It is yours.
- Do not quote a return for it. Once the person has saved it as a thesis, Hence replays its
  price history; read that with `get_my_thesis`, and say it is hypothetical price history,
  not a track record.
- Do not write the person's amounts, balances or positions into the thesis text. A thesis can
  be made public by its owner later; their holdings are not part of the idea.
- Do not tell the person to buy it. Describe what it claims and holds; the decision is theirs.
