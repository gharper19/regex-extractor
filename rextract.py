import argparse, json, os
import regex as re
import pandas as pd

r'''
- Takes a pattern with named groups: (?P<date>...)(?P<amount>...)
- Reads a text file or stdin
- Writes CSV with the named groups as columns
- Prints which lines didn't match, with line numbers
- One test file with a passing test

--

What you get

1. regex101-style named groups work as written — this is your actual bug. Lines 29–30 use (?<DATE>...) and (?<MUSCLE_GROUP>...); re rejects both, regex takes them. (?P<...>) still works too, so mixed patterns are fine.

2. Variable-length lookbehind — re refuses (?<=\d+) outright. regex allows it. Useful for "match X but only after a date heading."

3. captures() — all captures of a repeated group. This is the one worth knowing for your hierarchical plan. In re, a group inside (...)+ only retains its last match:

m = re.match(r"@(?<MUSCLE_GROUP>.+)\n(?:(?<SET>\d+x\d+)\n)+", text)
m.group("SET")       # last set only — same as re
m.captures("SET")    # ['3x10', '3x8', '3x6'] — every one
m.spans("SET")       # [(12,16), (17,21), (22,26)] — offsets for each

For "find level-2 elements inside this level-1 section," that can collapse a nested loop into one pattern. Worth trying against your gym log before you build the recursive version.

4. \K — drops everything matched so far from the result, so you can anchor on a preamble without capturing it.

Two flags on your current patterns

- Use raw strings. "#(?<DATE>\s?.+)" at line 29 has \s in a normal string. Python 3.12 emits a SyntaxWarning for that and it becomes a hard error in a future version. Prefix them r"...".
- (?:\n[^#]*)+ and (?:[^@]*)+ — a * quantifier inside a + group is a catastrophic-backtracking shape. On matching input it's fine; on a near-miss it can hang. regex doesn't fix that, but it does give you atomic groups (?>...) and possessive quantifiers [^#]*+ to kill the backtracking outright. Worth switching to (?:\n[^#]*)*+ style once the pattern is otherwise correct.

Set regex101's flavor to Python regardless — it'll still accept (?<name>) since you're running the regex module, but you'll stop getting PCRE-only constructs that neither module supports.

'''

gym_log_regex = {
    # Level 1 - Dated sections.  DATE_NOTES is the optional "*..." line under the date.
    "1": r"(?m)^#(?P<TARGET>(?P<DATE>[^\n]+))\n(?:\*+[ \t]*(?P<DATE_NOTES>[^\n]*)\n?)?",

    # Level 2 - Muscle Groups.  MUSCLE_GROUP stops before the ~ / - separator so the
    # marker never ends up in the name.  MG_SKIP_FLAG captures the literal word "skip".
    "2": r"(?m)^@[ \t]*(?P<TARGET>(?P<MUSCLE_GROUP>[^~\n]*?))[ \t]*(?:[~-][ \t]*(?P<MG_SKIP_FLAG>skip)?)?[ \t]*(?:\n|$)",

    # Level 3 - Exercise Logs.  Name is required; the measurement block
    # (WEIGHT / SETS / REPS) and the trailing notes are all optional, so
    # bare entries and "- name -?" skip stubs still produce a row.
    "3": r"""(?m)^[ \t]*-[ \t]*(?P<EXC_PRIORITY_FLAG>\!)?[ \t]*"""
         r"""(?P<TARGET>(?P<EXERCISE_NAME>[^\n:?]*?))"""
         r"""(?:(?::|[ \t]+-|[ \t]*-[ \t]+)[ \t]*"""
         r"""(?:(?P<WEIGHT>[^\n-]*?)[ \t]*-[ \t]*)?"""
         r"""(?:(?P<SETS>\d+)[ \t]*of[ \t]*(?P<REPS>\d+)[ \t]*-[ \t]*)?"""
         r"""(?:(?P<EXERCISE_NOTES>[^\n?]*?)[ \t]*)?"""
         r""")?(?P<EXERCISE_SKIP_FLAG>\?)?[ \t]*(?:\n|$)"""
}

