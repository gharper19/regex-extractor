# examples

A non-gym hierarchy, to show the tool isn't tied to the bundled example. Same three-level
shape — host contains services, services contain log lines — but different delimiters and
different field names.

Run each from the repo root:

```sh
# patterns as one-per-line (backslashes literal)
.venv/bin/python rextract.py examples/hosts.txt -p examples/hosts.patterns

# same patterns as JSON (every backslash doubled)
.venv/bin/python rextract.py examples/hosts.txt -p examples/hosts.json

# patterns + schema + input + output all from one config
.venv/bin/python rextract.py -c examples/hosts.config.json
```

| File | |
|---|---|
| `hosts.txt` | Sample input |
| `hosts.patterns` | Three patterns, one per line, level 1 first |
| `hosts.json` | The same three as `{level: pattern}` |
| `hosts.config.json` | Config bundling patterns, a schema, and both paths |

The `-p` runs have no schema, so every captured group is exported as text in the order the
levels captured it:

```
HOST,SERVICE,TIMESTAMP,SEVERITY,MESSAGE
prod-web-01,nginx,2026-10-01 14:02,ERROR,502 upstream timeout
```

The config run adds a schema that reorders the columns, putting the leaf's own fields first:

```
TIMESTAMP,SEVERITY,HOST,SERVICE,MESSAGE
2026-10-01 14:02,ERROR,prod-web-01,nginx,502 upstream timeout
```

Both write to `data/output_data/`, which is gitignored.
