# regex-extractor

Extracts data fields from a hierarchical text file using one regex per level, then flattens
the tree into a CSV for analysis.

You supply a **regex hierarchy** — one pattern per nesting level. Each row in the output
inherits the fields captured by its ancestors, so the flat CSV keeps the context the
indentation used to carry.

A gym log ships as the bundled example, used when you don't supply your own patterns:

```
# Tues, Oct 20, 2026          ->  DATE
@ Chest                       ->  MUSCLE_GROUP
- Bench Press: 40 LB D - 4 of 12 - ?   ->  EXERCISE_NAME, WEIGHT, SETS, REPS, EXERCISE_SKIP_FLAG
```

## Install

```sh
python -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Built and tested on Python 3.12. Two dependencies:

- **`regex`** — not the stdlib `re`. The patterns use constructs `re` rejects.
- **`pandas`** — for the DataFrame/CSV export.

## Usage

```sh
.venv/bin/python rextract.py                                  # bundled gym example
.venv/bin/python rextract.py log.txt -p my.patterns           # your own hierarchy
.venv/bin/python rextract.py log.txt -o out/rows.csv
.venv/bin/python rextract.py -c my.config.json                # everything from a config
.venv/bin/python rextract.py log.txt -r 'pat1' -r 'pat2'      # patterns inline
```

| Flag | Meaning |
|---|---|
| `input` | Input text file. Default `data/input_data/input_small.txt` |
| `-o`, `--output` | Output CSV. Default `data/output_data/gym_log.csv` |
| `-p`, `--patterns` | Regex hierarchy file (see below) |
| `-r`, `--regex` | One level's pattern inline; repeat once per level, in order |
| `-c`, `--config` | JSON config bundling patterns, schema, input, output |
| `-q`, `--quiet` | Skip the parse trail, print the summary only |
| `--version` | Print version and exit |

Exits `1` on a missing input, an invalid hierarchy, or an empty parse; `0` otherwise.

## Supplying your own hierarchy

Three ways, in order of precedence: **`-r` > `-p` > `--config` > the bundled gym example.**

A complete worked example of a different hierarchy — host / service / log line — lives in
[`examples/`](examples/), in all three formats.

### A patterns file (`-p`)

Any file that isn't `.json` is read as **one pattern per line, level 1 first**. Blank lines
are ignored. Backslashes are literal, which is why this is usually the nicer format:

```
(?m)^==[ \t]*(?P<TARGET>(?P<HOST>[^\n]+?))[ \t]*(?:\n|$)
(?m)^--[ \t]*(?P<TARGET>(?P<SERVICE>[^\n]+?))[ \t]*(?:\n|$)
(?m)^\*[ \t]*(?P<TARGET>(?P<TIMESTAMP>[0-9-]+ [0-9:]+))[ \t]+(?P<SEVERITY>[A-Z]+)[ \t]+(?P<MESSAGE>[^\n]*?)[ \t]*(?:\n|$)
```

A `.json` file is read as `{level: pattern}` instead. JSON requires every backslash
doubled (`\\t`, `\\n`), so prefer the line format unless you're generating the file.

### A config file (`-c`)

JSON, every key optional. Anything on the command line overrides it:

```json
{
  "input": "logs/hosts.txt",
  "output": "out/hosts.csv",
  "patterns": { "1": "...", "2": "...", "3": "..." },
  "schema": {
    "columns": ["TIMESTAMP", "SEVERITY", "HOST", "SERVICE", "MESSAGE"],
    "text": ["HOST", "SERVICE", "MESSAGE"]
  }
}
```

### Validation

Levels must run `1..N` with no gaps — the parser finds the leaf level by looking for the
next level up, so a gap would end the walk early. Each pattern must compile. Failures name
the file and the level:

```
extract: my.patterns: levels must run 1..2 with no gaps, missing ['2'] (found ['1', '3'])
extract: my.patterns: level 2 pattern does not compile: unterminated character set at position 20
```

## How it works

### One pattern per level

Level 1 matches the outermost sections; each deeper level is matched against the text
between its parent's matches. The last level in the dict is automatically the leaf — the
parser looks for `level + 1` to decide, which is why the levels have to be contiguous.

Anchor header patterns with `(?m)^`. Unanchored, a stray delimiter inside a note reads as a
new section and fabricates a bogus parent.

### `TARGET` is the label for a match

Each pattern should define a `TARGET` group. Its text becomes the node's `match_text` and is
dropped from the exported fields (it would duplicate the named group nested inside it).
With no `TARGET`, the first capture group is used.

A match whose `TARGET` is empty — a bare `-`, a lone `-?` — is treated as a stub and
produces no row.

### Inheritance

Each node builds its own copy of its ancestors' fields:

```python
self.inherited_fields_dict = {**parent.inherited_fields_dict, **parent.groups}
```

Copied, not shared. A single dict passed by reference down a depth-first walk accumulates
across sibling branches, which silently gives rows the previous branch's values.

### Unmatched text is kept

Text the pattern can't place — before the first match, between two matches, or after the
last — becomes an `ERROR` node holding the raw text and its span. `to_error_dataframe()`
collects those separately so they never reach the export but also never disappear.

### The parse trail

Without `-q` the hierarchy is printed so you can read each row against where it came from.
Custom patterns get the generic view, built from each node's `ancestor_dict`:

```
[1] prod-web-01
  [2] nginx
      2026-10-01 14:02   SEVERITY=ERROR  MESSAGE=502 upstream timeout
      2026-10-01 14:05   SEVERITY=WARN  MESSAGE=slow response 3.2s
  [2] postgres
      2026-10-01 14:10   SEVERITY=ERROR  MESSAGE=deadlock detected

