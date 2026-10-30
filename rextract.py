import argparse, os
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
# def parse_cli_arguments():
#     p = argparse.ArgumentParser(
#             prog=PROGRAM_NAME,
#             description=PROGRAM_DESCRIPTION,
#             version=PROGRAM_VERSION
#         )
        
#     p.add_argument("input",
#                     help="text file to read (use - for stdin)")
#     p.add_argument("-p", "--pattern", required=True,
#                     help="regex with named groups")
#     p.add_argument("-o", "--output",
#                     help="CSV file to write (default: stdout)")
#     p.add_argument("-m", "--multilevel", required=False,
#                         help="filepath to csv with multilevel hierarchy details for multilevel extraction")
#     p.add_argument("--ignore-case", action="store_true",
#                     help="case-insensitive matching")
#     p.add_argument("-f", action="store_true",
#                    help="fail if any lines don't match")
    
#     args = p.parse_args()

#     # Check multilevel file
#     if args.multilevel is not None:
#         pass

#     # print(args.input, args.pattern, args.output, args.ignore_case)
#     return (args.input, args.pattern, args.output, args.ignore_case)

# def search_regex(text, pattern, ignore_case=True):
#     text = text.strip()
#     regex = re.compile(pattern, re.IGNORECASE) if ignore_case else re.compile(pattern)
        
#     match = regex.search(text)
#     if not match: return match

#     match_start = match.start() # starting index in text where match was found
#     match_end = match.end()
#     match_span = match.span() # span of indicies in text where match was found

#     groups = match.groupdict() # dictionary of groups found (Label: Text)
#     #group1 = match.group(1)
#     #groupByName = match.group("group-name")

#     return match

# def extract_groups(text, pattern, ignore_case=True):
#     '''Returns list of group dicts retrieved from each match found. 
#     Dicts will have 'text' attribute included if there is text collected that doesnt match the pattern exactly
#     One dict per distinct match found. Returns None if no match was found
#     '''
#     text = text.strip()
#     regex = re.compile(pattern, re.IGNORECASE) if ignore_case else re.compile(pattern)
        
#     matches = regex.finditer(text)
#     if not matches: return None

#     group_dicts = []
#     for match in matches:
#         if not match: return None

#         match_start = match.start() # starting index in text where match was found
#         match_end = match.end()
#         match_span = match.span() # span of indicies in text where match was found

#         groups = match.groupdict() # dictionary of groups found (Label: Text)
#         #group1 = match.group(1)
#         #groupByName = match.group("group-name")

#         match_text = text[match_start:match_end]
#         groups["text"] = match_text

#         # Remove match from text
#         text = (text[:match_start] + text[match_end:]).strip()

#         group_dicts.append(groups)

#         print(f"GROUPS: {match_text}\nTEXT: {text[:10]}")
    
#     # Return list of dicts and remaining text
#     return group_dicts, text

gym_log_regex = {
    "1": r"#(?P<TARGET>(?P<DATE>\s?.+))\n(\*(?P<DATE_NOTES>)(.+\n-.+\n))?", # Level 1 - Dated sections
    "2": r"@\s*(?P<TARGET>(?P<MUSCLE_GROUP>.+))\s?([~-]\s?(?P<MG_SKIP_FLAG>skip|SKIP|Skip))?\n?", # Level 2 - Muscle Groups
    "3": r"-\s*(?:(?P<EXC_PRIORITY_FLAG>\!)?)\s*(?:(?P<TARGET>(?P<EXERCISE_NAME>.+))\s?\s?\s?[:-])\s?\s?\s?((?:(?P<WEIGHT>\d\d[^-\n]+)-\s*(?P<SETS>\d+)\s*(of|Of|OF)\s*(?P<REPS>\d+)\s?\s?\s?-\s?\s?\s?)(?:(?P<EXERCISE_SKIP_FLAG>\?)|(?P<EXERCISE_NOTES>[^?\n]+)))\s*\n" # Exercise Logs
}