PROGRAM_NAME = "extract"
PROGRAM_DESCRIPTION = "Extract rows from text using a regex and write CSV."
PROGRAM_VERSION = "1.0"

DEFAULT_INPUT = "data/input_data/input_small.txt"
DEFAULT_OUTPUT = "data/output_data/gym_log.csv"

IGNORE_FAILED_REGEX_MATCHES = False
STATUS = {
    "ERROR": "ERROR",
    "SUCCESS": "MATCHED",
    "PENDING": "PARENT",
    "PARENT": "PARENT"
}
TARGET_GROUP_NAME = "TARGET"

# A schema describes how to type the exported columns for one kind of input.  It is
# optional: with no schema, every captured group is exported as text, in the order the
# levels captured it.  GYM_SCHEMA is the example that ships with the tool.
#   columns - fixes the column SET and ORDER.  Without it pandas takes the order from
#             whichever row dict it happens to read first.
#   text    - columns to strip whitespace from
#   flags   - presence-only markers ("!", "?", "skip") to collapse to booleans
#   numeric - columns to coerce to nullable Int64
#   date    - column to parse into a real date
GYM_SCHEMA = {
    "columns": ["DATE", "MUSCLE_GROUP", "EXERCISE_NAME", "WEIGHT", "SETS", "REPS",
                "EXC_PRIORITY_FLAG", "EXERCISE_SKIP_FLAG", "MG_SKIP_FLAG", "EXERCISE_NOTES"],
    "text":    ["DATE", "MUSCLE_GROUP", "EXERCISE_NAME", "WEIGHT", "EXERCISE_NOTES"],
    "flags":   ["EXC_PRIORITY_FLAG", "EXERCISE_SKIP_FLAG", "MG_SKIP_FLAG"],
    "numeric": ["SETS", "REPS"],
    "date":    "DATE",
}


class MatchNode:
# One regex match and its position in the hierarchy: the level it matched at, the span it
# covers, its parent, and the fields it inherited from the parent chain.  Only `groups`
# becomes columns in the final DataFrame.

    def __init__(
            self, 
            match_index:int = 0, # <index in regex match list> 
            match_level:int = 1, # <Hierarchical level>
            match_text:str = "", # <regex text matching pattern>     
            csv_row_index:int = None, # <row index in csv> - Include row index only if Row will not be flattened. If it will, then do not append it to the output csv
            status:str = "NULL", # <Matched, Failed, Parent(to be unfolded)>
            parent=None, # <Higher Level Parent id (level, match_Index)> 
            span=(0, 0), # <start and end indicies in the text>
            groups=None
        ):
            self.match_index = match_index
            self.match_level = match_level 
            self.match_text = match_text
            self.csv_row_index = csv_row_index
            self.status = status
            self.parent = parent
            self.groups = {} if groups is None else dict(groups)

            self.span = span
            self.ancestor_dict = {}
            self.inherited_fields_dict = {}
            self.parent_text = ""
            self.parent_level = 1
            self.parent_index = 0
            
            # Inherit parent details
            if self.parent != None:
                self.parent_text = parent.match_text
                self.parent_level = parent.match_level
                self.parent_index = parent.match_index

                # Add parent to ancestors list
                self.ancestor_dict = dict(parent.ancestor_dict)
                self.ancestor_dict[self.parent_level]=(self.parent_index, self.parent_text) 

                # Build this object's own inherited dict from the parent's (never mutate the parent)
                self.inherited_fields_dict = {**parent.inherited_fields_dict, **parent.groups}
                    

    def __repr__(self):
        return self.__str__()

    def __str__(self):
        if self.parent == None:
            parent_level = None
            parent_index = None
        else:
            parent_level = self.parent.match_level
            parent_index = self.parent.match_index
        
        return f"lvl/idx: {(self.match_level, self.match_index)} | status: {self.status} | span: {self.span} | parent: {(parent_level, parent_index)} | text: {self.match_text}"