------------------------------------------------------------
5 rows parsed, 0 unmatched
```

The bundled gym example has a tailored view instead (`show_gym_rows`), with named columns
and `~ SKIPPED` markers.

## Output columns

**With no schema**, every captured group becomes a text column, ordered by first
appearance, whitespace stripped. Nothing is typed, because nothing can be assumed:

```
HOST,SERVICE,TIMESTAMP,SEVERITY,MESSAGE
prod-web-01,nginx,2026-10-01 14:02,ERROR,502 upstream timeout
```

**With a schema**, you get column order and dtypes. The keys:

| Key | Effect |
|---|---|
| `columns` | Fixes the column set and order. Groups not listed are dropped; names listed but never captured come through empty |
| `text` | Columns to strip whitespace from |
| `flags` | Presence-only markers (`!`, `?`, `skip`) collapsed to booleans |
| `numeric` | Coerced to nullable `Int64` |
| `date` | Parsed into a real date |

`GYM_SCHEMA` in `rextract.py` is the worked example. It produces:

| Column | Type | Notes |
|---|---|---|
| `DATE` | date | Parsed to ISO. Weekday discarded (`Tues` isn't a standard abbreviation) |
| `MUSCLE_GROUP` | str | |
| `EXERCISE_NAME` | str | |
| `WEIGHT` | str | Free text — `40 LB D`, `12(max) resistance cable`, `Body weight` |
| `SETS`, `REPS` | Int64 | Nullable — bodyweight entries have no count |
| `EXC_PRIORITY_FLAG` | bool | The `!` marker |
| `EXERCISE_SKIP_FLAG` | bool | The `?` marker |
| `MG_SKIP_FLAG` | bool | `skip` after a `~` on the muscle-group line |
| `EXERCISE_NOTES` | str | |

`numeric` uses pandas' nullable `Int64`, not `int64` — a plain int can't hold missing
values and would silently turn the column into floats (`4.0`).

## Gym log format

```
# Wed, Oct 28, 2026                  date section
**NOTE: anything here becomes DATE_NOTES

@ Chest                              muscle group
@ Bicep ~ skip                       muscle group, skipped
- Bench Press: 40 LB D - 4 of 12 - did.    exercise with notes
- Bench Press: 40 LB D - 4 of 12 - ?       exercise, skipped
- ! Pull-ups:                              priority, no measurement
- triceps extensions                       name only
```

`WEIGHT`, `SETS`/`REPS`, and the notes are each independently optional, so partial entries
still produce a row rather than an error.

## Known gaps

- A parent whose only content is a stub (`@Hams` / `-?`) produces no rows at all, so it
  vanishes from the output. Representing it needs a row emitted at the parent's level.
- `show_matches()` is a stub — the span-highlighting view described in `DESIGN.md`.
- Should higher-level patterns match only the heading, with bodies taken from the offsets
  between consecutive headings? Section-capturing patterns need a nested quantifier like
  `(?:\n[^#]*)+`, which is a catastrophic-backtracking shape that can hang on a near-miss —
  bad in a UI that re-matches on every keystroke. Header-only patterns are trivial
  one-liners and the body comes free from the offsets.

Design notes and the original implementation spec are in `DESIGN.md`.