PROGRAM_NAME = "extract"
PROGRAM_DESCRIPTION = "Extract rows from text using a regex and write CSV."
PROGRAM_VERSION = "1.0"

IGNORE_FAILED_REGEX_MATCHES = False
STATUS = {
    "ERROR": "ERROR",
    "SUCCESS": "MATCHED",
    "PENDING": "PARENT",
    "PARENT": "PARENT"
}
TARGET_GROUP_NAME = "TARGET"


class CSVObject:
# Each object only gets its groups included in the final DataFrame
    PRINT_LIMIT = 15

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
                    

    def __str__(self):
        if self.parent == None:
            parent_level = None
            parent_index = None
        else:
            parent_level = self.parent.match_level
            parent_index = self.parent.match_index
        
        return f"lvl/idx: {(self.match_level, self.match_index)} | status: {self.status} | span: {self.span} | parent: {(parent_level, parent_index)} | text: {self.match_text[:len(self.match_text)%self.PRINT_LIMIT]}"

def read_file(filepath):
    with open(filepath) as f:
        return f.read()

def test_regex_match(text, pattern, ignore_case=True):
    text = text.strip()
    regex = re.compile(pattern, re.IGNORECASE) if ignore_case else re.compile(pattern)
    return list(regex.finditer(text))

def parse_final_level(text:str, pattern:str, current_level:int, regex_pattern_dict:dict, parent:CSVObject):
# outputs list of csv objects ready to be converted into rows, extracted from text using regex provided
    csv_output = []
    
    # Test regex
    matches = test_regex_match(text, pattern)
    if len(matches) == 0: 
    # If empty, create an error row in the CSV
        csv_output.append(CSVObject(
                        match_level=current_level,
                        match_text="No matches found when parsing final level.", 
                        status=STATUS["ERROR"], 
                        parent=parent,
                        span=None,
                        groups = {
                            "error": "No matches found when parsing final level."
                        }))
        return csv_output

    # Iterate through matches
    for idx, match in enumerate(matches):
        if idx == 0: 
            if match.span()[0] > 0:
            # Check for unmatched rows before target text and create rows for them if they exists
                unmatched_text = text[0:match.span()[0]]
                csv_output.append(CSVObject(
                        match_level=current_level,
                        match_text=unmatched_text, 
                        status=STATUS["ERROR"], 
                        parent=parent,
                        span=(0,match.span()[0]),
                        groups = {
                                "unmatched_text": unmatched_text,
                                "error": f"Text did not match regex pattern r'{pattern}'"
                            }))

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
        groups.update(parent.inherited_fields_dict)
        
        # Append Match to output
        csv_output.append(CSVObject(
            match_index=idx,
            match_level=current_level,
            match_text=match_text, 
            status=STATUS["SUCCESS"], 
            parent=parent, 
            span=match.span,
            groups=groups))

        final_span = match.span()[1]

    # Check for unmatched rows before target text and create rows for them if they exists
    if final_span < len(text)-1:
            unmatched_text = text[final_span:len(text)-1]
            csv_output.append(CSVObject(match_level=current_level,
                        match_text=unmatched_text, 
                        status=STATUS["ERROR"], 
                        parent=parent,
                        span=(0,match.span()[0]),
                        groups = {
                            "unmatched_text": unmatched_text,
                            "error": f"Text did not match regex pattern r'{pattern}'"
                        }))
                
    return csv_output