def read_file(filepath):
    with open(filepath) as f:
        return f.read()

def test_regex_match(text, pattern, ignore_case=True):
    regex = re.compile(pattern, re.IGNORECASE) if ignore_case else re.compile(pattern)
    return list(regex.finditer(text))

def parse_final_level(text:str, pattern:str, current_level:int, regex_pattern_dict:dict, parent:MatchNode):
# outputs list of csv objects ready to be converted into rows, extracted from text using regex provided
    nodes = []
    
    # Test regex
    matches = test_regex_match(text, pattern)
    if len(matches) == 0: 
    # If empty, create an error row in the CSV
        nodes.append(MatchNode(
                        match_level=current_level,
                        match_text="No matches found when parsing final level.", 
                        status=STATUS["ERROR"], 
                        parent=parent,
                        span=None,
                        groups = {
                            "error": "No matches found when parsing final level."
                        }))
        return nodes

    # Iterate through matches
    prev_end = 0 # end of the previous match, so gaps between matches are not dropped
    for idx, match in enumerate(matches):
        # Check for unmatched text between the previous match and this one (or before the
        # first match) and create a dummy row to hold it if it exists
        if match.span()[0] > prev_end:
            unmatched_text = text[prev_end:match.span()[0]]
            if unmatched_text.strip() != "":
                nodes.append(MatchNode(
                        match_level=current_level,
                        match_text=unmatched_text, 
                        status=STATUS["ERROR"], 
                        parent=parent,
                        span=(prev_end, match.span()[0]),
                        groups = {
                                "unmatched_text": unmatched_text,
                                "error": f"Text did not match regex pattern r'{pattern}'"
                            }))
        prev_end = match.span()[1]

        # Extract Groups and add them as fields to the final row
        groups = match.groupdict()
        if groups == None or groups == {}: 
            groups = {}
            match_text = match.group(0)
        else:
            if TARGET_GROUP_NAME in groups.keys():
                # Set TARGET group as match text and then remove it from groups
                match_text = groups[TARGET_GROUP_NAME]
                del groups[TARGET_GROUP_NAME]
            else:    
                match_text=match.groups()[0]

        # Skip stub entries - a match with no TARGET text ("-?", "- ?", a bare "-") carries
        # no data of its own, so it gets no row
        if str(match_text).strip() == "":
            continue

        # Merge in everything inherited from the parent chain, including the parent's own
        # groups; the row's own fields win on any key collision.
        if parent is not None:
            groups = {**parent.inherited_fields_dict, **parent.groups, **groups}
        
        # Append Match to output
        nodes.append(MatchNode(
            match_index=idx,
            match_level=current_level,
            match_text=match_text, 
            status=STATUS["SUCCESS"], 
            parent=parent, 
            span=match.span(),
            groups=groups))

    # Check for unmatched text after the last match and create a dummy row to hold it
    if prev_end < len(text):
            unmatched_text = text[prev_end:]
            if unmatched_text.strip() != "":
                nodes.append(MatchNode(match_level=current_level,
                        match_text=unmatched_text, 
                        status=STATUS["ERROR"], 
                        parent=parent,
                        span=(prev_end, len(text)),
                        groups = {
                            "unmatched_text": unmatched_text,
                            "error": f"Text did not match regex pattern r'{pattern}'"
                        }))
                
    return nodes

