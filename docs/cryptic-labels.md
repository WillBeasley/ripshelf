# Cryptic labels and an AI model

Some discs are mastered with labels nobody could search for: `BTTF2`, `TDKR_D1`, `HP_DH_PT2`. A language model is good at reading those ("BTTF2" is *Back to the Future Part II*), so ripshelf can ask one. It's off until you turn it on.

## How it works

1. The model is told only what the disc says about itself: the volume label, the title on a Blu-ray, the disc type and its running times. Nothing TMDB or any other provider returned is ever sent to it.
2. It answers with up to five full titles, most likely first, and whether the disc looks like a film or a TV series.
3. ripshelf searches for the first few with the film or show searches in Settings → Identify, and scores what they find with its usual rules. The title, the runtime and the year still have to fit.
4. A match the model's reading supports, and the label's own reading doesn't, counts for less (its score is multiplied by 0.85). So does one only the searches for the model's titles found. Either way it is marked as coming from the AI model, and **it never confirms without review**, whatever the auto-confirm line is.

This split comes from a trial with a small model (`qwen2.5:3b`, CPU only). Asked to pick between search results, it chose 4 of 10 discs right, because it went by runtime and ignored titles (`BTTF2` became *The Terminator*). Asked only to expand the label, with ripshelf searching and scoring, it got 9 of 10 right, none wrong, in about 4 s each.

## When it's asked

Settings → Identify → Cryptic labels:
- **Never** (the default).
- **Only when I press the button in Review.** Each review card gets **Ask the AI model what the label means**. On a film disc, the titles it reads are searched straight away and the candidates are ranked again. On a TV disc, each title is a button that searches shows for it.
- **When nothing found fits the disc, and with the button.** A film disc is read when no candidate reaches the auto-confirm line. A TV disc is read when no show reaches the TV match line. It's skipped when TheDiscDB knows the disc, a cover scan names it, or the label is a [catalogue number](catalogue.md) the web search named.

The job log says what the model read, for example `qwen2.5:3b read the label as “Back to the Future Part II” (4 s)`, and the candidate says "The AI model read the label as …".

## Which model, and where

It uses the [vision model's](cover-scans.md#a-vision-model-as-a-second-reader) connection (Settings → Scanner and covers): on your network (Ollama or another OpenAI-compatible server), a cloud OpenAI-compatible service, or Anthropic's API. When the vision model is off, the connection fields still show there while label reading is on.

**Model for labels** (under Advanced) names a different model on the same connection; blank uses the vision model. Any chat model will do, and a small one is enough:
- **`qwen2.5:3b`** on Ollama reads most labels in a few seconds on a CPU (`docker compose exec ollama ollama pull qwen2.5:3b`).
- **The vision model** (`qwen3-vl:4b-instruct`) works too, a little more slowly.
- **A cloud model** such as one of Anthropic's Haiku models is quick and knows more films, but the label and running times leave your network.

**Test (reads BTTF2)** asks the model about a made-up DVD labelled `BTTF2` and checks that it answers *Back to the Future*.