def parse_heirarchy(text:str, current_level:int, regex_pattern_dict:dict, parent=None, pattern:str=None): # Here recent parent is the CsvRow Object that contains all the data we need to populate the children. If we have to go a level deeper, then w
    '''Might need to include child text as a group in the data, alternative is to take the span between headings and pass that into the parse hierarchy, one parent after the other.'''
    # outputs list of csv Rows ready to be, irst validate that the starting_level value exists in the regex_pattern_dict. If not, Return a list containing a single Generic Error CSVRow Object describing the error. We are checking a generic level, We check our first match:
    csv_output = []
    # if parent == None: parent = CSVObject() # Create Null Parent if none was provided
    if pattern == None: pattern = regex_pattern_dict[str(current_level)] # set pattern according to regex_pattern_dict if none was provided

    # Check is current level final level
    if str(current_level+1) not in regex_pattern_dict:
        return [parse_final_level(text=text, pattern=pattern, current_level=current_level, regex_pattern_dict=regex_pattern_dict, parent=parent)]
        
    # Test regex and parse as final level if no matches are found for cihld
    matches = test_regex_match(text, pattern)
    if len(matches) == 0: 
        # If empty, create an error row in the CSV and parse this level as the final level
        csv_output.append(CSVObject(match_level=level,
                            match_text=f"No matches found when parsing level {current_level}.", 
                            status=STATUS["ERROR"], 
                            parent=parent,
                            span=(-1,-1),
                            groups = {
                                "error": "No matches found when parsing level {current_level}."
                            }))
        return csv_output.append(parse_final_level(text=text, pattern=pattern, current_level=current_level, regex_pattern_dict=regex_pattern_dict, parent=parent))
    
    # Iterate through matches
    for idx, match in enumerate(matches):
        if idx == 0: 
        # for first match in level, Check for unmatched rows before target text and create rows for them if they exists
            if match.span()[0] > 0:
                unmatched_text = text[0:match.span()[0]]
                csv_output.append(CSVObject(
                    match_level=level,
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
        current_object = CSVObject(
            match_index=idx,
            match_level=current_level,
            match_text=match_text, 
            status=STATUS["PENDING"], 
            parent=parent, 
            span=match.span,
            groups=groups)

        # Extract child text and parse children at next heirarchical level
        if idx == len(matches)-1:
            child_text = text[match.span()[1]:]
        else:
            child_text = text[match.span()[1]:matches[idx+1].span()[0]]

        csv_output.append(parse_heirarchy(
            current_level=current_level+1,
            text=child_text,
            pattern=regex_pattern_dict[str(current_level+1)],
            regex_pattern_dict=regex_pattern_dict,
            parent=current_object,
        ))

    return csv_output

def show_matches():
# Show spans for each match highlighted with Parent data color coded according to parent level. All parent data of the same level should be highlighted the same color. If you run out of colors just start from the begging with the shading multiplied by some ratio within a span.Maybe lookup the name of the color for the legend. 
    pass

def main():
    # input, pattern, output, ignore_case = parse_inputs()

    # # Check existing column order from output file if it exists
    # if output is not None and os.path.exists(output):
    #     with open(output) as f:
    #         pass
    
    # Check multilevel hierarchy file if it exists
    pass

    # Level 1 extraction > For each in Level 1, extract level 2 elements, continue until all levels are extracted
    # If nothing is returned during child search, continue to next item. 
    # If nothing is returned for the whole hierarchical section, then print parents list and exit with an error message.  
    
    # Set Mock Data
    # input = "data/input_data/input.txt"
    input = "data/input_data/input_small.txt"
    
    # Read input file
    text = read_file(input)

    # groups, t = extract_groups(text=text, pattern=gym_log_regex["1"])
    # print(f"Groups: \n{groups}")
    # print(f"Remaining Text: \n{t}")
    rows = parse_heirarchy(text, pattern=gym_log_regex["1"], current_level=1, regex_pattern_dict=gym_log_regex)
    for i in rows: print(i)
    # # Write csv_output to output
    # if output is None:
    #     print("\n".join(csv_output))
    # else:
    #     with open(output, "w") as f:
    #         f.write("\n".join(csv_output))



    # Test pattern > extract section from text > Parse fields in section > write fields to csv_output

if __name__ == "__main__":
    main()