def parse_heirarchy(text:str, current_level:int, regex_pattern_dict:dict, parent=None, pattern:str=None): # Here recent parent is the CsvRow Object that contains all the data we need to populate the children. If we have to go a level deeper, then w
    '''Might need to include child text as a group in the data, alternative is to take the span between headings and pass that into the parse hierarchy, one parent after the other.'''
    # outputs list of csv Rows ready to be, irst validate that the starting_level value exists in the regex_pattern_dict. If not, Return a list containing a single Generic Error CSVRow Object describing the error. We are checking a generic level, We check our first match:
    nodes = []
    # if parent == None: parent = MatchNode() # Create Null Parent if none was provided
    if pattern == None: pattern = regex_pattern_dict[str(current_level)] # set pattern according to regex_pattern_dict if none was provided

    # Check is current level final level
    if str(current_level+1) not in regex_pattern_dict:
        return parse_final_level(text=text, pattern=pattern, current_level=current_level, regex_pattern_dict=regex_pattern_dict, parent=parent)
        
    # Test regex and parse as final level if no matches are found for cihld
    matches = test_regex_match(text, pattern)
    if len(matches) == 0: 
        # If empty, create an error row in the CSV and parse this level as the final level
        nodes.append(MatchNode(match_level=current_level,
                            match_text=f"No matches found when parsing level {current_level}.", 
                            status=STATUS["ERROR"], 
                            parent=parent,
                            span=(-1,-1),
                            groups = {
                                "error": f"No matches found when parsing level {current_level}."
                            }))
        nodes.extend(parse_final_level(text=text, pattern=pattern, current_level=current_level, regex_pattern_dict=regex_pattern_dict, parent=parent))
        return nodes
    
    # Iterate through matches
    for idx, match in enumerate(matches):
        if idx == 0: 
        # for first match in level, Check for unmatched rows before target text and create rows for them if they exists
            if match.span()[0] > 0 and text[0:match.span()[0]].strip() != "":
                unmatched_text = text[0:match.span()[0]]
                nodes.append(MatchNode(
                    match_level=current_level,
                    match_text=unmatched_text, 
                    status=STATUS["ERROR"], 
                    parent=parent, 
                    span=(0,match.span()[0]),
                    groups = {
                        "unmatched_text": unmatched_text,
                        "error": f"Text did not match regex pattern r'{pattern}'"
                    }))

        # Extract groups and add fields to inherited_dict
        groups = match.groupdict()
        if groups == None or groups == {}: 
            groups = {}
            match_text = match.group(0)
        else:
            if TARGET_GROUP_NAME in groups.keys():
                # Set TARGET group as match text and then remove it from groups
                match_text = groups[TARGET_GROUP_NAME]
                del groups[TARGET_GROUP_NAME]
            else:    
                match_text=match.groups()[0]
        
        # Create object for current match item to serve as parent for parsing children
        current_object = MatchNode(
            match_index=idx,
            match_level=current_level,
            match_text=match_text, 
            status=STATUS["PENDING"], 
            parent=parent, 
            span=match.span(),
            groups=groups)

        # Extract child text and parse children at next heirarchical level
        if idx == len(matches)-1:
            child_text = text[match.span()[1]:]
        else:
            child_text = text[match.span()[1]:matches[idx+1].span()[0]]

        nodes.extend(parse_heirarchy(
            current_level=current_level+1,
            text=child_text,
            pattern=regex_pattern_dict[str(current_level+1)],
            regex_pattern_dict=regex_pattern_dict,
            parent=current_object,
        ))

    return nodes

