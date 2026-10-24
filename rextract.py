import argparse, os
import regex as re
import pandas as pd

'''
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
    "1": r"#(?P<DATE>\s?.+)(?:\n[^#]*)+", # Level 1 Regex for extracted dated sections
    "2": r"@\s*(?P<MUSCLE_GROUP>.+)\s*\n(?:[^@]*)+" # Level 2 
}

PROGRAM_NAME = "extract"
PROGRAM_DESCRIPTION = "Extract rows from text using a regex and write CSV."
PROGRAM_VERSION = "1.0"

IGNORE_FAILED_REGEX_MATCHES = False

def read_file(filepath):
    with open(filepath) as f:
        return f.read()

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

def search_regex(text, pattern, ignore_case=True):
    text = text.strip()
    regex = re.compile(pattern, re.IGNORECASE) if ignore_case else re.compile(pattern)
        
    match = regex.search(text)
    if not match: return match

    match_start = match.start() # starting index in text where match was found
    match_end = match.end()
    match_span = match.span() # span of indicies in text where match was found

    groups = match.groupdict() # dictionary of groups found (Label: Text)
    #group1 = match.group(1)
    #groupByName = match.group("group-name")

    return match

def extract_groups(text, pattern, ignore_case=True):
    '''Returns list of group dicts retrieved from each match found. 
    Dicts will have 'text' attribute included if there is text collected that doesnt match the pattern exactly
    One dict per distinct match found. Returns None if no match was found
    '''
    text = text.strip()
    regex = re.compile(pattern, re.IGNORECASE) if ignore_case else re.compile(pattern)
        
    matches = regex.finditer(text)
    if not matches: return None

    group_dicts = []
    for match in matches:
        if not match: return None

        match_start = match.start() # starting index in text where match was found
        match_end = match.end()
        match_span = match.span() # span of indicies in text where match was found

        groups = match.groupdict() # dictionary of groups found (Label: Text)
        #group1 = match.group(1)
        #groupByName = match.group("group-name")

        match_text = text[match_start:match_end]
        groups["text"] = match_text

        # Remove match from text
        text = (text[:match_start] + text[match_end:]).strip()

        group_dicts.append(groups)

        print(f"GROUPS: {match_text}\nTEXT: {text[:10]}")
    
    # Return list of dicts and remaining text
    return group_dicts, text

def parse_final_level(text:str, pattern:str, starting_level:int, regex_pattern_dict:dict, parent_csv_object:CSVObject):
# outputs list of csv objects ready to be converted into rows, extracted from text using regex provided
    csv_output = []
    '''
	So we are in the final level, we check our first match: 
		is there no matches, return null. On the layer above, check for a level to return null and output that as a list with an empty csv row item with Status Failed Matched and title “No Matches Found”
		Is there text data before it? Make that text a row with status Failed Match.
		Is there text between the end of the span and the next match / or the end of the text-if no addl matches exists? capture that as a row with status failed match
	take current match and parse groups for data. Leave data fields Null when not extracted successfully. 
	*All csv rows have the following fields populated
		- match_Index: <index in regex match list> 
		- match_level: <Hierarchical level>
		- csv_row_index: <row index in csv> 
		- status: <Matched, Failed, Parent>
		- parent: <Higher Level Parent id (level, match_Index)> 
		- parent_match_text: <pattern matched text retrieved from parent regex>
		- ancestor_dict: {
				<level>: (<Ancestor ID(level, match_index)>, <Ancestor Match Text>)
		- inherited_fields_dict: {		# Contains all fields extracted from ancestors, including most recent parent. Maintains group fields extracted from parents to be included in child object
				<group_name>:<Extracted_Value>} 
		- span: <start and end indicies in the text>
		- match_text: <regex text matching pattern> 
		* One of each of the group fields provided in regex pattern
		* One of each of the group fields extracted from each ancestor from each hierarchal level
    '''
    return csv_output

def parse_heirarchy(text:str, pattern:str, starting_level:int, regex_pattern_dict:dict, parent_csv_object): # Here recent parent is the CsvRow Object that contains all the data we need to populate the children. If we have to go a level deeper, then w
    # outputs list of csv Rows ready to be, irst validate that the starting_level value exists in the regex_pattern_dict. If not, Return a list containing a single Generic Error CSVRow Object describing the error. We are checking a generic level, We check our first match:
    csv_output = []
    '''
    is this the last level? 
		if so, run parse_final_level(level, pattern, text) and add result to the csv list
		If not, we simply want to flatten this level into the next, then extract that data as the rows, with this parent data as associated properties of that final level csv item. 

	check matches
		is there no matches, return Return a list containing a single Generic Error CSVRow Object describing the error. Status Failed Matched and title “No Matches Found”
	for match in matches: 
		For the first match, Is there text data before it? Make that text a row with status Failed Match.
		Is there text between the end of the span and the next match / or the end of the text-if no addl matches exists? capture that as a row with status failed match and append it to our csv_output
	take current match and parse groups for data. Leave data fields Null when not extracted successfully. Create CSVRow Object using the below fields
	*All csv rows have the following fields populated
		- match_Index: <index in regex match list> 
		- match_level: <Hierarchical level>
		- csv_row_index: <row index in csv>   # Include row index only if Row will not be flattened. If it will, then do not append it to the output csv
		- status: <Matched, Failed, Parent(to be unfolded)>
		- ancestor_dict: {
				<level>: (<Ancestor ID(level, match_index)>, <Ancestor Match Text>)}
		* One of each of the group fields provided in regex pattern, all of which need to be inherited by the child
		- inherited_fields_dict: {		# Contains all fields extracted from ancestors, including most recent parent. Maintains group fields extracted from parents to be included in child object
				<group_name>:<Extracted_Value>} 
		- parent: <Higher Level Parent id (level, match_Index)> 
		- parent_match_text: <pattern matched text retrieved from parent regex>
		- span: <start and end indicies in the text>
		- match_text: <regex text matching pattern>
		* IF Parent Item was provided as input, then use that to inform
	
	# If you encounter a critical error while extracting data, add a generic CSVRow Ojbect detailing the error to your csv_output, then return csv_output.	

	# Once data has been parsed and CSVRow Object has been created for Parent Item, we want to take what rows we get from parsing the match text at subsequent child levels and append those output rows to our current csv_output list. We will not append the parent object we just created, we will just pass it in as an input to the parse function. The output is what we are adding to our csv_output list
		with child_rows as parse_heirarchy(
				text=parent_csv_object.match_text, 
				pattern=regex_pattern_dict[str(starting_level+1)],
				starting_level=starting_level+1, 
				regex_pattern_dict=regex_pattern_dict, 
				parent_csv_object=parent_csv_object
			):
			# Check if there are any successful matches with status=Matched. If not, then there was an error in parsing or no results, so change this Parent item’s status to Matched, append it to the csv_output, then append the error rows retrieved from the parse call.
			# if there are matches, then we have successfully flattened this parent item and can add the parse output directly to the csv_output
			if matches_exists: 
				parent_csv_object.status = “Matched”
			csv_output += child_rows
    '''
    # return csv_output collection once all matches have processed all of their descendants in each lower level and appended them to the collection
    return csv_output

def show_matches():
# Show spans for each match highlighted with Parent data color coded according to parent level. All parent data of the same level should be highlighted the same color. If you run out of colors just start from the begging with the shading multiplied by some ratio within a span.Maybe lookup the name of the color for the legend. 
    pass

# class CSVObject():
#     ___init___(self):
#         pass


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

    groups, t = extract_groups(text=text, pattern=gym_log_regex["1"])
    print(f"Groups: \n{groups}")
    print(f"Remaining Text: \n{t}")
    
    # # Write csv_output to output
    # if output is None:
    #     print("\n".join(csv_output))
    # else:
    #     with open(output, "w") as f:
    #         f.write("\n".join(csv_output))



    # Test pattern > extract section from text > Parse fields in section > write fields to csv_output

if __name__ == "__main__":
    main()