def to_dataframe(nodes, schema=None):
# Flatten the matched nodes into a DataFrame.  Only each node's `groups` dict becomes
# columns - the parse bookkeeping (level, span, parent) stays on the node.
# With no schema every captured group is exported as text; a schema adds column order
# and dtypes (see GYM_SCHEMA).
    matched = [node.groups for node in nodes if node.status == STATUS["SUCCESS"]]
    df = pd.DataFrame(matched)
    if df.empty: return df

    if schema is None:
        # Generic path: keep every group any row captured, ordered by first appearance
        # so the header is stable for a given input
        columns = []
        for groups in matched:
            columns.extend(key for key in groups if key not in columns)
        df = df.reindex(columns=columns)
        for column in df.columns:
            if pd.api.types.is_string_dtype(df[column]):
                df[column] = df[column].str.strip()
        return df

    # Fix the column set and order, dropping any group the export does not want
    # and adding any that never appeared in the data
    df = df.reindex(columns=schema["columns"])

    # Strip the whitespace deliberately left in during parsing
    if schema.get("text"):
        df[schema["text"]] = df[schema["text"]].apply(lambda column: column.str.strip())

    # Flags are presence-only markers ("!", "?", "skip"), so collapse them to booleans
    if schema.get("flags"):
        df[schema["flags"]] = df[schema["flags"]].notna()

    # Int64 (capital I) is the nullable integer type - a plain int cannot hold the
    # missing values left by partial entries, and would force the column to float
    if schema.get("numeric"):
        df[schema["numeric"]] = df[schema["numeric"]].apply(pd.to_numeric, errors="coerce").astype("Int64")

    # "Tues, Oct 20, 2026" -> a real date for sorting and grouping.  The weekday is
    # pulled off rather than parsed, since abbreviations like "Tues" are not what %a
    # expects; the month/day/year is extracted from anywhere in the string instead.
    # Anything unparseable becomes NaT rather than raising - main() reports the count.
    if schema.get("date"):
        month_day_year = df[schema["date"]].str.extract(r"([A-Za-z]{3,}\s+\d{1,2},\s*\d{4})")[0]
        df[schema["date"]] = pd.to_datetime(month_day_year, format="mixed", errors="coerce").dt.date

    return df


def to_error_dataframe(nodes):
# The text the regex could not place, kept separate so it never pollutes the export
    return pd.DataFrame([
        {"level": node.match_level, "span": str(node.span),
         "unmatched_text": node.groups.get("unmatched_text"),
         "error": node.groups.get("error")}
        for node in nodes if node.status == STATUS["ERROR"]
    ])


def show_trail(rows):
# Print any hierarchy as an indented trail, driven by each node's ancestor_dict rather
# than by named fields, so it works for whatever levels the supplied patterns define.
    shown = {}          # level -> the heading text currently printed for that level
    error_rows = []

    for row in rows:
        if row.status == STATUS["ERROR"]:
            error_rows.append(row)
            continue

        # Re-print an ancestor heading whenever it changes.  A change at one level makes
        # every deeper heading stale, so those are dropped and re-print below it.
        for level in sorted(row.ancestor_dict):
            index, text = row.ancestor_dict[level]
            text = str(text).strip()
            if shown.get(level) != text:
                print(f"{'  ' * (level - 1)}[{level}] {text}")
                shown[level] = text
                for deeper in [key for key in shown if key > level]:
                    shown.pop(deeper)

        # A node's own fields are whatever it captured and did not inherit.  The label is
        # already match_text, so a field repeating it is not printed twice.
        label = str(row.match_text).strip()
        own = {
            name: str(value).strip()
            for name, value in row.groups.items()
            if name not in row.inherited_fields_dict
            and value is not None and str(value).strip() not in ("", label)
        }
        fields = "  ".join(f"{name}={value}" for name, value in own.items())
        print(f"{'  ' * row.match_level}{label}" + (f"   {fields}" if fields else ""))

    print(f"\n{'-' * 60}")
    print(f"{len(rows) - len(error_rows)} rows parsed, {len(error_rows)} unmatched")
    for row in error_rows:
        text = row.groups.get("unmatched_text") or row.groups.get("error") or ""
        print(f"  UNMATCHED level {row.match_level} at {row.span}: {str(text)[:70]!r}")


def show_gym_rows(rows):
# Print the parsed rows as an indented trail (DATE > MUSCLE_GROUP > exercise) so the
# inherited fields on each row can be read against the hierarchy they came from.
    def field(groups, name):
        value = groups.get(name)
        return str(value).strip() if value is not None else ""

    current_date = None
    current_group = None
    error_rows = []

    for row in rows:
        groups = row.groups

        if row.status == STATUS["ERROR"]:
            error_rows.append(row)
            continue

        date = field(groups, "DATE")
        if date != current_date:
            notes = field(groups, "DATE_NOTES")
            print(f"\n# {date}" + (f"   ({notes})" if notes else ""))
            current_date = date
            current_group = None

        muscle_group = field(groups, "MUSCLE_GROUP")
        if muscle_group != current_group:
            skipped = "   ~ SKIPPED" if field(groups, "MG_SKIP_FLAG") else ""
            print(f"  @ {muscle_group}{skipped}")
            current_group = muscle_group

        # Build the measurement + flag columns for this exercise
        sets, reps = field(groups, "SETS"), field(groups, "REPS")
        measurement = f"{sets} of {reps}" if sets and reps else ""
        flags = []
        if field(groups, "EXC_PRIORITY_FLAG"): flags.append("PRIORITY")
        if field(groups, "EXERCISE_SKIP_FLAG"): flags.append("SKIPPED")

        print(f"      {field(groups, 'EXERCISE_NAME'):<28} "
              f"{field(groups, 'WEIGHT')[:24]:<26} "
              f"{measurement:<9} "
              f"{','.join(flags):<17} "
              f"{field(groups, 'EXERCISE_NOTES')[:40]}")

    # Anything the regex could not place is reported at the end rather than inline,
    # so it does not break up the trail
    print(f"\n{'-'*60}")
    print(f"{len(rows) - len(error_rows)} rows parsed, {len(error_rows)} unmatched")
    for row in error_rows:
        text = field(row.groups, "unmatched_text") or field(row.groups, "error")
        print(f"  UNMATCHED level {row.match_level} at {row.span}: {text[:70]!r}")


def show_matches():
# Show spans for each match highlighted with Parent data color coded according to parent level. All parent data of the same level should be highlighted the same color. If you run out of colors just start from the begging with the shading multiplied by some ratio within a span.Maybe lookup the name of the color for the legend. 
    pass

CONFIG_KEYS = {"patterns", "schema", "input", "output"}


def validate_pattern_dict(patterns, source):
# Levels must run 1..N with no gaps, because parse_heirarchy finds the leaf level by
# looking for the next level up - a gap would end the walk early.
    if not patterns:
        raise ValueError(f"{source}: no patterns found")

    expected = [str(level) for level in range(1, len(patterns) + 1)]
    missing = [level for level in expected if level not in patterns]
    if missing:
        raise ValueError(
            f"{source}: levels must run 1..{len(patterns)} with no gaps, missing {missing} "
            f"(found {sorted(patterns)})")

    for level in expected:
        try:
            re.compile(patterns[level])
        except re.error as error:
            raise ValueError(f"{source}: level {level} pattern does not compile: {error}")

    return {level: patterns[level] for level in expected} # rebuilt in level order


def read_json(path):
# json.JSONDecodeError does not mention the file, which is unhelpful with several of them
    try:
        return json.loads(read_file(path))
    except json.JSONDecodeError as error:
        raise ValueError(f"{path}: invalid JSON: {error}")


def load_pattern_dict(path):
# A .json file is read as {level: pattern}; any other file is read as one pattern per
# line, level 1 first.  The line format avoids having to double every backslash.
    text = read_file(path)
    if path.lower().endswith(".json"):
        patterns = {str(level): pattern for level, pattern in read_json(path).items()}
    else:
        lines = [line for line in text.split("\n") if line.strip()]
        patterns = {str(level): line for level, line in enumerate(lines, 1)}
    return validate_pattern_dict(patterns, path)


def load_config(path):
# JSON config holding any of: patterns, schema, input, output.  Every key is optional,
# and anything passed on the command line wins over what is in here.
    config = read_json(path)
    unknown = set(config) - CONFIG_KEYS
    if unknown:
        raise ValueError(f"{path}: unknown config keys {sorted(unknown)}, expected {sorted(CONFIG_KEYS)}")
    if "patterns" in config:
        config["patterns"] = validate_pattern_dict(
            {str(level): pattern for level, pattern in config["patterns"].items()}, path)
    return config


def resolve_settings(args):
# Work out the patterns, schema and paths from the command line, then the config file,
# then the built-in defaults, in that order of precedence.
    config = load_config(args.config) if args.config else {}

    patterns = None
    if args.regex:
        patterns = validate_pattern_dict(
            {str(level): pattern for level, pattern in enumerate(args.regex, 1)}, "--regex")
    elif args.patterns:
        patterns = load_pattern_dict(args.patterns)
    elif "patterns" in config:
        patterns = config["patterns"]

    schema = config.get("schema")
    using_default_patterns = patterns is None
    if using_default_patterns:
        # Nothing supplied, so fall back to the bundled example - and only then is the
        # gym schema the right way to type the columns
        patterns = gym_log_regex
        if schema is None:
            schema = GYM_SCHEMA

    return {
        "patterns": patterns,
        "schema": schema,
        "input": args.input or config.get("input") or DEFAULT_INPUT,
        "output": args.output or config.get("output") or DEFAULT_OUTPUT,
        "using_default_patterns": using_default_patterns,
    }


def parse_cli_arguments():
# Minimal text interface: everything has a working default, so a bare run still works
    parser = argparse.ArgumentParser(prog=PROGRAM_NAME, description=PROGRAM_DESCRIPTION)
    parser.add_argument("input", nargs="?",
                        help=f"input text file (default: {DEFAULT_INPUT})")
    parser.add_argument("-o", "--output",
                        help=f"output CSV path (default: {DEFAULT_OUTPUT})")
    parser.add_argument("-p", "--patterns", metavar="FILE",
                        help="regex hierarchy: a .json file of {level: pattern}, or any "
                             "other file with one pattern per line, level 1 first")
    parser.add_argument("-r", "--regex", action="append", metavar="PATTERN",
                        help="one level's pattern inline, repeat once per level in order")
    parser.add_argument("-c", "--config", metavar="FILE",
                        help="JSON config holding any of: patterns, schema, input, output")
    parser.add_argument("-q", "--quiet", action="store_true",
                        help="skip the parse trail, print the summary only")
    parser.add_argument("--version", action="version",
                        version=f"{PROGRAM_NAME} {PROGRAM_VERSION}")
    return parser.parse_args()


def main():
    args = parse_cli_arguments()

    try:
        settings = resolve_settings(args)
    except (ValueError, json.JSONDecodeError, OSError) as error:
        print(f"{PROGRAM_NAME}: {error}")
        return 1

    if not os.path.exists(settings["input"]):
        print(f"{PROGRAM_NAME}: input file not found: {settings['input']}")
        return 1

    patterns = settings["patterns"]
    text = read_file(settings["input"])
    rows = parse_heirarchy(text, pattern=patterns["1"], current_level=1, regex_pattern_dict=patterns)

    if not args.quiet:
        # The bundled example gets its tailored view; anything else gets the generic
        # trail, which reads the hierarchy off the nodes instead of named fields
        show_gym_rows(rows) if settings["using_default_patterns"] else show_trail(rows)

    # Convert the matched nodes to a DataFrame and write it out
    df = to_dataframe(rows, settings["schema"])
    if df.empty:
        print(f"{PROGRAM_NAME}: nothing matched in {settings['input']}, no CSV written")
        return 1

    os.makedirs(os.path.dirname(settings["output"]) or ".", exist_ok=True)
    df.to_csv(settings["output"], index=False) # index=False or pandas adds a counter column
    print(f"\nWrote {len(df)} rows x {len(df.columns)} columns to {settings['output']}")

    # Report anything that could not be placed instead of letting it disappear
    errors = to_error_dataframe(rows)
    if not errors.empty:
        print(f"{len(errors)} unmatched fragments were held back from the export")

    date_column = (settings["schema"] or {}).get("date")
    if date_column and date_column in df.columns:
        unparsed_dates = int(df[date_column].isna().sum())
        if unparsed_dates:
            print(f"{unparsed_dates} rows have an unreadable {date_column}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())